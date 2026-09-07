#!/usr/bin/env python
"""Independent oracle scenarios, collected individually by pytest."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "tools"))
import owner_validator as ov  # noqa: E402

TOOL = Path(__file__).resolve().parent.parent / "plugin" / "tools" / "owner_validator.py"


@pytest.fixture
def git_repo(sandbox):
    return lambda files, org_dict: sandbox({"org.json": json.dumps(org_dict), **files})


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

def test_bare_globstar_matches_root_nested_and_dotfiles():
    o = org("all", [node("all", ["**"], mode="Leaf")])
    for p in ["README", ".gitignore", "content/post.md", "a/b/c/deep.txt", ".config/x"]:
        assert owners(o, p) == ["all"], (p, owners(o, p))


def test_dir_globstar_scopes_to_subtree_only():
    o = org("r", [node("blog", ["content/**"], mode="Leaf"),
                  node("rest", ["**"], excludes=["content/**"], mode="Leaf")])
    assert owners(o, "content/2021/post.md") == ["blog"]
    assert owners(o, "content/index.md") == ["blog"]
    assert owners(o, "themes/base.css") == ["rest"]
    # a bare file literally named "content" is NOT under content/** and falls to the catch-all
    assert owners(o, "content") == ["rest"]


def test_globstar_name_matches_in_any_directory():
    o = org("r", [node("locks", ["**/*.lock"], mode="Leaf"),
                  node("app", ["**"], excludes=["**/*.lock"], mode="Leaf")])
    assert owners(o, "deps.lock") == ["locks"]
    assert owners(o, "app/sub/pkg.lock") == ["locks"]
    assert owners(o, "app/main.py") == ["app"]


def test_matching_is_case_sensitive():
    o = org("r", [node("cap", ["Src/**"], mode="Leaf"),
                  node("rest", ["**"], excludes=["Src/**"], mode="Leaf")])
    assert owners(o, "src/x.py") == ["rest"]   # lowercase is not matched by Src/**
    assert owners(o, "Src/x.py") == ["cap"]


def test_dotfiles_are_matched_like_any_path():
    o = org("r", [node("cfg", ["config/**"], mode="Leaf"),
                  node("rest", ["**"], excludes=["config/**"], mode="Leaf")])
    assert owners(o, "config/.secret") == ["cfg"]
    assert owners(o, ".editorconfig") == ["rest"]


# --- coverage: exactly-one-owner (design §2.2) -------------------------------------------------------

def test_coverage_gap_is_unowned():
    o = org("r", [node("only", ["kitchen/**"], mode="Leaf")])
    v = ov.check_coverage(o, ["kitchen/stove.py", "garden/rose.py"])
    assert [(x["path"], x["rule"]) for x in v] == [("garden/rose.py", "uncovered")], v


def test_coverage_overlap_is_flagged():
    o = org("r", [node("a", ["shared/**"], mode="Leaf"),
                  node("b", ["shared/**"], mode="Leaf")])
    v = ov.check_coverage(o, ["shared/x"])
    assert len(v) == 1 and v[0]["rule"] == "overlap", v


def test_coverage_partition_is_clean():
    o = org("r", [node("a", ["left/**"], mode="Leaf"),
                  node("b", ["**"], excludes=["left/**"], mode="Leaf")])
    assert ov.check_coverage(o, ["left/x", "right/y", "top"]) == []


# --- tree invariants (design §5) ---------------------------------------------------------------------

def test_tree_valid_parent_with_two_leaves():
    o = org("root", [node("root", ["shared/**"], parent=None, children=["c1", "c2"], mode="Parent"),
                     node("c1", ["one/**"], parent="root", mode="Leaf"),
                     node("c2", ["two/**"], parent="root", mode="Leaf")])
    assert ov.check_tree(o) == []


def test_tree_rejects_parent_globstar_domain():
    # a Parent's domain must be an explicit shared set, never ** (design §2.2)
    o = org("root", [node("root", ["**"], parent=None, children=["c1", "c2"], mode="Parent"),
                     node("c1", ["one/**"], parent="root", mode="Leaf"),
                     node("c2", ["two/**"], parent="root", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("Parent domain is **" in e for e in ev), ev


def test_tree_rejects_two_roots():
    o = org("a", [node("a", ["x/**"], parent=None, mode="Leaf"),
                  node("b", ["y/**"], parent=None, mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("expected exactly 1 root" in e for e in ev), ev


def test_tree_rejects_leaf_with_children():
    o = org("root", [node("root", ["**"], parent=None, children=["k"], mode="Leaf"),
                     node("k", ["k/**"], parent="root", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("Leaf has children" in e for e in ev), ev


def test_tree_rejects_parent_with_one_child():
    o = org("root", [node("root", ["**"], parent=None, children=["only"], mode="Parent"),
                     node("only", ["only/**"], parent="root", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("fewer than 2 children" in e for e in ev), ev


def test_tree_rejects_backreference_mismatch():
    o = org("root", [node("root", ["**"], parent=None, children=["c1", "c2"], mode="Parent"),
                     node("c1", ["a/**"], parent="root", mode="Leaf"),
                     node("c2", ["b/**"], parent="c1", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("back-reference mismatch" in e for e in ev), ev


def test_tree_rejects_duplicate_id():
    o = org("root", [node("root", ["**"], parent=None, children=["c1", "c2"], mode="Parent"),
                     node("c1", ["a/**"], parent="root", mode="Leaf"),
                     node("c1", ["b/**"], parent="root", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("duplicate id" in e for e in ev), ev


def test_tree_rejects_cycle():
    o = org("x", [node("x", ["x/**"], parent="y", mode="Leaf"),
                  node("y", ["y/**"], parent="x", mode="Leaf")])
    ev = [x["evidence"] for x in ov.check_tree(o)]
    assert any("cycle" in e for e in ev), ev


# --- containment: the integration gate (design §2.5, §2.7) ------------------------------------------

def test_containment_accepts_acting_owned_paths():
    o = org("r", [node("a", ["a/**"], mode="Leaf"),
                  node("b", ["**"], excludes=["a/**"], mode="Leaf")])
    r = ov.check_containment(o, "a", ["a/x.py", "a/sub/y.py"])
    assert r["status"] == "ok", r


def test_containment_rejects_foreign_path():
    o = org("r", [node("a", ["a/**"], mode="Leaf"),
                  node("b", ["**"], excludes=["a/**"], mode="Leaf")])
    r = ov.check_containment(o, "a", ["b/other.py"])
    assert r["status"] == "violations"
    assert r["violations"][0]["rule"] == "containment"
    assert r["violations"][0]["owner"] == "b"


def test_containment_rejects_unowned_change():
    o = org("r", [node("a", ["a/**"], mode="Leaf")])
    r = ov.check_containment(o, "a", ["z/new.py"])
    assert r["violations"][0]["rule"] == "uncovered", r


# --- path normalization ------------------------------------------------------------------------------

def test_normalize_backslashes_and_dot_prefix():
    assert ov.normalize("a\\b\\c") == "a/b/c"
    assert ov.normalize("./x/y") == "x/y"
    assert ov.normalize("././z") == "z"


def prefix_skill_org():
    return org("root", [
        node("root", ["shared/**"], parent=None, children=["auth", "other"], mode="Parent"),
        node(
            "auth",
            [".github/skills/agent-org-auth-*/**", "auth/**"],
            excludes=[".github/skills/agent-org-auth-api-*/**"],
            parent="root",
            children=["auth-api", "auth-ui"],
            mode="Parent",
        ),
        node(
            "auth-api",
            [".github/skills/agent-org-auth-api-*/**", "auth/api/**"],
            parent="auth",
            mode="Leaf",
        ),
        node(
            "auth-ui",
            [".github/skills/agent-org-auth-ui-*/**", "auth/ui/**"],
            parent="auth",
            mode="Leaf",
        ),
        node("other", ["other/**"], parent="root", mode="Leaf"),
    ])


def test_standard_skill_ownership_comes_from_explicit_charters():
    o = prefix_skill_org()
    child = ".github/skills/agent-org-auth-api-refresh/SKILL.md"
    parent = ".github/skills/agent-org-auth-review/SKILL.md"
    assert ov.ownership(o, child)["owner"] == "auth-api"
    assert ov.ownership(o, parent)["owner"] == "auth"


def test_proposed_skill_metadata_cannot_reassign_existing_descendant(tmp_path):
    o = prefix_skill_org()
    path = tmp_path / ".github" / "skills" / "agent-org-auth-api-refresh" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: agent-org-auth-api-refresh\nowner: auth-api\n---\n", encoding="utf-8")
    payload = {
        "cwd": str(tmp_path),
        "toolArgs": {
            "content": "---\nname: agent-org-auth-api-refresh\nowner: auth\nuser-invocable: false\n---\n",
            "path": str(path),
        },
        "toolName": "edit",
    }
    decision, records = ov.classify_write(payload, o, acting="auth", root=tmp_path)
    assert decision["permissionDecision"] == "deny"
    assert records == [{
        "acting": "auth",
        "disposition": "deny",
        "owner": "auth-api",
        "path": ".github/skills/agent-org-auth-api-refresh/SKILL.md",
    }]


def test_new_skill_and_companion_paths_support_absolute_and_subdirectory_patches(tmp_path):
    o = prefix_skill_org()
    relative_skill = ".github/skills/agent-org-auth-api-refresh/SKILL.md"
    relative_companion = ".github/skills/agent-org-auth-api-refresh/examples/sample.json"
    subdirectory = tmp_path / "work"
    subdirectory.mkdir()
    subdirectory_skill = "..\\" + relative_skill.replace("/", "\\")
    subdirectory_companion = "..\\" + relative_companion.replace("/", "\\")
    path_sets = [
        (tmp_path, str(tmp_path / Path(relative_skill)), str(tmp_path / Path(relative_companion))),
        (subdirectory, subdirectory_skill, subdirectory_companion),
    ]
    for cwd, skill_path, companion_path in path_sets:
        payload = {
            "cwd": str(cwd),
            "toolArgs": {
                "input": (
                    "*** Begin Patch\n"
                    f"*** Add File: {skill_path}\n"
                    "+---\n+name: agent-org-auth-api-refresh\n+owner: auth\n+user-invocable: false\n+---\n"
                    f"*** Add File: {companion_path}\n"
                    "+{}\n"
                    "*** End Patch\n"
                ),
            },
            "toolName": "apply_patch",
        }
        decision, records = ov.classify_write(payload, o, acting="auth-api", root=tmp_path)
        assert decision["permissionDecision"] == "allow"
        assert records == []


def test_skill_deletion_rename_and_companion_ownership_do_not_depend_on_metadata(tmp_path):
    o = prefix_skill_org()
    old = tmp_path / ".github" / "skills" / "agent-org-auth-api-refresh" / "SKILL.md"
    old.parent.mkdir(parents=True)
    old.write_text("---\nname: agent-org-auth-api-refresh\nowner: auth-api\n---\n", encoding="utf-8")
    companion = old.parent / "examples" / "sample.json"
    assert ov.ownership(o, companion.relative_to(tmp_path).as_posix())["owner"] == "auth-api"
    old.unlink()
    delete = {"cwd": str(tmp_path), "toolArgs": {"path": str(old)}, "toolName": "delete"}
    decision, records = ov.classify_write(delete, o, acting="auth-api", root=tmp_path)
    assert decision["permissionDecision"] == "allow"
    assert records == []
    rename = {
        "cwd": str(tmp_path),
        "toolArgs": {
            "input": (
                "*** Begin Patch\n"
                "*** Update File: .github/skills/agent-org-auth-api-refresh/SKILL.md\n"
                "*** Move to: .github/skills/agent-org-auth-api-renamed/SKILL.md\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            ),
        },
        "toolName": "apply_patch",
    }
    decision, records = ov.classify_write(rename, o, acting="auth-api", root=tmp_path)
    assert decision["permissionDecision"] == "allow"
    assert records == []


def test_unrelated_skill_metadata_does_not_change_charter_ownership(tmp_path):
    o = org("root", [node("root", [".github/**"], parent=None, mode="Leaf")])
    path = tmp_path / ".github" / "skills" / "human-release" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: human-release\nowner: person\n---\n", encoding="utf-8")
    assert ov.ownership(o, path.relative_to(tmp_path).as_posix())["owner"] == "root"


# --- glob boundary cases (gitignore dialect via pathspec) -------------------------------------------

def test_glob_slashless_matches_any_depth_but_anchored_does_not():
    o = org("r", [node("a", ["Makefile"], mode="Leaf"),
                  node("b", ["**"], excludes=["Makefile"], mode="Leaf")])
    assert owners(o, "Makefile") == ["a"]
    assert owners(o, "sub/Makefile") == ["a"]        # slashless matches at any depth
    anchored = org("r", [node("a", ["/Makefile"], mode="Leaf"),
                         node("b", ["**"], excludes=["/Makefile"], mode="Leaf")])
    assert owners(anchored, "sub/Makefile") == ["b"]  # a leading slash anchors to the root


def test_glob_trailing_slash_matches_directory_contents():
    o = org("r", [node("a", ["build/"], mode="Leaf"),
                  node("b", ["**"], excludes=["build/"], mode="Leaf")])
    assert owners(o, "build/out.o") == ["a"]


def test_glob_character_class():
    o = org("r", [node("a", ["**/*.[ch]"], mode="Leaf"),
                  node("b", ["**"], excludes=["**/*.[ch]"], mode="Leaf")])
    assert owners(o, "src/main.c") == ["a"]
    assert owners(o, "src/main.h") == ["a"]
    assert owners(o, "src/main.py") == ["b"]


def test_exclude_not_recovered_is_unowned():
    o = org("r", [node("a", ["src/**"], excludes=["src/gen/**"], mode="Leaf")])  # nobody re-covers src/gen
    v = ov.check_coverage(o, ["src/x", "src/gen/y"])
    assert [(x["path"], x["rule"]) for x in v] == [("src/gen/y", "uncovered")], v


def test_tree_rejects_parent_missing_child_backref():
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


def test_domain_size_counts_only_owned_bytes(git_repo):
    repo = git_repo({"a/x.txt": "12345", "c/y.txt": "123"}, _AB)  # a owns 5 bytes, c owns 3
    m = ov.domain_size(_AB, "a", repo)
    assert m["files"] == 1 and m["bytes"] == 5, m
    assert m["est_tokens"] == 1, m  # 5 // 4


def test_cli_owner_exit_codes(git_repo):
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


def test_split_valid_root_split():
    old, new = _root_split()
    r = ov.check_split(old, new, ["a/x", "b/y", "root.txt"])
    assert r["status"] == "ok", r


def test_split_valid_second_generation():
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


def test_split_rejects_wrong_version():
    old, new = _root_split()
    new["version"] = 5  # must be old + 1
    r = ov.check_split(old, new)
    assert any("version must bump" in v["evidence"] for v in r["violations"]), r


def test_split_rejects_root_change():
    old, new = _root_split()
    new["root"] = "a"
    r = ov.check_split(old, new)
    assert any("root changed" in v["evidence"] for v in r["violations"]), r


def test_split_rejects_removed_node():
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


def test_split_rejects_non_leaf_child():
    old, new = _root_split()
    new["nodes"][1]["mode"] = "Parent"  # child 'a' must be a Leaf
    r = ov.check_split(old, new)
    assert any("must be a Leaf" in v["evidence"] for v in r["violations"]), r


def test_split_rejects_path_leaving_subtree():
    old, new = _root_split()  # new main domain is only root.txt; a/**, b/** cover the rest
    r = ov.check_split(old, new, ["a/x", "b/y", "orphan.txt"])  # orphan matches nothing new
    assert any("left the split subtree" in v["evidence"] for v in r["violations"]), r


def test_split_rejects_absorbing_a_previously_unowned_path():
    old = org("main", [node("main", ["owned/**"], parent=None, mode="Leaf")])
    old["version"] = 3
    new = {"version": 4, "root": "main", "nodes": [
        node("main", ["owned/shared.txt"], parent=None, children=["a", "b"], mode="Parent"),
        node("a", ["owned/a/**"], parent="main", mode="Leaf"),
        node("b", ["orphan/**"], parent="main", mode="Leaf"),
    ]}
    result = ov.check_split(old, new, ["owned/shared.txt", "owned/a/x", "orphan/new.py"])
    assert any(
        violation["evidence"] == "path orphan/new.py changed owner outside the split: [] -> ['b']"
        for violation in result["violations"]
    ), result


# --- preToolUse hook: warn/enforce classification + in-place identity + foreign-log ----------------

_HOOK_ORG = org("root", [node("root", ["shared/**"], parent=None, children=["a", "b"], mode="Parent"),
                         node("a", ["a/**"], parent="root", mode="Leaf"),
                         node("b", ["b/**"], parent="root", mode="Leaf")])


def _payload(tool, path, cwd="/repo", sid=None):
    p = {"toolName": tool, "toolArgs": {"path": path}, "cwd": cwd}
    if sid:
        p["sessionId"] = sid
    return p


def test_hook_allows_owned_write_no_foreign():
    d, f = ov.hook_decision(_payload("create", "/repo/a/new.py"), _HOOK_ORG, acting="a")
    assert d["permissionDecision"] == "allow" and f is None, (d, f)


def test_hook_warn_allows_but_flags_foreign():
    d, f = ov.hook_decision(_payload("edit", "/repo/b/x.py"), _HOOK_ORG, mode="warn", acting="a")
    assert d["permissionDecision"] == "allow", d          # warn: write is allowed (content preserved)
    assert f == {"path": "b/x.py", "owner": "b", "acting": "a", "disposition": "warn"}, f


def test_hook_enforce_denies_foreign():
    d, f = ov.hook_decision(_payload("edit", "/repo/b/x.py"), _HOOK_ORG, mode="enforce", acting="a")
    assert d["permissionDecision"] == "deny" and "owned by 'b'" in d["permissionDecisionReason"], d
    assert f is not None


def test_hook_flags_unowned():
    d, f = ov.hook_decision(_payload("create", "/repo/nowhere/x"), _HOOK_ORG, mode="warn", acting="a")
    assert d["permissionDecision"] == "allow" and f["owner"] is None, (d, f)
    d2, _ = ov.hook_decision(_payload("create", "/repo/nowhere/x"), _HOOK_ORG, mode="enforce", acting="a")
    assert d2["permissionDecision"] == "deny" and "UNOWNED" in d2["permissionDecisionReason"], d2


def test_hook_allows_non_write_tool():
    d, f = ov.hook_decision({"toolName": "glob", "toolArgs": {"pattern": "**/*"}, "cwd": "/repo"},
                            _HOOK_ORG, acting="a")
    assert d["permissionDecision"] == "allow" and f is None, (d, f)


def test_hook_never_infers_actor_from_shared_worktree_name():
    payload = _payload("create", r"D:\repo\.worktrees\a\b\new.txt", r"D:\repo\.worktrees\a")
    decision, foreign = ov.hook_decision(payload, _HOOK_ORG, acting="root")
    assert decision["permissionDecision"] == "deny"
    assert foreign["acting"] == "root" and foreign["owner"] == "b"


def test_hook_without_acting_reports_missing_identity():
    owned, f1 = ov.hook_decision(_payload("create", "/repo/b/x.py"), _HOOK_ORG, acting=None)
    assert owned["permissionDecision"] == "deny" and "identity" in owned["permissionDecisionReason"]
    unowned, f2 = ov.hook_decision(_payload("create", "/repo/z/x"), _HOOK_ORG, acting=None)
    assert unowned["permissionDecision"] == "deny"


# --- in-place identity: record_acting (userPromptSubmitted) + preToolUse resolves + logs foreign -----

def test_record_acting_and_resolve_roundtrip(git_repo):
    repo = git_repo({"a/keep.txt": "x", "b/keep.txt": "x", "shared/keep.txt": "x"}, _HOOK_ORG)
    ov.record_acting({"sessionId": "S1", "cwd": str(repo), "prompt": "AgentOrgActingNode: a\nDo work"})
    assert ov._acting_from_map({"sessionId": "S1", "cwd": str(repo)}) == "a"
    assert ov._acting_from_map({"sessionId": "unknown", "cwd": str(repo)}) is None


def test_hook_cli_warn_allows_and_logs_foreign(git_repo):
    repo = git_repo({"a/keep.txt": "x", "b/keep.txt": "x", "shared/keep.txt": "x"}, _HOOK_ORG)
    # record 'a' as the acting node for session S2, then S2 writes into b/ (foreign)
    ov.record_acting({"sessionId": "S2", "cwd": str(repo), "prompt": "AgentOrgActingNode: a"})
    pl = json.dumps(_payload("edit", str(repo / "b" / "x.py"), str(repo), sid="S2"))
    p = subprocess.run([sys.executable, str(TOOL), "--hook", "--org", str(repo / "org.json")],
                       input=pl, capture_output=True, text=True)
    assert json.loads(p.stdout)["permissionDecision"] == "allow", p.stdout   # warn = allow
    log = repo / ".git" / "agent-org" / "foreign" / "S2" / "S2.jsonl"
    assert log.exists(), "foreign write must be logged"
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["path"] == "b/x.py" and entry["owner"] == "b" and entry["acting"] == "a", entry
    assert entry["sessionId"] == "S2", entry


def test_hook_cli_enforce_denies_foreign(git_repo):
    repo = git_repo({"a/keep.txt": "x", "b/keep.txt": "x", "shared/keep.txt": "x"}, _HOOK_ORG)
    ov.record_acting({"sessionId": "S3", "cwd": str(repo), "prompt": "AgentOrgActingNode: a"})
    pl = json.dumps(_payload("edit", str(repo / "b" / "x.py"), str(repo), sid="S3"))
    p = subprocess.run([sys.executable, str(TOOL), "--hook", "--mode", "enforce", "--org", str(repo / "org.json")],
                       input=pl, capture_output=True, text=True)
    assert json.loads(p.stdout)["permissionDecision"] == "deny", p.stdout


def test_hook_cli_allows_when_no_org(tmp_path):
    # a non-agent-org repo (no org.json) must never be disturbed by the plugin hook
    d = tmp_path
    pl = json.dumps(_payload("create", str(d / "anything.txt"), str(d)))
    p = subprocess.run([sys.executable, str(TOOL), "--hook", "--org", str(d / "org.json")],
                       input=pl, capture_output=True, text=True)
    assert json.loads(p.stdout) == {}, p.stdout


# --- usage log + split-advice (parent's over-burden signal) -----------------------------------------

def test_usage_record_and_split_advice_over_by_usage(git_repo):
    repo = git_repo({"a/small.txt": "x", "b/keep.txt": "x", "shared/keep.txt": "x"}, _HOOK_ORG)
    # 'a' owns a tiny domain, but a session burned a lot of tokens -> over threshold by usage
    ov.usage_record(str(repo), "a", 100)
    ov.usage_record(str(repo), "a", 130000)  # peak
    adv = ov.split_advice(_HOOK_ORG, "a", str(repo), window=200000, threshold=0.60)  # limit = 120000
    assert adv["peak_session_tokens"] == 130000, adv
    assert adv["recommend_split"] is True and any("peak session" in r for r in adv["reasons"]), adv


def test_split_advice_not_over_when_small(git_repo):
    repo = git_repo({"a/small.txt": "x", "b/keep.txt": "x", "shared/keep.txt": "x"}, _HOOK_ORG)
    ov.usage_record(str(repo), "a", 5000)
    adv = ov.split_advice(_HOOK_ORG, "a", str(repo), window=200000, threshold=0.60)
    assert adv["recommend_split"] is False, adv


def test_usage_record_cli_appends_log(git_repo):
    repo = git_repo({"a/x.txt": "x", "b/keep.txt": "x", "shared/keep.txt": "x"}, _HOOK_ORG)
    subprocess.run([sys.executable, str(TOOL), "--usage-record", "a", "--tokens", "150000", "--root", str(repo)],
                   capture_output=True, text=True)
    log = repo / ".git" / "agent-org" / "usage" / "a.jsonl"
    assert log.exists() and json.loads(log.read_text(encoding="utf-8").splitlines()[0])["tokens"] == 150000
