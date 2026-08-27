#!/usr/bin/env python
"""Self-tests for the worktree lifecycle substrate (§2.5): isolation + serialized integration.

Deterministic — the tests supply the "work" directly (no agent). Run:
``python .github/tools/test_worktree.py`` — one-line summary, non-zero exit on failure.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import worktree as wt  # noqa: E402

CASES = []
_DIRS = []


def case(fn):
    CASES.append(fn)
    return fn


def repo():
    d = Path(tempfile.mkdtemp(prefix="wtt-"))
    _DIRS.append(d)
    org = {"version": 3, "root": "root", "nodes": [
        {"id": "root", "charter": {"domain": ["shared/**"]}, "parent": None, "children": ["a", "b"], "mode": "Parent"},
        {"id": "a", "charter": {"domain": ["a/**"]}, "parent": "root", "children": [], "mode": "Leaf"},
        {"id": "b", "charter": {"domain": ["b/**"]}, "parent": "root", "children": [], "mode": "Leaf"}]}
    (d / "org.json").write_text(json.dumps(org), encoding="utf-8")
    for f in ("a/keep.txt", "b/keep.txt", "shared/keep.txt"):
        p = d / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["config", "core.autocrlf", "false"],
                 ["config", "core.hooksPath", str(d / ".git" / "hooks")], ["add", "-A"],
                 ["-c", "user.email=t@local", "-c", "user.name=t", "commit", "-q", "--no-verify", "-m", "seed"]):
        subprocess.run(["git", *args], cwd=d, capture_output=True, text=True)
    return d


@case
def create_execute_integrate_cleanup():
    d = repo()
    w = wt.create(d, "a")
    assert Path(w["path"]).exists()
    (Path(w["path"]) / "a" / "new.txt").write_text("hello", encoding="utf-8")  # in-domain work
    r = wt.integrate(d, w, "a")
    assert r["integrated"], r
    assert (d / "a" / "new.txt").exists(), "change must reach main via integration"
    wt.cleanup(d, w)
    assert not Path(w["path"]).exists(), "worktree removed on cleanup"


@case
def two_concurrent_same_node_serialize_without_conflict():
    d = repo()
    w1 = wt.create(d, "a", "run1")
    w2 = wt.create(d, "a", "run2")  # a second run of the SAME node, kept apart by run-id
    (Path(w1["path"]) / "a" / "one.txt").write_text("1", encoding="utf-8")
    (Path(w2["path"]) / "a" / "two.txt").write_text("2", encoding="utf-8")
    r1 = wt.integrate(d, w1, "a")
    r2 = wt.integrate(d, w2, "a")  # serialized after r1; disjoint files -> no conflict
    assert r1["integrated"] and r2["integrated"], (r1, r2)
    assert (d / "a" / "one.txt").exists() and (d / "a" / "two.txt").exists()
    wt.cleanup(d, w1)
    wt.cleanup(d, w2)


@case
def integration_rejects_out_of_domain_write():
    d = repo()
    w = wt.create(d, "a")
    (Path(w["path"]) / "b" / "z.txt").write_text("intrude", encoding="utf-8")  # a writing into b/
    r = wt.integrate(d, w, "a")
    assert not r["integrated"] and r["reason"] == "containment", r
    assert not (d / "b" / "z.txt").exists(), "a rejected change must not reach main"
    wt.cleanup(d, w)


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
