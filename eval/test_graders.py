#!/usr/bin/env python
"""Fresh self-tests for the eval graders (grade()): verdict logic over synthetic sandboxes.

Independent of the design examples and of eval/fixtures. Each case builds a tiny git sandbox and a
tree-valid three-node org (a retained parent + two leaves) so coverage stays clean, then drives one
verdict at a time. In particular this locks in the invocation-health and build-outcome gates, so a
crashed or no-op run can never score a vacuous pass.

Run: ``python eval/test_graders.py`` — prints a one-line summary and exits non-zero if any case fails.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graders  # noqa: E402

CASES = []
_DIRS = []


def case(fn):
    CASES.append(fn)
    return fn


def _sh(args, cwd):
    subprocess.run(args, cwd=cwd, capture_output=True, text=True)


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


def run():
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
    sys.exit(run())
