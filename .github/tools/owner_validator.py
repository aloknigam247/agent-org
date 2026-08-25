"""owner-oracle: the deterministic coverage validator for agentOrg.

Computes ``owner(path)`` for a git repository's tracked files against ``org.json`` and reports
coverage violations. This is the single source of truth for coverage — the ``splitter`` calls it
pre-commit over a proposed tree, and the integration gate calls it over each change's diff. Coverage
is never eyeballed. See ``.github/org-design.md`` §2.2 and §2.7.

Pinned glob dialect: **gitignore semantics** (via ``pathspec``).
- ``dir/**`` matches everything under ``dir``; ``**/Name`` matches ``Name`` in any directory; a bare
  ``**`` matches everything.
- Matching is case-sensitive; dotfiles are matched like any other path.
- Paths come from ``git ls-files`` (files only), normalized to forward-slash and repo-root-relative,
  so behaviour is identical on Windows and POSIX.

Ownership rule: ``owner(path)`` = the single node whose *effective domain* (``domain`` minus
``excludes``) matches ``path``. Exactly one node must match — zero is ``UNOWNED`` (``uncovered``),
more than one is ``overlap``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:  # pathspec >= 0.11 exposes GitIgnoreSpec; older versions use the from_lines factory.
    from pathspec import GitIgnoreSpec

    def _spec(globs):
        return GitIgnoreSpec.from_lines(list(globs))
except ImportError:  # pragma: no cover - version fallback
    try:
        from pathspec import PathSpec

        def _spec(globs):
            return PathSpec.from_lines("gitwildmatch", list(globs))
    except ImportError:  # pragma: no cover - dependency missing
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "pathspec is required: pip install -r .github/tools/requirements.txt",
                }
            )
        )
        sys.exit(2)


def normalize(path: str) -> str:
    """Normalize a path to the oracle's canonical form: forward-slash, no leading './'."""
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _match(spec, path: str) -> bool:
    return bool(spec.match_file(path)) if spec is not None else False


def compile_nodes(nodes):
    """Pre-compile each node's domain/excludes into pathspec matchers (once per validate call)."""
    compiled = []
    for node in nodes:
        charter = node.get("charter", {})
        domain = charter.get("domain") or []
        excludes = charter.get("excludes") or []
        compiled.append(
            {
                "id": node["id"],
                "domain": _spec(domain) if domain else None,
                "excludes": _spec(excludes) if excludes else None,
            }
        )
    return compiled


def owners_of(compiled, path: str):
    """Return the list of node ids whose effective domain matches ``path`` (should be exactly one)."""
    path = normalize(path)
    hits = []
    for node in compiled:
        if _match(node["domain"], path) and not _match(node["excludes"], path):
            hits.append(node["id"])
    return hits


def check_tree(org):
    """Structural invariants: single root, valid back-references, arity, acyclicity, unique ids."""
    violations = []
    nodes = org.get("nodes", [])
    by_id = {}
    for node in nodes:
        if node["id"] in by_id:
            violations.append({"rule": "tree", "node": node["id"], "evidence": "duplicate id"})
        by_id[node["id"]] = node

    roots = [n for n in nodes if n.get("parent") is None]
    if len(roots) != 1:
        violations.append({"rule": "tree", "node": None, "evidence": f"expected exactly 1 root, found {len(roots)}"})
    if org.get("root") not in by_id:
        violations.append({"rule": "tree", "node": org.get("root"), "evidence": "root id not among nodes"})
    elif roots and roots[0]["id"] != org.get("root"):
        violations.append({"rule": "tree", "node": org.get("root"), "evidence": "root field disagrees with parent==null node"})

    for node in nodes:
        nid = node["id"]
        kids = node.get("children", [])
        parent = node.get("parent")
        if parent is not None and parent not in by_id:
            violations.append({"rule": "tree", "node": nid, "evidence": f"unknown parent {parent}"})
        for child in kids:
            if child not in by_id:
                violations.append({"rule": "tree", "node": nid, "evidence": f"unknown child {child}"})
            elif by_id[child].get("parent") != nid:
                violations.append({"rule": "tree", "node": nid, "evidence": f"child {child} back-reference mismatch"})
        if node.get("mode") == "Leaf" and kids:
            violations.append({"rule": "tree", "node": nid, "evidence": "Leaf has children"})
        if node.get("mode") == "Parent" and len(kids) < 2:
            violations.append({"rule": "tree", "node": nid, "evidence": "Parent has fewer than 2 children"})

    for node in nodes:  # acyclicity via the parent chain
        seen = set()
        cur = node
        while cur is not None and cur.get("parent") is not None:
            if cur["id"] in seen:
                violations.append({"rule": "tree", "node": node["id"], "evidence": "cycle in parent chain"})
                break
            seen.add(cur["id"])
            cur = by_id.get(cur["parent"])
    return violations


def check_coverage(org, paths):
    """Exactly-one-owner over the given path set: 0 owners = uncovered (UNOWNED), >1 = overlap."""
    violations = []
    compiled = compile_nodes(org.get("nodes", []))
    for path in paths:
        hits = owners_of(compiled, path)
        if len(hits) == 0:
            violations.append({"rule": "uncovered", "path": normalize(path), "evidence": "UNOWNED: matches no node's effective domain"})
        elif len(hits) > 1:
            violations.append({"rule": "overlap", "path": normalize(path), "evidence": f"owned by {hits}"})
    return violations


def validate(org, paths):
    """Full verdict: structural tree checks plus exactly-one-owner coverage over ``paths``."""
    violations = check_tree(org) + check_coverage(org, paths)
    return {"status": "ok" if not violations else "violations", "violations": violations}


def git_tracked(root):
    """Tracked plus untracked-non-ignored files (forward-slash), so a just-created unowned file is
    caught rather than silently missed (design §2.7)."""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [normalize(line) for line in out.stdout.splitlines() if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(description="agentOrg owner-oracle / coverage validator")
    parser.add_argument("--org", default="org.json", help="path to org.json")
    parser.add_argument("--root", default=".", help="repo root for git ls-files")
    parser.add_argument("--paths", nargs="*", help="explicit paths to check instead of git ls-files")
    parser.add_argument("--owner", help="print the owner of a single path and exit")
    args = parser.parse_args(argv)

    org = json.loads(Path(args.org).read_text(encoding="utf-8"))

    if args.owner:
        hits = owners_of(compile_nodes(org["nodes"]), args.owner)
        owner = hits[0] if len(hits) == 1 else None
        print(json.dumps({"path": normalize(args.owner), "owner": owner, "matches": hits}))
        return 0 if owner else 1

    paths = [normalize(p) for p in args.paths] if args.paths else git_tracked(args.root)
    result = validate(org, paths)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
