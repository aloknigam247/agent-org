"""owner-oracle: the deterministic coverage validator for agent-org.

Computes ``owner(path)`` for a git repository's tracked files against ``org.json`` and reports
coverage violations. This is the single source of truth for coverage — the ``splitter`` calls it
pre-commit over a proposed tree, and the integration gate calls it over each change's diff. Coverage
is never eyeballed. See the agent-org-design skill (§2.2, §2.7).

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
import os
import re
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
                    "message": "pathspec is required: pip install -r requirements.txt",
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
        elif parent is not None and nid not in (by_id[parent].get("children") or []):
            violations.append({"rule": "tree", "node": nid,
                               "evidence": f"parent {parent} does not list it as a child"})
        for child in kids:
            if child not in by_id:
                violations.append({"rule": "tree", "node": nid, "evidence": f"unknown child {child}"})
            elif by_id[child].get("parent") != nid:
                violations.append({"rule": "tree", "node": nid, "evidence": f"child {child} back-reference mismatch"})
        if node.get("mode") == "Leaf" and kids:
            violations.append({"rule": "tree", "node": nid, "evidence": "Leaf has children"})
        if node.get("mode") == "Parent" and len(kids) < 2:
            violations.append({"rule": "tree", "node": nid, "evidence": "Parent has fewer than 2 children"})
        if node.get("mode") == "Parent" and "**" in (node.get("charter", {}).get("domain") or []):
            violations.append({"rule": "tree", "node": nid,
                               "evidence": "Parent domain is ** (must be an explicit shared set, §2.2)"})

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


def check_containment(org, acting, paths):
    """Integration gate (s1b): assert every changed path is owned by the acting node."""
    violations = []
    compiled = compile_nodes(org.get("nodes", []))
    for path in paths:
        hits = owners_of(compiled, path)
        if len(hits) == 0:
            violations.append({"rule": "uncovered", "path": normalize(path), "evidence": "UNOWNED: matches no node's effective domain"})
        elif len(hits) > 1:
            violations.append({"rule": "overlap", "path": normalize(path), "evidence": f"owned by {hits}"})
        elif hits[0] != acting:
            violations.append({"rule": "containment", "path": normalize(path), "acting": acting, "owner": hits[0], "evidence": f"changed a path owned by {hits[0]}, not acting node {acting}"})
    return {"status": "ok" if not violations else "violations", "violations": violations}


def check_split(old_org, new_org, paths=None):
    """Validate a single add-children split (design §2.3, §3.6): exactly one former Leaf became a Parent
    with >= 2 new Leaf children, root and version move correctly, no pre-existing node is
    reparented/renamed/otherwise changed, and (given the tracked paths) nothing leaves the split subtree.
    Pure old-vs-new comparison, so a split can be graded without running the splitter agent."""
    violations = []
    old_by = {n["id"]: n for n in old_org.get("nodes", [])}
    new_by = {n["id"]: n for n in new_org.get("nodes", [])}

    def add(evidence, node=None):
        item = {"rule": "split", "evidence": evidence}
        if node:
            item["node"] = node
        violations.append(item)

    if old_org.get("root") != new_org.get("root"):
        add(f"root changed: {old_org.get('root')} -> {new_org.get('root')}")
    if new_org.get("version") != (old_org.get("version", 0) + 1):
        add(f"version must bump by exactly 1: {old_org.get('version')} -> {new_org.get('version')}")

    removed = [nid for nid in old_by if nid not in new_by]
    if removed:
        add(f"pre-existing nodes removed (no reparent/rename in a split): {sorted(removed)}")
    added = [nid for nid in new_by if nid not in old_by]

    targets = [nid for nid in new_by if nid in old_by
               and old_by[nid].get("mode") == "Leaf" and new_by[nid].get("mode") == "Parent"]
    if len(targets) != 1:
        add(f"a split turns exactly one Leaf into a Parent, found {sorted(targets)}")
        return {"status": "ok" if not violations else "violations", "violations": violations}
    target = targets[0]

    if len(added) < 2:
        add(f"a split adds >= 2 new children, found {len(added)}")
    for nid in added:
        if new_by[nid].get("mode") != "Leaf":
            add("new child must be a Leaf", nid)
        if new_by[nid].get("parent") != target:
            add(f"new child's parent must be the split target {target}", nid)
    if set(new_by[target].get("children") or []) != set(added):
        add(f"target children {sorted(new_by[target].get('children') or [])} != added {sorted(added)}", target)

    for nid, old_n in old_by.items():  # every pre-existing non-target node must be untouched
        if nid == target or nid not in new_by:
            continue
        for field in ("charter", "parent", "children", "mode"):
            if old_n.get(field) != new_by[nid].get(field):
                add(f"unrelated node changed field {field!r}", nid)

    if paths is not None:  # nothing may leave the split subtree; owners outside it are unchanged
        old_c, new_c = compile_nodes(old_org.get("nodes", [])), compile_nodes(new_org.get("nodes", []))
        allowed_new = {target, *added}
        for path in paths:
            oo, no = owners_of(old_c, path), owners_of(new_c, path)
            o1 = oo[0] if len(oo) == 1 else None
            if o1 == target:
                if not (len(no) == 1 and no[0] in allowed_new):
                    add(f"path {normalize(path)} left the split subtree: {oo} -> {no}", target)
            elif no != oo:
                add(f"path {normalize(path)} changed owner outside the split: {oo} -> {no}")
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


def git_changed(root):
    """Paths changed vs HEAD plus untracked-non-ignored, for the integration gate."""
    changed = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "HEAD"], capture_output=True, text=True, check=True)
    untracked = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True, check=True)
    lines = changed.stdout.splitlines() + untracked.stdout.splitlines()
    return [normalize(line) for line in lines if line.strip()]


def domain_size(org, acting, root):
    """Offline domain-size proxy for the split self-check (s10): bytes and est-tokens of the files
    owned by ``acting``. est-tokens ~= bytes/4 (a rough proxy, not an exact count)."""
    compiled = compile_nodes(org.get("nodes", []))
    owned, total_bytes = [], 0
    for path in git_tracked(root):
        hits = owners_of(compiled, path)
        if len(hits) == 1 and hits[0] == acting:
            owned.append(path)
            try:
                total_bytes += (Path(root) / path).stat().st_size
            except OSError:
                pass
    return {"node": acting, "files": len(owned), "bytes": total_bytes, "est_tokens": total_bytes // 4}


# pre-tool-use containment hook + the in-place identity / foreign-log machinery.
HOOK_WRITE_TOOLS = {"create", "edit", "str_replace", "write", "apply_patch", "multi_edit"}
_WT_RE = re.compile(r"\.worktrees/([^/]+)/[^/]+/(.+)")
MARKER_RE = re.compile(r"AgentOrgActingNode:\s*(\S+)")


def _allow():
    return {"permissionDecision": "allow"}


def _state_dir(cwd, sub):
    """The `.git/agent-org/<sub>/` state dir, shared across worktrees via --git-common-dir. Lives inside
    `.git`, so it is never tracked (non-invasive). Returns None outside a git repo."""
    try:
        out = subprocess.run(["git", "-C", str(cwd or "."), "rev-parse", "--git-common-dir"],
                             capture_output=True, text=True)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    g = Path(out.stdout.strip())
    if not g.is_absolute():
        g = Path(cwd or ".") / g
    d = g / "agent-org" / sub
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    return d


def record_acting(payload):
    """userPromptSubmitted hook: parse the parent-injected `AgentOrgActingNode: <id>` marker from the
    prompt and persist sessionId -> node, so preToolUse can attribute in-place writes to the acting node
    (in-place has no worktree path to attribute by)."""
    sid = payload.get("sessionId")
    m = MARKER_RE.search(payload.get("prompt") or "")
    if not (sid and m):
        return
    d = _state_dir(payload.get("cwd"), "acting")
    if d:
        try:
            (d / sid).write_text(m.group(1), encoding="utf-8")
        except Exception:
            pass


def _acting_from_map(payload):
    sid = payload.get("sessionId")
    if not sid:
        return None
    d = _state_dir(payload.get("cwd"), "acting")
    f = (d / sid) if d else None
    if f and f.exists():
        try:
            return f.read_text(encoding="utf-8").strip() or None
        except Exception:
            return None
    return None


def hook_decision(payload, org, mode="warn", acting=None):
    """Classify a preToolUse write. Returns (decision, foreign): `foreign` is {path, owner, acting} when
    the write is UNOWNED or outside the acting node's domain, else None. `enforce` mode denies a foreign
    write; `warn` mode allows it (so its content is preserved on disk for reroute) and the caller logs
    `foreign`. The acting node comes from a `.worktrees/<id>/` path prefix, else the caller's `acting`."""
    if (payload.get("toolName") or "") not in HOOK_WRITE_TOOLS:
        return _allow(), None
    args = payload.get("toolArgs") or {}
    path = args.get("path") or args.get("file_path") or args.get("filename")
    if not path:
        return _allow(), None
    try:
        rel = normalize(os.path.relpath(path, payload.get("cwd") or "."))
    except ValueError:  # different drive on Windows — outside the repo
        return _allow(), None
    if rel.startswith("../") or rel.startswith("/"):
        return _allow(), None
    m = _WT_RE.match(rel)
    if m:
        acting, rel = m.group(1), m.group(2)
    hits = owners_of(compile_nodes(org.get("nodes", [])), rel)
    owner = hits[0] if len(hits) == 1 else None
    if owner is None:
        foreign, reason = {"path": rel, "owner": None, "acting": acting}, \
            f"agent-org: '{rel}' is UNOWNED or multiply-owned"
    elif acting and owner != acting:
        foreign, reason = {"path": rel, "owner": owner, "acting": acting}, \
            f"agent-org: '{rel}' is owned by '{owner}', not the acting node '{acting}'"
    else:
        return _allow(), None
    if mode == "enforce":
        return {"permissionDecision": "deny", "permissionDecisionReason": reason}, foreign
    return _allow(), foreign


