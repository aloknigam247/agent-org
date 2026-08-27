#!/usr/bin/env python
"""Fresh self-tests for the eval graders (grade()): verdict logic over synthetic sandboxes.

Independent of the design examples and of eval/fixtures. Each case builds a tiny git sandbox and a
tree-valid three-node org (a retained parent + two leaves) so coverage stays clean, then drives one
verdict at a time. In particular this locks in the invocation-health and build-outcome gates, so a
crashed or no-op run can never score a vacuous pass.

Run: ``python eval/test_graders.py`` — prints a one-line summary and exits non-zero if any case fails.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graders  # noqa: E402
import run  # noqa: E402

CASES = []
_DIRS = []


def case(fn):
    CASES.append(fn)
    return fn


def _sh(args, cwd):
    subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _sha(cwd):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True).stdout.strip()


def _commit(cwd, msg):
    _sh(["git", "add", "-A"], cwd)
    _sh(["git", "-c", "user.email=t@local", "-c", "user.name=t", "commit", "-q", "--no-verify", "-m", msg], cwd)


def sandbox(files):
    """A hermetic git repo seeded with ``files`` (rel-path -> content), committed as the baseline."""
    d = Path(tempfile.mkdtemp(prefix="gtest-"))
    _DIRS.append(d)
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _sh(["git", "init", "-q", "-b", "main"], d)
    _sh(["git", "config", "core.autocrlf", "false"], d)
    _sh(["git", "config", "core.hooksPath", str(d / ".git" / "hooks")], d)
    _sh(["git", "add", "-A"], d)
    _sh(["git", "-c", "user.email=t@local", "-c", "user.name=t", "commit", "-q", "--no-verify", "-m", "seed"], d)
    return d


# a retained parent (shared set) + two leaves; disjoint and total, so coverage is clean
ORG = {"version": 1, "root": "root", "nodes": [
    {"id": "root", "charter": {"domain": ["shared/**"]}, "parent": None, "children": ["a", "b"], "mode": "Parent"},
    {"id": "a", "charter": {"domain": ["region_a/**"]}, "parent": "root", "children": [], "mode": "Leaf"},
    {"id": "b", "charter": {"domain": ["**"], "excludes": ["region_a/**", "shared/**"]},
     "parent": "root", "children": [], "mode": "Leaf"},
]}

FILES = {"shared/index.md": "x", "region_a/keep.txt": "x", "misc/readme.txt": "x"}


def check(g, name):
    return next((c for c in g["checks"] if c["check"] == name), None)


# --- invocation health: a crashed or timed-out run never passes -------------------------------------

@case
def invocation_fails_on_nonzero_exit():
    g = graders.grade({"id": "t", "agent": "main"}, ORG, [], sandbox(FILES), "", exit_code=1, timed_out=False)
    assert check(g, "invocation")["result"] == "fail"
    assert g["passed"] is False


@case
def invocation_fails_on_timeout():
    g = graders.grade({"id": "t", "agent": "main"}, ORG, [], sandbox(FILES), "", exit_code=0, timed_out=True)
    assert check(g, "invocation")["result"] == "fail"
    assert g["passed"] is False


# --- routing: acted owner vs the manifest's human label ---------------------------------------------

@case
def routing_passes_when_owner_matches():
    m = {"id": "t", "agent": "main", "expected_owner": ["a"]}
    g = graders.grade(m, ORG, ["region_a/new.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "routing")["result"] == "pass"
    assert g["passed"] is True


@case
def routing_fails_on_foreign_owner():
    m = {"id": "t", "agent": "main", "expected_owner": ["a"]}
    g = graders.grade(m, ORG, ["misc/thing.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "routing")["result"] == "fail"  # misc/* is owned by b, not a
    assert g["passed"] is False


@case
def routing_fails_on_unowned_change():
    org2 = {"version": 1, "root": "root", "nodes": [
        {"id": "root", "charter": {"domain": ["shared/**"]}, "parent": None, "children": ["a", "c"], "mode": "Parent"},
        {"id": "a", "charter": {"domain": ["region_a/**"]}, "parent": "root", "children": [], "mode": "Leaf"},
        {"id": "c", "charter": {"domain": ["region_c/**"]}, "parent": "root", "children": [], "mode": "Leaf"},
    ]}
    sb = sandbox({"shared/i.md": "x", "region_a/k.txt": "x", "region_c/k.txt": "x"})
    m = {"id": "t", "agent": "main", "expected_owner": ["a"]}
    g = graders.grade(m, org2, ["nowhere/x.txt"], sb, "ok", exit_code=0, timed_out=False)
    assert check(g, "routing")["result"] == "fail"  # nowhere/* is owned by no node
    assert g["passed"] is False


# --- paths: changes stay inside allowed regions and out of forbidden ones ---------------------------

@case
def paths_fail_inside_forbidden():
    m = {"id": "t", "agent": "main", "allowed_paths": ["**"], "forbidden_paths": ["shared/**"]}
    g = graders.grade(m, ORG, ["shared/leak.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "paths")["result"] == "fail"
    assert g["passed"] is False


@case
def paths_fail_outside_allowed():
    m = {"id": "t", "agent": "main", "allowed_paths": ["region_a/**"]}
    g = graders.grade(m, ORG, ["misc/x.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "paths")["result"] == "fail"
    assert g["passed"] is False


# --- build: the deterministic outcome assertion -----------------------------------------------------

@case
def build_fails_when_assertion_fails():
    m = {"id": "t", "agent": "main",
         "build_cmd": "python -c \"import pathlib; assert pathlib.Path('made.txt').exists()\""}
    g = graders.grade(m, ORG, [], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "build")["result"] == "fail"
    assert g["passed"] is False


@case
def build_passes_when_outcome_present():
    m = {"id": "t", "agent": "main",
         "build_cmd": "python -c \"import pathlib; assert pathlib.Path('made.txt').read_text().strip() == 'ok'\""}
    g = graders.grade(m, ORG, [], sandbox({**FILES, "made.txt": "ok\n"}), "ok", exit_code=0, timed_out=False)
    assert check(g, "build")["result"] == "pass"
    assert g["passed"] is True


# --- the overall rule: passed == every applicable check green ---------------------------------------

@case
def all_green_passes():
    m = {"id": "t", "agent": "a", "expected_owner": ["a"], "allowed_paths": ["region_a/**"],
         "forbidden_paths": ["shared/**"]}
    g = graders.grade(m, ORG, ["region_a/new.txt"], sandbox(FILES), "done", exit_code=0, timed_out=False)
    assert g["passed"] is True, [c for c in g["checks"] if c["result"] != "pass"]
    assert check(g, "containment")["result"] == "pass"  # agent "a" only touched region_a/*


# --- containment applies to a leaf; a parent routing across its subtree is validated by routing/paths -

@case
def containment_fails_for_leaf_touching_sibling():
    m = {"id": "t", "agent": "a"}  # a is a leaf owning region_a/**
    g = graders.grade(m, ORG, ["misc/x.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "containment")["result"] == "fail"  # misc/* is owned by b, not a
    assert g["passed"] is False


@case
def containment_skipped_for_parent():
    m = {"id": "t", "agent": "root"}  # root is a Parent; it may route anywhere in its subtree
    g = graders.grade(m, ORG, ["region_a/x.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "containment") is None  # no single-owner containment check for a parent
    assert g["passed"] is True


# --- H3: attribution uses the immutable baseline org, not one the agent rewrote --------------------

@case
def grades_against_baseline_not_rewritten_org():
    # the agent touched a foreign path AND rewrote org.json to "own" everything; attribution must
    # still use the baseline ORG (a owns region_a only), so routing and containment fail.
    rewritten = {"version": 1, "root": "a",
                 "nodes": [{"id": "a", "charter": {"domain": ["**"]}, "parent": None,
                            "children": [], "mode": "Leaf"}]}
    m = {"id": "t", "agent": "a", "expected_owner": ["a"]}
    g = graders.grade(m, ORG, ["misc/x.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False,
                      final_org=rewritten)
    assert check(g, "routing")["result"] == "fail"       # misc/* -> b under baseline, not a
    assert check(g, "containment")["result"] == "fail"
    assert check(g, "coverage")["result"] == "pass"      # coverage validated on the (valid) final org
    assert g["passed"] is False


# --- H9: a missing/unparseable final org fails coverage, never crashes -----------------------------

@case
def broken_final_org_fails_coverage():
    m = {"id": "t", "agent": "a"}
    g = graders.grade(m, ORG, [], sandbox(FILES), "ok", exit_code=0, timed_out=False, final_org=None)
    assert check(g, "coverage")["result"] == "fail"
    assert g["passed"] is False


# --- H1: capture() sees committed + untracked + renames (not just working-tree status) -------------

@case
def capture_sees_committed_change():
    d = sandbox({"a.txt": "1"})
    base = _sha(d)
    (d / "b.txt").write_text("2", encoding="utf-8")
    _commit(d, "add b")  # committed AFTER baseline — invisible to a bare `git status`
    cap = run.capture(d, base)
    assert "b.txt" in cap["changed_paths"], cap
    assert cap["new_commits"] == 1, cap


@case
def capture_sees_untracked_and_rename():
    d = sandbox({"orig.txt": "hello world payload"})
    base = _sha(d)
    (d / "untracked.txt").write_text("u", encoding="utf-8")   # never staged
    _sh(["git", "mv", "orig.txt", "renamed.txt"], d)          # staged rename (identical content)
    cap = run.capture(d, base)
    assert "untracked.txt" in cap["changed_paths"], cap
    assert "renamed.txt" in cap["changed_paths"], cap         # rename destination present
    assert "orig.txt" in cap["changed_paths"], cap            # and its source side


# --- H7: an overlapped (multi-owner) changed path fails routing, never passes silently -------------

@case
def routing_fails_on_overlap():
    org2 = {"version": 1, "root": "root", "nodes": [
        {"id": "root", "charter": {"domain": ["shared/**"]}, "parent": None, "children": ["a", "b"], "mode": "Parent"},
        {"id": "a", "charter": {"domain": ["dup/**"]}, "parent": "root", "children": [], "mode": "Leaf"},
        {"id": "b", "charter": {"domain": ["dup/**"]}, "parent": "root", "children": [], "mode": "Leaf"}]}
    m = {"id": "t", "agent": "root", "expected_owner": ["a"]}
    g = graders.grade(m, org2, ["dup/x.txt"], sandbox({"shared/i": "x", "dup/x.txt": "y"}),
                      "ok", exit_code=0, timed_out=False)
    assert check(g, "routing")["result"] == "fail"  # dup/x.txt has two owners -> overlap


# --- H2: fail closed on a no-op via effect (required_paths / required_touched_owners / no_changes) --

@case
def effect_fails_on_noop_when_change_required():
    m = {"id": "t", "agent": "a", "required_paths": ["region_a/**"]}
    g = graders.grade(m, ORG, [], sandbox(FILES), "ok", exit_code=0, timed_out=False)  # nothing changed
    assert check(g, "effect")["result"] == "fail"
    assert g["passed"] is False


@case
def effect_passes_when_required_path_changed():
    m = {"id": "t", "agent": "a", "required_paths": ["region_a/**"]}
    g = graders.grade(m, ORG, ["region_a/new.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "effect")["result"] == "pass"


@case
def effect_expected_no_changes_reject_case():
    m = {"id": "t", "agent": "a", "expected_no_changes": True}
    ok = graders.grade(m, ORG, [], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(ok, "effect")["result"] == "pass"           # a reject/refuse case that changed nothing
    bad = graders.grade(m, ORG, ["region_a/x.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(bad, "effect")["result"] == "fail"          # but it did change something


@case
def effect_required_owner_missing_fails():
    m = {"id": "t", "agent": "root", "required_touched_owners": ["a"]}
    g = graders.grade(m, ORG, ["shared/x"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "effect")["result"] == "fail"            # touched root, not the required owner a


# --- H6: per-check rate denominator counts only runs where the check is present --------------------

@case
def summarize_denominator_excludes_absent_checks():
    def mk(checks, passed):
        return {"grade": {"passed": passed, "checks": checks},
                "usage": {"totalPremiumRequestCost": 0.1}, "duration_s": 10,
                "trajectory_analysis": {"tool_calls": 1, "delegated_to": [], "ran_oracle": False,
                                        "committed": False, "oracle_before_commit": True, "looped_tools": []}}
    r1 = mk([{"check": "invocation", "result": "pass", "evidence": ""},
             {"check": "containment", "result": "fail", "evidence": "x"}], False)  # containment present & fails
    r2 = mk([{"check": "invocation", "result": "pass", "evidence": ""}], True)     # containment absent
    s = run.summarize([r1, r2])
    # present in 1 run, failed in 1 -> 0.0 (old buggy logic diluted to (2-1)/2 = 0.5)
    assert s["per_check_pass_rate"]["containment"] == 0.0, s["per_check_pass_rate"]
    assert s["per_check_pass_rate"]["invocation"] == 1.0


# --- H5: a build_cmd that overruns build_timeout fails (never stalls the run) ----------------------

@case
def build_times_out_and_fails():
    m = {"id": "t", "agent": "a", "build_cmd": "python -c \"import time; time.sleep(5)\"", "build_timeout": 1}
    g = graders.grade(m, ORG, [], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "build")["result"] == "fail"
    assert "timed out" in check(g, "build")["evidence"]


# --- H4: fixture preflight validates a fixture before spending agent runs --------------------------

_GOOD_ORG = {"version": 3, "root": "main", "nodes": [
    {"id": "main", "parent": None, "children": [], "mode": "Leaf",
     "charter": {"domain": ["**"], "concerns": [], "excludes": []}}]}


def _fixture(org, extra_seed=None):
    d = Path(tempfile.mkdtemp(prefix="fx-"))
    _DIRS.append(d)
    seed = d / "seed"
    seed.mkdir()
    (seed / "org.json").write_text(json.dumps(org), encoding="utf-8")
    for rel, content in (extra_seed or {}).items():
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


@case
def preflight_accepts_valid_fixture():
    assert run.preflight(_fixture(_GOOD_ORG), {"id": "t", "agent": "main"}) == []


@case
def preflight_rejects_bad_schema_version():
    probs = run.preflight(_fixture({**_GOOD_ORG, "version": 1}), {"id": "t", "agent": "main"})
    assert any("schema" in p for p in probs), probs


@case
def preflight_rejects_missing_agent_def():
    probs = run.preflight(_fixture(_GOOD_ORG), {"id": "t", "agent": "ghost"})
    assert any("ghost" in p for p in probs), probs


@case
def preflight_rejects_baseline_coverage_gap():
    org = {"version": 3, "root": "main", "nodes": [
        {"id": "main", "parent": None, "children": [], "mode": "Leaf",
         "charter": {"domain": ["sub/**"], "concerns": [], "excludes": []}}]}  # owns sub/** only
    probs = run.preflight(_fixture(org, {"orphan.txt": "x"}), {"id": "t", "agent": "main"})
    assert any("coverage" in p for p in probs), probs


# --- host-mode invocation omits --agent so the Host performs the hardcoded entry to main -----------

@case
def host_mode_omits_agent_flag():
    usage = Path(tempfile.gettempdir()) / "u.json"
    host_cmd = run.build_invoke_cmd({"agent": "host", "intent": "x"}, Path("/sb"), None, None, usage)
    assert "--agent" not in host_cmd, host_cmd
    node_cmd = run.build_invoke_cmd({"agent": "catalog", "intent": "x"}, Path("/sb"), None, None, usage)
    assert node_cmd[node_cmd.index("--agent") + 1] == "catalog", node_cmd


def _main():
    failed = 0
    try:
        for fn in CASES:
            try:
                fn()
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {fn.__name__}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    finally:
        for d in _DIRS:
            shutil.rmtree(d, ignore_errors=True)
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
