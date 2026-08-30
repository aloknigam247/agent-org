#!/usr/bin/env python
"""Self-tests for the bundle-integrity validator (SO2-SO6). Fresh scenarios, no agent calls.

Run: ``python .github/tools/test_bundle_validator.py`` — one-line summary, non-zero exit on failure.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "tools"))
import bundle_validator as bv  # noqa: E402

CASES = []
_DIRS = []


def case(fn):
    CASES.append(fn)
    return fn


ORG = {"version": 3, "root": "root", "nodes": [
    {"id": "root", "charter": {"domain": ["shared/**"]}, "parent": None, "children": ["a", "b"], "mode": "Parent"},
    {"id": "a", "charter": {"domain": ["a/**"]}, "parent": "root", "children": [], "mode": "Leaf"},
    {"id": "b", "charter": {"domain": ["b/**"]}, "parent": "root", "children": [], "mode": "Leaf"}]}


def repo(files):
    """A repo tree with the given files (rel->content). Agent-defs for all live nodes are added unless
    a `.github/agents/<id>.md` is explicitly overridden in `files`."""
    d = Path(tempfile.mkdtemp(prefix="bvt-"))
    _DIRS.append(d)
    for nid in ("root", "a", "b"):
        p = d / ".github" / "agents" / f"{nid}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {nid}\n", encoding="utf-8")
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


def evidences(result):
    return [v["evidence"] for v in result["violations"]]


@case
def front_matter_scalar_and_lists():
    assert bv._front_matter("---\nowner: a\nsources: [x, y]\n---\nbody")["owner"] == "a"
    assert bv._front_matter("---\nowner: a\nsources: [x, y]\n---\n")["sources"] == ["x", "y"]
    block = "---\nowner: b\nsources:\n  - one.py\n  - two.py\n---\n"
    assert bv._front_matter(block)["sources"] == ["one.py", "two.py"]
    assert bv._front_matter("no front matter") == {}


@case
def valid_bundle_passes():
    r = bv.check_bundle(ORG, repo({
        "wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes",
        "a/x.py": "y",
        "tools/b/run.py": "print(1)"}))
    assert r["status"] == "ok", r


@case
def so2_missing_agent_def_fails():
    d = repo({})
    (d / ".github" / "agents" / "b.md").unlink()  # remove a live node's def
    assert any("missing agent-def" in e for e in evidences(bv.check_bundle(ORG, d)))


@case
def so6_orphan_namespace_fails():
    r = bv.check_bundle(ORG, repo({"wiki/ghost/page.md": "x"}))  # 'ghost' is not a live node
    assert any("orphan" in e for e in evidences(r)), r


@case
def so3_owner_namespace_mismatch_fails():
    r = bv.check_bundle(ORG, repo({"wiki/a/page.md": "---\nowner: b\n---\nx"}))  # under a/, claims owner b
    assert any("single-writer" in e for e in evidences(r)), r


@case
def dangling_source_fails():
    r = bv.check_bundle(ORG, repo({"wiki/a/page.md": "---\nowner: a\nsources: [a/missing.py]\n---\nx"}))
    assert any("dangling source" in e for e in evidences(r)), r


# --- SO5 freshness: a source changed but the artifact citing it was not re-touched ------------------

@case
def freshness_stale_when_source_changed_not_retouched():
    d = repo({"wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes", "a/x.py": "y"})
    r = bv.check_freshness(d, ["a/x.py"])  # source changed, notes.md not touched
    assert r["status"] == "violations"
    assert any("not re-touched" in e for e in evidences(r)), r


@case
def freshness_ok_when_retouched_together():
    d = repo({"wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes", "a/x.py": "y"})
    r = bv.check_freshness(d, ["a/x.py", "wiki/a/notes.md"])  # both changed in the same run
    assert r["status"] == "ok", r
    assert r["checked"] == 1


@case
def freshness_ok_when_source_unchanged():
    d = repo({"wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes", "a/x.py": "y"})
    r = bv.check_freshness(d, ["b/unrelated.py"])  # nothing the artifact depends on changed
    assert r["status"] == "ok", r


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
