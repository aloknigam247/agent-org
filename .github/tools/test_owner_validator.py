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

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import owner_validator as ov  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


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
    o = org("root", [node("root", ["**"], parent=None, children=["c1", "c2"], mode="Parent"),
                     node("c1", ["one/**"], parent="root", mode="Leaf"),
                     node("c2", ["two/**"], parent="root", mode="Leaf")])
    assert ov.check_tree(o) == []


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


def run():
    failed = 0
    for fn in CASES:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
