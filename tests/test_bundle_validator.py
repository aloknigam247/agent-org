#!/usr/bin/env python
"""Eval-only bundle metadata and freshness scenarios."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import bundle_validator as bv  # noqa: E402

ORG = {"version": 3, "root": "root", "nodes": [
    {"id": "root", "charter": {"domain": ["shared/**"]}, "parent": None, "children": ["a", "b"], "mode": "Parent"},
    {"id": "a", "charter": {"domain": ["a/**"]}, "parent": "root", "children": [], "mode": "Leaf"},
    {"id": "b", "charter": {"domain": ["b/**"]}, "parent": "root", "children": [], "mode": "Leaf"}]}


@pytest.fixture
def repo(file_tree):
    agents = {f".github/agents/{node['id']}.md": f"# {node['id']}\n" for node in ORG["nodes"]}
    return lambda files: file_tree({**agents, **files})


def evidences(result):
    return [v["evidence"] for v in result["violations"]]


def test_front_matter_scalar_and_lists():
    assert bv._front_matter("---\nowner: a\nsources: [x, y]\n---\nbody")["owner"] == "a"
    assert bv._front_matter("---\nowner: a\nsources: [x, y]\n---\n")["sources"] == ["x", "y"]
    block = "---\nowner: b\nsources:\n  - one.py\n  - two.py\n---\n"
    assert bv._front_matter(block)["sources"] == ["one.py", "two.py"]
    assert bv._front_matter("no front matter") == {}


def test_valid_bundle_passes(repo):
    r = bv.check_bundle(ORG, repo({
        "wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes",
        "a/x.py": "y",
        "tools/b/run.py": "print(1)"}))
    assert r["status"] == "ok", r


def test_so2_missing_agent_def_fails(repo):
    d = repo({})
    (d / ".github" / "agents" / "b.md").unlink()  # remove a live node's def
    assert any("missing agent-def" in e for e in evidences(bv.check_bundle(ORG, d)))


def test_so6_orphan_namespace_fails(repo):
    r = bv.check_bundle(ORG, repo({"wiki/ghost/page.md": "x"}))  # 'ghost' is not a live node
    assert any("orphan" in e for e in evidences(r)), r


def test_so3_owner_namespace_mismatch_fails(repo):
    r = bv.check_bundle(ORG, repo({"wiki/a/page.md": "---\nowner: b\n---\nx"}))  # under a/, claims owner b
    assert any("single-writer" in e for e in evidences(r)), r


def test_dangling_source_fails(repo):
    r = bv.check_bundle(ORG, repo({"wiki/a/page.md": "---\nowner: a\nsources: [a/missing.py]\n---\nx"}))
    assert any("dangling source" in e for e in evidences(r)), r


# --- SO5 freshness: a source changed but the artifact citing it was not re-touched ------------------

def test_freshness_stale_when_source_changed_not_retouched(repo):
    d = repo({"wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes", "a/x.py": "y"})
    r = bv.check_freshness(d, ["a/x.py"])  # source changed, notes.md not touched
    assert r["status"] == "violations"
    assert any("not re-touched" in e for e in evidences(r)), r


def test_freshness_ok_when_retouched_together(repo):
    d = repo({"wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes", "a/x.py": "y"})
    r = bv.check_freshness(d, ["a/x.py", "wiki/a/notes.md"])  # both changed in the same run
    assert r["status"] == "ok", r
    assert r["checked"] == 1


def test_freshness_ok_when_source_unchanged(repo):
    d = repo({"wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes", "a/x.py": "y"})
    r = bv.check_freshness(d, ["b/unrelated.py"])  # nothing the artifact depends on changed
    assert r["status"] == "ok", r
