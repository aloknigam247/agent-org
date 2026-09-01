#!/usr/bin/env python
"""Fresh, independent self-tests for the owner-oracle (owner_validator).

The scenarios are deliberately unrelated to the design's canonical examples, so a green run proves the
oracle's *semantics* rather than re-asserting an example it was modelled on. Pure functions only (no
git, no filesystem): each test builds a tiny in-memory org and checks ownership, coverage, tree, and
containment directly.

Run: ``python .github/tools/test_owner_validator.py`` — prints a one-line summary and exits non-zero if
any case fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "tools"))
import owner_validator as ov  # noqa: E402

TOOL = Path(__file__).resolve().parent.parent / "plugin" / "tools" / "owner_validator.py"
CASES = []
_DIRS = []


def case(fn):
    CASES.append(fn)
    return fn


def git_repo(files, org_dict):
    """A hermetic git repo with `files` (rel->content) plus org.json, committed — for git_tracked /
    domain_size / CLI tests."""
    d = Path(tempfile.mkdtemp(prefix="ovt-"))
    _DIRS.append(d)
    (d / "org.json").write_text(json.dumps(org_dict), encoding="utf-8")
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["config", "core.autocrlf", "false"],
                 ["config", "core.hooksPath", str(d / ".git" / "hooks")], ["add", "-A"],
                 ["-c", "user.email=t@local", "-c", "user.name=t", "commit", "-q", "--no-verify", "-m", "seed"]):
        subprocess.run(["git", *args], cwd=d, capture_output=True, text=True)
    return d


def node(nid, domain=None, excludes=None, parent=None, children=None, mode=None):
    charter = {}
    if domain is not None:
        charter["domain"] = domain
    if excludes is not None:
        charter["excludes"] = excludes
    n = {"id": nid, "charter": charter, "parent": parent, "children": children or []}
    if mode:
        n["mode"] = mode
    return n


def org(root, nodes):
    return {"version": 1, "root": root, "nodes": nodes}


def owners(o, path):
    return ov.owners_of(ov.compile_nodes(o["nodes"]), path)


# --- glob dialect: gitignore semantics via pathspec (design §2.7) ------------------------------------

@case
def bare_globstar_matches_root_nested_and_dotfiles():
    o = org("all", [node("all", ["**"], mode="Leaf")])
    for p in ["README", ".gitignore", "content/post.md", "a/b/c/deep.txt", ".config/x"]:
        assert owners(o, p) == ["all"], (p, owners(o, p))


@case
def dir_globstar_scopes_to_subtree_only():
    o = org("r", [node("blog", ["content/**"], mode="Leaf"),
                  node("rest", ["**"], excludes=["content/**"], mode="Leaf")])
    assert owners(o, "content/2021/post.md") == ["blog"]
    assert owners(o, "content/index.md") == ["blog"]
    assert owners(o, "themes/base.css") == ["rest"]
    # a bare file literally named "content" is NOT under content/** and falls to the catch-all
    assert owners(o, "content") == ["rest"]


@case
def globstar_name_matches_in_any_directory():
    o = org("r", [node("locks", ["**/*.lock"], mode="Leaf"),
                  node("app", ["**"], excludes=["**/*.lock"], mode="Leaf")])
    assert owners(o, "deps.lock") == ["locks"]
    assert owners(o, "app/sub/pkg.lock") == ["locks"]
    assert owners(o, "app/main.py") == ["app"]


@case
def matching_is_case_sensitive():
    o = org("r", [node("cap", ["Src/**"], mode="Leaf"),
                  node("rest", ["**"], excludes=["Src/**"], mode="Leaf")])
    assert owners(o, "src/x.py") == ["rest"]   # lowercase is not matched by Src/**
    assert owners(o, "Src/x.py") == ["cap"]


@case
def dotfiles_are_matched_like_any_path():
    o = org("r", [node("cfg", ["config/**"], mode="Leaf"),
                  node("rest", ["**"], excludes=["config/**"], mode="Leaf")])
    assert owners(o, "config/.secret") == ["cfg"]
    assert owners(o, ".editorconfig") == ["rest"]


# --- coverage: exactly-one-owner (design §2.2) -------------------------------------------------------

@case
def coverage_gap_is_unowned():
    o = org("r", [node("only", ["kitchen/**"], mode="Leaf")])
    v = ov.check_coverage(o, ["kitchen/stove.py", "garden/rose.py"])
    assert [(x["path"], x["rule"]) for x in v] == [("garden/rose.py", "uncovered")], v


@case
def coverage_overlap_is_flagged():
    o = org("r", [node("a", ["shared/**"], mode="Leaf"),
                  node("b", ["shared/**"], mode="Leaf")])
    v = ov.check_coverage(o, ["shared/x"])
    assert len(v) == 1 and v[0]["rule"] == "overlap", v


@case
def coverage_partition_is_clean():
    o = org("r", [node("a", ["left/**"], mode="Leaf"),
                  node("b", ["**"], excludes=["left/**"], mode="Leaf")])
    assert ov.check_coverage(o, ["left/x", "right/y", "top"]) == []


# --- tree invariants (design §5) ---------------------------------------------------------------------

@case
def tree_valid_parent_with_two_leaves():
    o = org("root", [node("root", ["shared/**"], parent=None, children=["c1", "c2"], mode="Parent"),
                     node("c1", ["one/**"], parent="root", mode="Leaf"),
                     node("c2", ["two/**"], parent="root", mode="Leaf")])
    assert ov.check_tree(o) == []


@case
def tree_rejects_parent_globstar_domain():
    # a Parent's domain must be an explicit shared set, never ** (design §2.2)
    o = org("root", [node("root", ["**"], parent=None, children=["c1", "c2"], mode="Parent"),
                     node("c1", ["one/**"], parent="root", mode="Leaf"),
                     node("c2", ["two/**"], parent="root", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("Parent domain is **" in e for e in ev), ev


@case
def tree_rejects_two_roots():
    o = org("a", [node("a", ["x/**"], parent=None, mode="Leaf"),
                  node("b", ["y/**"], parent=None, mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("expected exactly 1 root" in e for e in ev), ev


@case
def tree_rejects_leaf_with_children():
    o = org("root", [node("root", ["**"], parent=None, children=["k"], mode="Leaf"),
                     node("k", ["k/**"], parent="root", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("Leaf has children" in e for e in ev), ev


@case
def tree_rejects_parent_with_one_child():
    o = org("root", [node("root", ["**"], parent=None, children=["only"], mode="Parent"),
                     node("only", ["only/**"], parent="root", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("fewer than 2 children" in e for e in ev), ev


@case
def tree_rejects_backreference_mismatch():
    o = org("root", [node("root", ["**"], parent=None, children=["c1", "c2"], mode="Parent"),
                     node("c1", ["a/**"], parent="root", mode="Leaf"),
                     node("c2", ["b/**"], parent="c1", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("back-reference mismatch" in e for e in ev), ev


@case
def tree_rejects_duplicate_id():
    o = org("root", [node("root", ["**"], parent=None, children=["c1", "c2"], mode="Parent"),
                     node("c1", ["a/**"], parent="root", mode="Leaf"),
                     node("c1", ["b/**"], parent="root", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("duplicate id" in e for e in ev), ev


@case
def tree_rejects_cycle():
    o = org("x", [node("x", ["x/**"], parent="y", mode="Leaf"),
                  node("y", ["y/**"], parent="x", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("cycle" in e for e in ev), ev


# --- containment: the integration gate (design §2.5, §2.7) ------------------------------------------

@case
def containment_accepts_acting_owned_paths():
    o = org("r", [node("a", ["a/**"], mode="Leaf"),
                  node("b", ["**"], excludes=["a/**"], mode="Leaf")])
    r = ov.check_containment(o, "a", ["a/x.py", "a/sub/y.py"])
    assert r["status"] == "ok", r


@case
def containment_rejects_foreign_path():
    o = org("r", [node("a", ["a/**"], mode="Leaf"),
                  node("b", ["**"], excludes=["a/**"], mode="Leaf")])
    r = ov.check_containment(o, "a", ["b/other.py"])
    assert r["status"] == "violations"
    assert r["violations"][0]["rule"] == "containment"
    assert r["violations"][0]["owner"] == "b"


@case
def containment_rejects_unowned_change():
    o = org("r", [node("a", ["a/**"], mode="Leaf")])
    r = ov.check_containment(o, "a", ["z/new.py"])
    assert r["violations"][0]["rule"] == "uncovered", r


# --- path normalization ------------------------------------------------------------------------------

@case
def normalize_backslashes_and_dot_prefix():
    assert ov.normalize("a\\b\\c") == "a/b/c"
    assert ov.normalize("./x/y") == "x/y"
    assert ov.normalize("././z") == "z"


# --- glob boundary cases (gitignore dialect via pathspec) -------------------------------------------

@case
def glob_slashless_matches_any_depth_but_anchored_does_not():
    o = org("r", [node("a", ["Makefile"], mode="Leaf"),
                  node("b", ["**"], excludes=["Makefile"], mode="Leaf")])
    assert owners(o, "Makefile") == ["a"]
    assert owners(o, "sub/Makefile") == ["a"]        # slashless matches at any depth
    anchored = org("r", [node("a", ["/Makefile"], mode="Leaf"),
                         node("b", ["**"], excludes=["/Makefile"], mode="Leaf")])
    assert owners(anchored, "sub/Makefile") == ["b"]  # a leading slash anchors to the root


@case
def glob_trailing_slash_matches_directory_contents():
    o = org("r", [node("a", ["build/"], mode="Leaf"),
                  node("b", ["**"], excludes=["build/"], mode="Leaf")])
    assert owners(o, "build/out.o") == ["a"]


@case
def glob_character_class():
    o = org("r", [node("a", ["**/*.[ch]"], mode="Leaf"),
                  node("b", ["**"], excludes=["**/*.[ch]"], mode="Leaf")])
    assert owners(o, "src/main.c") == ["a"]
    assert owners(o, "src/main.h") == ["a"]
    assert owners(o, "src/main.py") == ["b"]


@case
def exclude_not_recovered_is_unowned():
    o = org("r", [node("a", ["src/**"], excludes=["src/gen/**"], mode="Leaf")])  # nobody re-covers src/gen
    v = ov.check_coverage(o, ["src/x", "src/gen/y"])
    assert [(x["path"], x["rule"]) for x in v] == [("src/gen/y", "uncovered")], v


@case
def tree_rejects_parent_missing_child_backref():
    # c2 claims root as parent, but root.children omits it (a reverse back-reference gap)
    o = org("root", [node("root", ["shared/**"], parent=None, children=["c1", "c2"], mode="Parent"),
                     node("c1", ["a/**"], parent="root", mode="Leaf"),
                     node("c2", ["b/**"], parent="c1", mode="Leaf")])  # c2's parent is c1, not root
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("does not list it as a child" in e for e in ev), ev


# --- git-backed: domain_size (split-trigger proxy) and CLI contracts --------------------------------

_AB = {"version": 3, "root": "b", "nodes": [
    {"id": "a", "charter": {"domain": ["a/**"]}, "parent": "b", "children": [], "mode": "Leaf"},
    {"id": "b", "charter": {"domain": ["**"], "excludes": ["a/**"]}, "parent": None,
     "children": ["a", "c"], "mode": "Parent"},
    {"id": "c", "charter": {"domain": ["c/**"]}, "parent": "b", "children": [], "mode": "Leaf"}]}


@case
def domain_size_counts_only_owned_bytes():
    repo = git_repo({"a/x.txt": "12345", "c/y.txt": "123"}, _AB)  # a owns 5 bytes, c owns 3
    m = ov.domain_size(_AB, "a", repo)
    assert m["files"] == 1 and m["bytes"] == 5, m
    assert m["est_tokens"] == 1, m  # 5 // 4


@case
def cli_owner_exit_codes():
    org_one = {"version": 3, "root": "main", "nodes": [
        {"id": "main", "charter": {"domain": ["a/**"]}, "parent": None, "children": [], "mode": "Leaf"}]}
    repo = git_repo({"a/x.txt": "1"}, org_one)
    org_path = str(repo / "org.json")
    owned = subprocess.run([sys.executable, str(TOOL), "--owner", "a/x.txt", "--org", org_path],
                           capture_output=True, text=True)
    assert owned.returncode == 0, owned.stderr
    unowned = subprocess.run([sys.executable, str(TOOL), "--owner", "top.txt", "--org", org_path],
                             capture_output=True, text=True)
    assert unowned.returncode == 1, unowned.stdout  # a path no node owns exits non-zero


# --- split-transition validator (design §2.3, §3.6) -------------------------------------------------

def _root_split():
    old = org("main", [node("main", ["**"], parent=None, mode="Leaf")])
    old["version"] = 3
    new = {"version": 4, "root": "main", "nodes": [
        node("main", ["root.txt"], parent=None, children=["a", "b"], mode="Parent"),
        node("a", ["a/**"], parent="main", mode="Leaf"),
        node("b", ["b/**"], parent="main", mode="Leaf")]}
    return old, new


@case
def split_valid_root_split():
    old, new = _root_split()
    r = ov.check_split(old, new, ["a/x", "b/y", "root.txt"])
    assert r["status"] == "ok", r


@case
def split_valid_second_generation():
    old = {"version": 3, "root": "root", "nodes": [
        node("root", ["shared/**"], parent=None, children=["a", "b"], mode="Parent"),
        node("a", ["a/**"], parent="root", mode="Leaf"),
        node("b", ["b/**"], parent="root", mode="Leaf")]}
    new = {"version": 4, "root": "root", "nodes": [
        node("root", ["shared/**"], parent=None, children=["a", "b"], mode="Parent"),
        node("a", ["a/base/**"], parent="root", children=["a1", "a2"], mode="Parent"),
        node("a1", ["a/one/**"], parent="a", mode="Leaf"),
        node("a2", ["a/two/**"], parent="a", mode="Leaf"),
        node("b", ["b/**"], parent="root", mode="Leaf")]}
    r = ov.check_split(old, new, ["a/one/x", "a/two/y", "a/base/z", "b/w", "shared/s"])
    assert r["status"] == "ok", r


@case
def split_rejects_wrong_version():
    old, new = _root_split()
    new["version"] = 5  # must be old + 1
    r = ov.check_split(old, new)
    assert any("version must bump" in v["evidence"] for v in r["violations"]), r


@case
def split_rejects_root_change():
    old, new = _root_split()
    new["root"] = "a"
    r = ov.check_split(old, new)
    assert any("root changed" in v["evidence"] for v in r["violations"]), r


@case
def split_rejects_removed_node():
    old = {"version": 3, "root": "root", "nodes": [
        node("root", ["shared/**"], parent=None, children=["a", "b"], mode="Parent"),
        node("a", ["a/**"], parent="root", mode="Leaf"),
        node("b", ["b/**"], parent="root", mode="Leaf")]}
    new = {"version": 4, "root": "root", "nodes": [  # split a, but illegally drop b
        node("root", ["shared/**"], parent=None, children=["a"], mode="Parent"),
        node("a", ["a/**"], parent="root", children=["a1", "a2"], mode="Parent"),
        node("a1", ["a/one/**"], parent="a", mode="Leaf"),
        node("a2", ["a/two/**"], parent="a", mode="Leaf")]}
    r = ov.check_split(old, new)
    assert any("removed" in v["evidence"] for v in r["violations"]), r


@case
def split_rejects_non_leaf_child():
    old, new = _root_split()
    new["nodes"][1]["mode"] = "Parent"  # child 'a' must be a Leaf
    r = ov.check_split(old, new)
    assert any("must be a Leaf" in v["evidence"] for v in r["violations"]), r


@case
def split_rejects_path_leaving_subtree():
    old, new = _root_split()  # new main domain is only root.txt; a/**, b/** cover the rest
    r = ov.check_split(old, new, ["a/x", "b/y", "orphan.txt"])  # orphan matches nothing new
    assert any("left the split subtree" in v["evidence"] for v in r["violations"]), r


# --- preToolUse hook: warn/enforce classification + in-place identity + foreign-log ----------------

_HOOK_ORG = org("root", [node("root", ["shared/**"], parent=None, children=["a", "b"], mode="Parent"),
                         node("a", ["a/**"], parent="root", mode="Leaf"),
                         node("b", ["b/**"], parent="root", mode="Leaf")])


def _payload(tool, path, cwd="/repo", sid=None):
    p = {"toolName": tool, "toolArgs": {"path": path}, "cwd": cwd}
    if sid:
        p["sessionId"] = sid
    return p


@case
def hook_allows_owned_write_no_foreign():
    d, f = ov.hook_decision(_payload("create", "/repo/a/new.py"), _HOOK_ORG, acting="a")
    assert d["permissionDecision"] == "allow" and f is None, (d, f)


@case
def hook_warn_allows_but_flags_foreign():
    d, f = ov.hook_decision(_payload("edit", "/repo/b/x.py"), _HOOK_ORG, mode="warn", acting="a")
    assert d["permissionDecision"] == "allow", d          # warn: write is allowed (content preserved)
    assert f == {"path": "b/x.py", "owner": "b", "acting": "a"}, f


@case
def hook_enforce_denies_foreign():
    d, f = ov.hook_decision(_payload("edit", "/repo/b/x.py"), _HOOK_ORG, mode="enforce", acting="a")
    assert d["permissionDecision"] == "deny" and "owned by 'b'" in d["permissionDecisionReason"], d
    assert f is not None


@case
def hook_flags_unowned():
    d, f = ov.hook_decision(_payload("create", "/repo/nowhere/x"), _HOOK_ORG, mode="warn", acting="a")
    assert d["permissionDecision"] == "allow" and f["owner"] is None, (d, f)
    d2, _ = ov.hook_decision(_payload("create", "/repo/nowhere/x"), _HOOK_ORG, mode="enforce", acting="a")
    assert d2["permissionDecision"] == "deny" and "UNOWNED" in d2["permissionDecisionReason"], d2


@case
def hook_allows_non_write_tool():
    d, f = ov.hook_decision({"toolName": "glob", "toolArgs": {"pattern": "**/*"}, "cwd": "/repo"},
                            _HOOK_ORG, acting="a")
    assert d["permissionDecision"] == "allow" and f is None, (d, f)


@case
def hook_derives_acting_from_worktree_path():
    ok, f1 = ov.hook_decision(_payload("create", "/repo/.worktrees/a/run1/a/x.py"), _HOOK_ORG,
                              mode="enforce", acting="zzz")  # wrong env ignored; path says 'a'
    assert ok["permissionDecision"] == "allow" and f1 is None, (ok, f1)
    bad, f2 = ov.hook_decision(_payload("create", "/repo/.worktrees/a/run1/b/y.py"), _HOOK_ORG,
                               mode="enforce", acting="zzz")
    assert bad["permissionDecision"] == "deny" and f2["owner"] == "b", (bad, f2)


@case
def hook_without_acting_flags_only_unowned():
    owned, f1 = ov.hook_decision(_payload("create", "/repo/b/x.py"), _HOOK_ORG, acting=None)
    assert owned["permissionDecision"] == "allow" and f1 is None, (owned, f1)  # containment uncheckable
    unowned, f2 = ov.hook_decision(_payload("create", "/repo/z/x"), _HOOK_ORG, acting=None)
    assert f2 is not None and f2["owner"] is None, f2


# --- in-place identity: record_acting (userPromptSubmitted) + preToolUse resolves + logs foreign -----

@case
def record_acting_and_resolve_roundtrip():
    repo = git_repo({"a/keep.txt": "x", "b/keep.txt": "x", "shared/keep.txt": "x"}, _HOOK_ORG)
    ov.record_acting({"sessionId": "S1", "cwd": str(repo), "prompt": "AgentOrgActingNode: a\nDo work"})
    assert ov._acting_from_map({"sessionId": "S1", "cwd": str(repo)}) == "a"
    assert ov._acting_from_map({"sessionId": "unknown", "cwd": str(repo)}) is None


@case
def hook_cli_warn_allows_and_logs_foreign():
    repo = git_repo({"a/keep.txt": "x", "b/keep.txt": "x", "shared/keep.txt": "x"}, _HOOK_ORG)
    # record 'a' as the acting node for session S2, then S2 writes into b/ (foreign)
    ov.record_acting({"sessionId": "S2", "cwd": str(repo), "prompt": "AgentOrgActingNode: a"})
    pl = json.dumps(_payload("edit", str(repo / "b" / "x.py"), str(repo), sid="S2"))
    p = subprocess.run([sys.executable, str(TOOL), "--hook", "--org", str(repo / "org.json")],
                       input=pl, capture_output=True, text=True)
    assert json.loads(p.stdout)["permissionDecision"] == "allow", p.stdout   # warn = allow
    log = repo / ".git" / "agent-org" / "foreign" / "a.jsonl"   # keyed by the acting node 'a'
    assert log.exists(), "foreign write must be logged"
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["path"] == "b/x.py" and entry["owner"] == "b" and entry["acting"] == "a", entry
    assert entry["sessionId"] == "S2", entry


@case
def hook_cli_enforce_denies_foreign():
    repo = git_repo({"a/keep.txt": "x", "b/keep.txt": "x", "shared/keep.txt": "x"}, _HOOK_ORG)
    ov.record_acting({"sessionId": "S3", "cwd": str(repo), "prompt": "AgentOrgActingNode: a"})
    pl = json.dumps(_payload("edit", str(repo / "b" / "x.py"), str(repo), sid="S3"))
    p = subprocess.run([sys.executable, str(TOOL), "--hook", "--mode", "enforce", "--org", str(repo / "org.json")],
                       input=pl, capture_output=True, text=True)
    assert json.loads(p.stdout)["permissionDecision"] == "deny", p.stdout


@case
def hook_cli_allows_when_no_org():
    # a non-agent-org repo (no org.json) must never be disturbed by the plugin hook
    d = Path(tempfile.mkdtemp(prefix="noorg-"))
    _DIRS.append(d)
    pl = json.dumps(_payload("create", str(d / "anything.txt"), str(d)))
    p = subprocess.run([sys.executable, str(TOOL), "--hook", "--org", str(d / "org.json")],
                       input=pl, capture_output=True, text=True)
    assert json.loads(p.stdout)["permissionDecision"] == "allow", p.stdout


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
