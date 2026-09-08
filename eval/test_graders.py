#!/usr/bin/env python
"""Independent grader and harness regressions over synthetic repositories."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graders  # noqa: E402
import run  # noqa: E402

def _sh(args, cwd):
    subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _sha(cwd):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True).stdout.strip()


def _commit(cwd, msg):
    _sh(["git", "add", "-A"], cwd)
    _sh(["git", "-c", "user.email=t@local", "-c", "user.name=t", "commit", "-q", "--no-verify", "-m", msg], cwd)


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

def test_invocation_fails_on_nonzero_exit(sandbox):
    g = graders.grade({"id": "t", "agent": "main"}, ORG, [], sandbox(FILES), "", exit_code=1, timed_out=False)
    assert check(g, "invocation")["result"] == "fail"
    assert g["passed"] is False


def test_invocation_fails_on_timeout(sandbox):
    g = graders.grade({"id": "t", "agent": "main"}, ORG, [], sandbox(FILES), "", exit_code=0, timed_out=True)
    assert check(g, "invocation")["result"] == "fail"
    assert g["passed"] is False


# --- routing: acted owner vs the manifest's human label ---------------------------------------------

def test_routing_passes_when_owner_matches(sandbox):
    m = {"id": "t", "agent": "main", "expected_owner": ["a"]}
    g = graders.grade(m, ORG, ["region_a/new.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "routing")["result"] == "pass"
    assert g["passed"] is True


def test_routing_fails_on_foreign_owner(sandbox):
    m = {"id": "t", "agent": "main", "expected_owner": ["a"]}
    g = graders.grade(m, ORG, ["misc/thing.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "routing")["result"] == "fail"  # misc/* is owned by b, not a
    assert g["passed"] is False


def test_routing_fails_on_unowned_change(sandbox):
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

def test_paths_fail_inside_forbidden(sandbox):
    m = {"id": "t", "agent": "main", "allowed_paths": ["**"], "forbidden_paths": ["shared/**"]}
    g = graders.grade(m, ORG, ["shared/leak.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "paths")["result"] == "fail"
    assert g["passed"] is False


def test_paths_fail_outside_allowed(sandbox):
    m = {"id": "t", "agent": "main", "allowed_paths": ["region_a/**"]}
    g = graders.grade(m, ORG, ["misc/x.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "paths")["result"] == "fail"
    assert g["passed"] is False


# --- build: the deterministic outcome assertion -----------------------------------------------------

def test_build_fails_when_assertion_fails(sandbox):
    m = {"id": "t", "agent": "main",
         "build_cmd": "python -c \"import pathlib; assert pathlib.Path('made.txt').exists()\""}
    g = graders.grade(m, ORG, [], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "build")["result"] == "fail"
    assert g["passed"] is False


def test_build_passes_when_outcome_present(sandbox):
    m = {"id": "t", "agent": "main",
         "build_cmd": "python -c \"import pathlib; assert pathlib.Path('made.txt').read_text().strip() == 'ok'\""}
    g = graders.grade(m, ORG, [], sandbox({**FILES, "made.txt": "ok\n"}), "ok", exit_code=0, timed_out=False)
    assert check(g, "build")["result"] == "pass"
    assert g["passed"] is True


# --- the overall rule: passed == every applicable check green ---------------------------------------

def test_all_green_passes(sandbox):
    m = {"id": "t", "agent": "a", "expected_owner": ["a"], "allowed_paths": ["region_a/**"],
         "forbidden_paths": ["shared/**"]}
    g = graders.grade(m, ORG, ["region_a/new.txt"], sandbox(FILES), "done", exit_code=0, timed_out=False)
    assert g["passed"] is True, [c for c in g["checks"] if c["result"] != "pass"]
    assert check(g, "containment")["result"] == "pass"  # agent "a" only touched region_a/*


# --- containment applies to a leaf; a parent routing across its subtree is validated by routing/paths -

def test_containment_fails_for_leaf_touching_sibling(sandbox):
    m = {"id": "t", "agent": "a"}  # a is a leaf owning region_a/**
    g = graders.grade(m, ORG, ["misc/x.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "containment")["result"] == "fail"  # misc/* is owned by b, not a
    assert g["passed"] is False


def test_containment_skipped_for_parent(sandbox):
    m = {"id": "t", "agent": "root"}  # root is a Parent; it may route anywhere in its subtree
    g = graders.grade(m, ORG, ["region_a/x.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "containment") is None  # no single-owner containment check for a parent
    assert g["passed"] is True


# --- H3: attribution uses the immutable baseline org, not one the agent rewrote --------------------

def test_grades_against_baseline_not_rewritten_org(sandbox):
    # The agent touched a foreign path AND rewrote .github/agent-org/org.json to "own" everything;
    # attribution must still use the baseline ORG (a owns region_a only).
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

def test_broken_final_org_fails_coverage(sandbox):
    m = {"id": "t", "agent": "a"}
    g = graders.grade(m, ORG, [], sandbox(FILES), "ok", exit_code=0, timed_out=False, final_org=None)
    assert check(g, "coverage")["result"] == "fail"
    assert ".github/agent-org/org.json" in check(g, "coverage")["evidence"]
    assert g["passed"] is False


# --- H1: capture() sees committed + untracked + renames (not just working-tree status) -------------

def test_capture_sees_committed_change(sandbox):
    d = sandbox({"a.txt": "1"})
    base = _sha(d)
    baseline_files = run.snapshot(d)
    (d / "b.txt").write_text("2", encoding="utf-8")
    _commit(d, "add b")  # committed AFTER baseline — invisible to a bare `git status`
    cap = run.capture(d, base, baseline_files)
    assert "b.txt" in cap["changed_paths"], cap
    assert cap["new_commits"] == 1, cap


def test_capture_sees_untracked_and_rename(sandbox):
    d = sandbox({"orig.txt": "hello world payload"})
    base = _sha(d)
    baseline_files = run.snapshot(d)
    (d / "untracked.txt").write_text("u", encoding="utf-8")   # never staged
    _sh(["git", "mv", "orig.txt", "renamed.txt"], d)          # staged rename (identical content)
    cap = run.capture(d, base, baseline_files)
    assert "untracked.txt" in cap["changed_paths"], cap
    assert "renamed.txt" in cap["changed_paths"], cap         # rename destination present
    assert "orig.txt" in cap["changed_paths"], cap            # and its source side


# --- H7: an overlapped (multi-owner) changed path fails routing, never passes silently -------------

def test_routing_fails_on_overlap(sandbox):
    org2 = {"version": 1, "root": "root", "nodes": [
        {"id": "root", "charter": {"domain": ["shared/**"]}, "parent": None, "children": ["a", "b"], "mode": "Parent"},
        {"id": "a", "charter": {"domain": ["dup/**"]}, "parent": "root", "children": [], "mode": "Leaf"},
        {"id": "b", "charter": {"domain": ["dup/**"]}, "parent": "root", "children": [], "mode": "Leaf"}]}
    m = {"id": "t", "agent": "root", "expected_owner": ["a"]}
    g = graders.grade(m, org2, ["dup/x.txt"], sandbox({"shared/i": "x", "dup/x.txt": "y"}),
                      "ok", exit_code=0, timed_out=False)
    assert check(g, "routing")["result"] == "fail"  # dup/x.txt has two owners -> overlap


# --- H2: fail closed on a no-op via effect (required_paths / required_touched_owners / no_changes) --

def test_effect_fails_on_noop_when_change_required(sandbox):
    m = {"id": "t", "agent": "a", "required_paths": ["region_a/**"]}
    g = graders.grade(m, ORG, [], sandbox(FILES), "ok", exit_code=0, timed_out=False)  # nothing changed
    assert check(g, "effect")["result"] == "fail"
    assert g["passed"] is False


def test_effect_passes_when_required_path_changed(sandbox):
    m = {"id": "t", "agent": "a", "required_paths": ["region_a/**"]}
    g = graders.grade(m, ORG, ["region_a/new.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "effect")["result"] == "pass"


def test_effect_expected_no_changes_reject_case(sandbox):
    m = {"id": "t", "agent": "a", "expected_no_changes": True}
    ok = graders.grade(m, ORG, [], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(ok, "effect")["result"] == "pass"           # a reject/refuse case that changed nothing
    bad = graders.grade(m, ORG, ["region_a/x.txt"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(bad, "effect")["result"] == "fail"          # but it did change something


def test_effect_required_owner_missing_fails(sandbox):
    m = {"id": "t", "agent": "root", "required_touched_owners": ["a"]}
    g = graders.grade(m, ORG, ["shared/x"], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "effect")["result"] == "fail"            # touched root, not the required owner a


# --- H6: per-check rate denominator counts only runs where the check is present --------------------

def test_summarize_denominator_excludes_absent_checks():
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

def test_build_times_out_and_fails(sandbox):
    m = {"id": "t", "agent": "a", "build_cmd": "python -c \"import time; time.sleep(5)\"", "build_timeout": 1}
    g = graders.grade(m, ORG, [], sandbox(FILES), "ok", exit_code=0, timed_out=False)
    assert check(g, "build")["result"] == "fail"
    assert "timed out" in check(g, "build")["evidence"]


# --- H4: fixture preflight validates a fixture before spending agent runs --------------------------

_GOOD_ORG = {"version": 3, "root": "main", "nodes": [
    {"id": "main", "parent": None, "children": [], "mode": "Leaf",
     "charter": {"domain": ["**"], "concerns": [], "excludes": []}}]}


@pytest.fixture
def _fixture(file_tree):
    return lambda org, extra_seed=None, org_path=run.ORG_PATH.as_posix(): file_tree({
        f"seed/{org_path}": json.dumps(org),
        **{f"seed/{rel}": content for rel, content in (extra_seed or {}).items()},
    })


def test_preflight_accepts_valid_fixture(_fixture):
    assert run.preflight(_fixture(_GOOD_ORG), {"id": "t", "agent": "main"}) == []


def test_preflight_rejects_bad_schema_version(_fixture):
    probs = run.preflight(_fixture({**_GOOD_ORG, "version": 1}), {"id": "t", "agent": "main"})
    assert any("schema" in p for p in probs), probs


def test_preflight_rejects_missing_agent_def(_fixture):
    probs = run.preflight(_fixture(_GOOD_ORG), {"id": "t", "agent": "ghost"})
    assert any("ghost" in p for p in probs), probs


def test_preflight_rejects_baseline_coverage_gap(_fixture):
    org = {"version": 3, "root": "main", "nodes": [
        {"id": "main", "parent": None, "children": [], "mode": "Leaf",
         "charter": {"domain": ["sub/**"], "concerns": [], "excludes": []}}]}  # owns sub/** only
    probs = run.preflight(_fixture(org, {"orphan.txt": "x"}), {"id": "t", "agent": "main"})
    assert any("coverage" in p for p in probs), probs


@pytest.mark.parametrize("canonical_present", [False, True], ids=["legacy-only", "conflicting-live-files"])
def test_preflight_does_not_implicitly_migrate_legacy_fixtures(_fixture, canonical_present):
    # Intentionally retain a root config to model legacy setup, not a second installed organization.
    extra = {run.ORG_PATH.as_posix(): json.dumps(_GOOD_ORG)} if canonical_present else {}
    fixture = _fixture(_GOOD_ORG, extra, org_path="org.json")
    legacy = fixture / "seed" / "org.json"
    before = legacy.read_bytes()
    problems = run.preflight(fixture, {"id": "legacy-layout", "agent": "main"})
    assert any("legacy seed/org.json" in problem for problem in problems), problems
    assert legacy.read_bytes() == before
    assert (fixture / "seed" / run.ORG_PATH).exists() is canonical_present


@pytest.mark.parametrize(
    "fixture",
    sorted(path.parent for path in (Path(__file__).parent / "fixtures").glob("*/manifest.yml")),
    ids=lambda fixture: fixture.name,
)
def test_repository_fixtures_use_canonical_installed_layout(fixture):
    seed = fixture / "seed"
    assert seed.is_dir()  # Fixture setup trees are not obsolete installed seed copies.
    assert (seed / run.ORG_PATH).is_file()
    assert not (seed / "org.json").exists()
    assert not (seed / ".github" / "agent-org" / "seed").exists()
    org = run._fixture_org(fixture)
    canonical = run.ORG_PATH.as_posix()
    if run.ov.managed(org, canonical):
        assert run.ov.owners_of(run.ov.compile_nodes(org["nodes"]), canonical) == [org["root"]]
    else:
        assert org["scope"] == ["managed/**"]  # Preserve the partial fixture's unmanaged config.
    manifest = yaml.safe_load((fixture / "manifest.yml").read_text(encoding="utf-8"))
    assert run.preflight(fixture, manifest) == []


def test_sandbox_assembly_uses_only_independent_fixture_org(_fixture, tmp_path):
    org = {
        "version": 3, "root": "archivist", "collaboration": "hybrid",
        "scope": ["records/**", ".github/**", "wiki/**"], "storage": "tracked",
        "nodes": [
            {"id": "archivist", "parent": None, "children": [], "mode": "Leaf",
             "charter": {"domain": ["records/**", ".github/**", "wiki/**"],
                         "concerns": ["independent archive fixture"], "excludes": []}},
        ],
    }
    artifact = "wiki/archivist/layout.md"
    fixture = _fixture(org, {
        "records/index.md": "archive",
        artifact: (
            "---\nowner: archivist\n"
            "sources: [.github/agent-org/org.json, records/index.md]\n---\nArchive layout.\n"
        ),
    })
    sandbox = tmp_path / "assembled"
    sandbox.mkdir()
    assert run.build_sandbox(fixture, sandbox)
    assert run._read_org(sandbox) == org
    assert not (sandbox / "org.json").exists()
    assert not (sandbox / ".github" / "agent-org" / "seed").exists()
    assert sorted(file.name for file in (sandbox / ".github" / "agents").glob("*.md")) == [
        "archivist.md", "splitter.md",
    ]
    assert (sandbox / ".github/instructions/agent-org.scope-partial.instructions.md").is_file()
    assert (sandbox / ".github/instructions/agent-org.collaboration-hybrid.instructions.md").is_file()
    assert run.ov.validate(org, run.ov.git_tracked(sandbox))["status"] == "ok"
    assert run.bv.check_bundle(org, sandbox)["status"] == "ok"
    stale = run.bv.check_freshness(sandbox, [run.ORG_PATH.as_posix()])
    assert [violation["path"] for violation in stale["violations"]] == [artifact]
    assert run.bv.check_freshness(sandbox, [run.ORG_PATH.as_posix(), artifact])["status"] == "ok"


# --- host-mode invocation omits --agent so the Host resolves the fixture's configured root ---------

def test_host_mode_omits_agent_flag():
    usage = Path(tempfile.gettempdir()) / "u.json"
    host_cmd = run.build_invoke_cmd({"agent": "host", "intent": "x"}, Path("/sb"), None, None, usage)
    assert "--agent" not in host_cmd, host_cmd
    node_cmd = run.build_invoke_cmd({"agent": "catalog", "intent": "x"}, Path("/sb"), None, None, usage)
    assert node_cmd[node_cmd.index("--agent") + 1] == "catalog", node_cmd


def test_partial_unmanaged_paths_do_not_fail_routing(sandbox):
    partial = {**ORG, "scope": ["region_a/**"]}
    directory = sandbox(FILES)
    result = graders.grade(
        {"agent": "a", "expected_owner": ["a"]}, partial, ["human-area/new.txt"], directory,
    )
    assert check(result, "routing")["result"] == "pass"
    assert check(result, "containment")["result"] == "pass"


def test_scope_cannot_be_rewritten_to_hide_failed_coverage(sandbox):
    altered = {**ORG, "scope": ["nonexistent/**"]}
    result = graders.grade({"agent": "a"}, ORG, [run.ORG_PATH.as_posix()], sandbox(FILES), final_org=altered)
    assert check(result, "configuration")["result"] == "fail"


def test_capture_does_not_trust_agent_modified_ignore_rules(sandbox):
    directory = sandbox({"required.txt": "before"})
    baseline = run.snapshot(directory)
    commit = _sha(directory)
    (directory / "foreign").mkdir()
    (directory / "foreign" / "extra.txt").write_text("hidden change", encoding="utf-8")
    (directory / ".gitignore").write_text("foreign/\n", encoding="utf-8")
    (directory / "required.txt").write_text("after", encoding="utf-8")
    result = run.capture(directory, commit, baseline)
    assert "foreign/extra.txt" in result["changed_paths"]


def test_foreign_log_requires_correct_actor_run_and_completed_phase(sandbox):
    directory = sandbox(FILES)
    manifest = {"agent": "a", "expected_foreign": [{"path": "misc/new.txt", "owner": "b", "acting": "a"}]}
    log = directory / ".git" / "agent-org" / "foreign" / "this-run" / "child.jsonl"
    log.parent.mkdir(parents=True)
    entry = {
        "acting": "b", "disposition": "warn", "owner": "b", "path": "misc/new.txt",
        "phase": "completed", "runId": "this-run", "sessionId": "child",
    }
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    result = graders.grade(manifest, ORG, ["misc/new.txt"], directory, run_id="this-run")
    assert check(result, "foreign_log")["result"] == "fail"
    entry["acting"] = "a"
    entry["phase"] = "attempted"
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    result = graders.grade(manifest, ORG, ["misc/new.txt"], directory, run_id="this-run")
    assert check(result, "foreign_log")["result"] == "fail"
    entry["phase"] = "completed"
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    result = graders.grade(manifest, ORG, ["misc/new.txt"], directory, run_id="this-run")
    assert check(result, "foreign_log")["result"] == "pass"
    result = graders.grade(manifest, ORG, ["misc/new.txt"], directory, run_id="another-run")
    assert check(result, "foreign_log")["result"] == "fail"


def test_expected_warning_does_not_disable_other_containment_checks(sandbox):
    manifest = {"agent": "a", "expected_foreign": [{"path": "misc/allowed.txt", "owner": "b", "acting": "a"}]}
    result = graders.grade(manifest, ORG, ["misc/unexpected.txt"], sandbox(FILES))
    assert check(result, "containment")["result"] == "fail"


def test_denial_fixture_requires_a_logged_attempt(sandbox):
    directory = sandbox(FILES)
    manifest = {
        "agent": "root", "expected_no_changes": True,
        "expected_denied": [{"path": "region_a/blocked.txt", "owner": "a", "acting": "root"}],
    }
    result = graders.grade(manifest, ORG, [], directory, run_id="run-one")
    assert check(result, "denied_writes")["result"] == "fail"
    log = directory / ".git" / "agent-org" / "foreign" / "run-one" / "parent.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({
        "acting": "root", "disposition": "deny", "owner": "a", "path": "region_a/blocked.txt",
        "phase": "attempted", "runId": "run-one", "sessionId": "parent",
    }) + "\n", encoding="utf-8")
    result = graders.grade(manifest, ORG, [], directory, run_id="run-one")
    assert check(result, "denied_writes")["result"] == "pass"


def test_role_promotion_requires_updating_loop_reference(tmp_path):
    current = {"nodes": [
        {"id": "director", "mode": "Parent"},
        {"id": "ledger", "mode": "Leaf"},
        {"id": "stock", "mode": "Leaf"},
    ]}
    agents = tmp_path / ".github" / "agents"
    loops = tmp_path / ".github" / "agent-org" / "loops"
    agents.mkdir(parents=True)
    loops.mkdir(parents=True)
    for role in ("leaf", "parent"):
        (loops / f"{role}.md").write_text(role, encoding="utf-8")
    for node in current["nodes"]:
        (agents / f"{node['id']}.md").write_text(
            f"---\nname: {node['id']}\n---\nloop: .github/agent-org/loops/leaf.md\n", encoding="utf-8",
        )
    assert graders.bv.check_agent_roles(current, tmp_path) == [
        "director must reference .github/agent-org/loops/parent.md"
    ]
    (agents / "director.md").write_text(
        "---\nname: director\n---\nloop: .github/agent-org/loops/parent.md\n", encoding="utf-8",
    )
    assert graders.bv.check_agent_roles(current, tmp_path) == []


@pytest.mark.parametrize("role", ["Leaf", "Parent"])
@pytest.mark.parametrize("form", [
    "body", "both", "duplicate-body", "duplicate-empty", "empty", "frontmatter-only", "missing", "self-reference",
    "wrong-role",
])
def test_role_grader_requires_one_body_reference(file_tree, role, form):
    expected = f".github\\agent-org\\loops\\{role.lower()}.md"
    header = "name: entry\n"
    if form in {"both", "frontmatter-only"}:
        header += f"loop: {expected}\n"
    body = f"Read and follow this operating file:\nloop: {expected}\n"
    if form == "duplicate-body":
        body += f"loop: {expected}\n"
    elif form == "duplicate-empty":
        body += "loop:\n"
    elif form == "empty":
        body = "loop:\n"
    elif form in {"frontmatter-only", "missing"}:
        body = ""
    elif form == "self-reference":
        body = "loop: .github\\agents\\entry.md\n"
    elif form == "wrong-role":
        other = "parent" if role == "Leaf" else "leaf"
        body = f"loop: .github\\agent-org\\loops\\{other}.md\n"
    root = file_tree({
        expected.replace("\\", "/"): role,
        ".github/agents/entry.md": f"---\n{header}---\n\n{body}",
    })
    org = {"nodes": [{"id": "entry", "mode": role}]}
    problems = graders.bv.check_agent_roles(org, root)
    assert bool(problems) is (form != "body"), problems


def test_role_grader_requires_the_referenced_file(file_tree):
    root = file_tree({
        ".github/agents/entry.md": "---\nname: entry\n---\nloop: .github\\agent-org\\loops\\leaf.md\n",
    })
    assert graders.bv.check_agent_roles({"nodes": [{"id": "entry", "mode": "Leaf"}]}, root) == [
        "missing loop file for entry"
    ]


def test_runner_reuses_one_workspace_for_root_and_children(tmp_path, monkeypatch):
    fixture = tmp_path / "fixture"
    seed = fixture / "seed"
    seed.mkdir(parents=True)
    org = {"version": 3, "root": "director", "nodes": [
        {"id": "director", "parent": None, "children": ["inbound", "outbound"], "mode": "Parent",
         "charter": {"domain": [".github/**", ".gitignore", "/.github/agent-org/org.json"],
                     "concerns": [], "excludes": []}},
        {"id": "inbound", "parent": "director", "children": [], "mode": "Leaf",
         "charter": {"domain": ["inbound/**"], "concerns": [], "excludes": []}},
        {"id": "outbound", "parent": "director", "children": [], "mode": "Leaf",
         "charter": {"domain": ["outbound/**"], "concerns": [], "excludes": []}},
    ]}
    (seed / run.ORG_PATH).parent.mkdir(parents=True)
    (seed / run.ORG_PATH).write_text(json.dumps(org), encoding="utf-8")
    manifest = {
        "agent": "director", "check_role_refs": True, "id": "shared-driver", "intent": "Plan and dispatch",
        "expected_owner": ["inbound", "outbound"], "required_touched_owners": ["inbound", "outbound"],
    }
    (fixture / "manifest.yml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    observed = {}

    def invoke(_manifest, workspace, env, _model, _effort, _timeout, acting, run_id):
        assert acting == "director"
        assert "AGENT_ORG_ACTING" not in env
        assert (workspace / ".git").is_file()
        assert run._read_org(workspace) == org
        assert not (workspace / "org.json").exists()
        assert not (workspace / ".github" / "agent-org" / "seed").exists()
        assert not (workspace / ".github" / "agents" / "main.md").exists()
        assert not (workspace / ".github" / "agent-org" / "tools" / "bundle_validator.py").exists()
        observed["workspace"] = workspace
        run.ov.record_acting({
            "cwd": str(workspace), "sessionId": "parent",
            "prompt": f"AgentOrgActingNode: director\nAgentOrgRunId: {run_id}\nPlan",
        })
        for child in ("inbound", "outbound"):
            dispatched = run.ov.process_hook({
                "cwd": str(workspace), "sessionId": "parent", "toolName": "task",
                "toolArgs": {"agent_type": child, "prompt": "Write your result"},
            })["modifiedArgs"]["prompt"]
            run.ov.record_acting({"cwd": str(workspace), "sessionId": child, "prompt": dispatched})
            reused = run.worktree.create(workspace)
            assert reused["session_id"] == run_id and Path(reused["path"]) == workspace
            file = workspace / child / "result.txt"
            file.parent.mkdir(exist_ok=True)
            call = {"cwd": str(workspace), "sessionId": child, "toolName": "create",
                    "toolArgs": {"path": str(file)}}
            assert run.ov.process_hook(call)["permissionDecision"] == "allow"
            file.write_text(child, encoding="utf-8")
        return {
            "response": "completed", "exit": 0, "timed_out": False, "duration_s": 1,
            "usage": {}, "trajectory": [], "infra_error": None,
        }

    monkeypatch.setattr(run, "invoke", invoke)
    monkeypatch.setattr(run, "copilot_env", lambda: {})
    result = run.run_case(fixture, 1, None, None, False, judge=False)
    assert not result.get("fixture_error"), result
    assert result["runs"][0]["grade"]["passed"], result["runs"][0]["grade"]
    assert result["runs"][0]["changed_paths"] == ["inbound/result.txt", "outbound/result.txt"]
    assert not observed["workspace"].exists()


@pytest.mark.parametrize("final_state", ["missing", "malformed", "rewritten", "conflicting"])
def test_runner_grades_only_unambiguous_canonical_final_org(_fixture, monkeypatch, final_state):
    fixture = _fixture(_GOOD_ORG)
    manifest = {"id": "canonical-final", "agent": "main", "intent": "Exercise final organization grading"}
    (fixture / "manifest.yml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    observed = {}

    def invoke(_manifest, workspace, _env, _model, _effort, _timeout, acting, _run_id):
        assert acting == "main"
        assert run._read_org(workspace) == _GOOD_ORG
        observed["workspace"] = workspace
        # Intentional legacy decoy: a valid root file must not mask a broken or ambiguous live config.
        if final_state != "rewritten":
            (workspace / "org.json").write_text(json.dumps(_GOOD_ORG), encoding="utf-8")
        canonical = workspace / run.ORG_PATH
        if final_state == "missing":
            canonical.unlink()
        elif final_state == "malformed":
            canonical.write_text("{invalid", encoding="utf-8")
        elif final_state == "rewritten":
            canonical.write_text(json.dumps({**_GOOD_ORG, "scope": ["nonexistent/**"]}), encoding="utf-8")
        return {
            "response": "finished", "exit": 0, "timed_out": False, "duration_s": 1,
            "usage": {}, "trajectory": [], "infra_error": None,
        }

    monkeypatch.setattr(run, "invoke", invoke)
    monkeypatch.setattr(run, "copilot_env", lambda: {})
    result = run.run_case(fixture, 1, None, None, False, judge=False)
    assert not result.get("fixture_error"), result
    captured = result["runs"][0]
    assert captured["grade"]["passed"] is False
    expected_check = "configuration" if final_state == "rewritten" else "coverage"
    assert check(captured["grade"], expected_check)["result"] == "fail"
    assert (run.ORG_PATH.as_posix() in captured["changed_paths"]) is (final_state != "conflicting")
    assert ("org.json" in captured["changed_paths"]) is (final_state != "rewritten")
    assert not observed["workspace"].exists()