def _log_foreign(payload, foreign):
    """Append a foreign write to `.git/agent-org/foreign/<acting-node>.jsonl` — keyed by the acting node
    so the parent (which knows which child it dispatched) can read it at reconciliation without knowing
    the child's session id. Falls back to the session id when the acting node is unknown."""
    key = foreign.get("acting") or payload.get("sessionId") or "unknown"
    d = _state_dir(payload.get("cwd"), "foreign")
    if d:
        try:
            entry = dict(foreign, sessionId=payload.get("sessionId"))
            with (d / f"{key}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception:
            pass


def _run_hook(org_path, mode="warn"):
    """preToolUse hook entry: read a payload on stdin, classify, log a foreign write (warn mode), print
    the decision. Fail open (allow) on any error or when the repo is not agent-org-managed."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        print(json.dumps(_allow()))
        return 0
    if not org_path.exists():
        print(json.dumps(_allow()))
        return 0
    try:
        org = json.loads(org_path.read_text(encoding="utf-8"))
    except Exception:
        print(json.dumps(_allow()))
        return 0
    acting = _acting_from_map(payload) or os.environ.get("AGENT_ORG_ACTING")
    decision, foreign = hook_decision(payload, org, mode=mode, acting=acting)
    if foreign is not None:
        _log_foreign(payload, foreign)
    print(json.dumps(decision))
    return 0


def _run_record_acting():
    """userPromptSubmitted hook entry: read a payload on stdin and record the sessionId -> node marker."""
    try:
        record_acting(json.loads(sys.stdin.read() or "{}"))
    except Exception:
        pass
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="agent-org owner-oracle / coverage validator")
    parser.add_argument("--org", default="org.json", help="path to org.json")
    parser.add_argument("--root", default=".", help="repo root for git ls-files")
    parser.add_argument("--paths", nargs="*", help="explicit paths to check instead of git ls-files")
    parser.add_argument("--owner", help="print the owner of a single path and exit")
    parser.add_argument("--acting", help="integration-gate containment: assert changed paths are owned by this node id")
    parser.add_argument("--size", help="split self-check: report the domain-size proxy for this node id")
    parser.add_argument("--split-baseline",
                        help="validate a split: compare this old org.json against --org (the new tree)")
    parser.add_argument("--window", type=int, default=200000, help="context window in tokens (default 200000)")
    parser.add_argument("--threshold", type=float, default=0.60, help="split fraction of the window (default 0.60)")
    parser.add_argument("--hook", action="store_true",
                        help="preToolUse hook: read a tool payload on stdin, print an allow/deny decision")
    parser.add_argument("--mode", choices=["warn", "enforce"], default="warn",
                        help="preToolUse hook mode: warn (allow + log a foreign write) or enforce (deny)")
    parser.add_argument("--record-acting", action="store_true",
                        help="userPromptSubmitted hook: read a payload on stdin, record sessionId -> node")
    args = parser.parse_args(argv)

    if args.record_acting:
        return _run_record_acting()

    if args.hook:  # handled before the unconditional org load below (must allow when org.json is absent)
        return _run_hook(Path(args.org), mode=args.mode)

    org = json.loads(Path(args.org).read_text(encoding="utf-8"))

    if args.owner:
        hits = owners_of(compile_nodes(org["nodes"]), args.owner)
        owner = hits[0] if len(hits) == 1 else None
        print(json.dumps({"path": normalize(args.owner), "owner": owner, "matches": hits}))
        return 0 if owner else 1

    if args.split_baseline:
        old = json.loads(Path(args.split_baseline).read_text(encoding="utf-8"))
        try:
            paths = [normalize(p) for p in args.paths] if args.paths else git_tracked(args.root)
        except Exception:
            paths = None
        result = check_split(old, org, paths)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "ok" else 1

    if args.acting:
        node_ids = {n["id"] for n in org["nodes"]}
        if args.acting not in node_ids:
            print(json.dumps({"status": "error", "message": f"unknown acting node {args.acting!r}"}))
            return 2
        paths = [normalize(p) for p in args.paths] if args.paths else git_changed(args.root)
        result = check_containment(org, args.acting, paths)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "ok" else 1

    if args.size:
        node_ids = {n["id"] for n in org["nodes"]}
        if args.size not in node_ids:
            print(json.dumps({"status": "error", "message": f"unknown node {args.size!r}"}))
            return 2
        m = domain_size(org, args.size, args.root)
        fraction = m["est_tokens"] / args.window if args.window else 0
        m.update({"window": args.window, "threshold": args.threshold, "fraction": round(fraction, 3), "over_threshold": fraction >= args.threshold})
        print(json.dumps(m, indent=2))
        return 0

    paths = [normalize(p) for p in args.paths] if args.paths else git_tracked(args.root)
    result = validate(org, paths)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
