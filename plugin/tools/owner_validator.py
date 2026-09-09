"""owner-oracle: the deterministic coverage validator for agent-org.

Computes ``owner(path)`` for repository-root-relative files against ``.github/agent-org/org.json`` and reports
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
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ORG_PATH = ".github/agent-org/org.json"

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


def _org_file(root, *, explicit=None, allow_uninstalled=False):
    selected = Path(root).resolve() / (ORG_PATH if explicit is None else explicit)
    if selected.is_file() or allow_uninstalled:
        return selected
    label = "canonical organization" if explicit is None else "organization candidate"
    raise FileNotFoundError(f"Missing {label}: {selected}")


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


def managed(org, path):
    """The managed scope survives splits; a Parent's retained domain is not the scope."""
    return _match(_spec(org.get("scope", ["**"])), normalize(path))


def ownership(org, path):
    path = normalize(path)
    in_scope = managed(org, path)
    hits = owners_of(compile_nodes(org.get("nodes", [])), path) if in_scope else []
    status = "owned" if len(hits) == 1 else "overlap" if hits else "unowned"
    if not in_scope:
        status = "unmanaged"
    return {"path": path, "owner": hits[0] if len(hits) == 1 else None, "matches": hits, "status": status}


def is_descendant(org, node_id, ancestor_id):
    by_id = {n["id"]: n for n in org["nodes"]}
    seen = set()
    current = by_id.get(node_id)
    while current and current.get("parent") is not None:
        parent = current["parent"]
        if parent == ancestor_id:
            return True
        if parent in seen:
            raise ValueError("cycle in org ancestry")
        seen.add(parent)
        current = by_id.get(parent)
    return False


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
        if len(kids) != len(set(kids)):
            violations.append({"rule": "tree", "node": nid, "evidence": "duplicate child id"})
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


_COVERAGE_ONLY = object()


def _check_paths(org, paths, acting=_COVERAGE_ONLY):
    violations = []
    compiled = compile_nodes(org.get("nodes", []))
    for path in paths:
        if not managed(org, path):
            continue
        hits = owners_of(compiled, path)
        if not hits:
            violations.append({"rule": "uncovered", "path": normalize(path),
                               "evidence": "UNOWNED: matches no node's effective domain"})
        elif len(hits) > 1:
            violations.append({"rule": "overlap", "path": normalize(path), "evidence": f"owned by {hits}"})
        elif acting is not _COVERAGE_ONLY and hits[0] != acting:
            violations.append({"rule": "containment", "path": normalize(path), "acting": acting, "owner": hits[0],
                               "evidence": f"changed a path owned by {hits[0]}, not acting node {acting}"})
    return violations


def check_coverage(org, paths):
    """Exactly-one-owner over the given path set: 0 owners = uncovered (UNOWNED), >1 = overlap."""
    return _check_paths(org, paths)


def validate(org, paths):
    """Full verdict: structural tree checks plus exactly-one-owner coverage over ``paths``."""
    violations = check_tree(org) + check_coverage(org, paths)
    return {"status": "ok" if not violations else "violations", "violations": violations}


def check_containment(org, acting, paths):
    """Integration gate (s1b): assert every changed path is owned by the acting node."""
    violations = _check_paths(org, paths, acting)
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
    for field, default in (("collaboration", "agents"), ("scope", ["**"]), ("storage", "local")):
        if old_org.get(field, default) != new_org.get(field, default):
            add(f"a split must not change {field}")

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
    if new_by[target].get("parent") != old_by[target].get("parent"):
        add("split target was reparented", target)

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
            if not managed(old_org, path):
                continue
            oo, no = owners_of(old_c, path), owners_of(new_c, path)
            o1 = oo[0] if len(oo) == 1 else None
            if o1 == target:
                if not (len(no) == 1 and no[0] in allowed_new):
                    add(f"path {normalize(path)} left the split subtree: {oo} -> {no}", target)
            elif no != oo:
                add(f"path {normalize(path)} changed owner outside the split: {oo} -> {no}")
    return {"status": "ok" if not violations else "violations", "violations": violations}


def git_output(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def git_root(cwd):
    result = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode:
        return None
    return Path(result.stdout.strip()).resolve()


def git_common_dir(root):
    return Path(git_output(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve()


def git_tracked(root):
    """Tracked plus untracked-non-ignored files (forward-slash), so a just-created unowned file is
    caught rather than silently missed (design §2.7)."""
    out = git_output(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({normalize(name) for name in out.split("\0") if name})


def git_changed(root, baseline="HEAD"):
    """Include committed and working changes, with both rename endpoints and untracked files."""
    changed = git_output(root, "diff", "--name-only", "-z", "--no-renames", baseline)
    untracked = git_output(root, "ls-files", "-z", "--others", "--exclude-standard")
    return sorted({normalize(name) for name in (changed + untracked).split("\0") if name})


def domain_size(org, acting, root):
    """Offline domain-size proxy for the split self-check (s10): bytes and est-tokens of the files
    owned by ``acting``. est-tokens ~= bytes/4 (a rough proxy, not an exact count)."""
    compiled = compile_nodes(org.get("nodes", []))
    owned, total_bytes = [], 0
    for path in git_tracked(root):
        if not managed(org, path):
            continue
        hits = owners_of(compiled, path)
        if len(hits) == 1 and hits[0] == acting:
            owned.append(path)
            file = Path(root) / path
            if file.is_file():
                total_bytes += file.stat().st_size
    return {"node": acting, "files": len(owned), "bytes": total_bytes, "est_tokens": total_bytes // 4}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4()}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot(org, root):
    files = {}
    for rel in git_tracked(root):
        file = Path(root) / rel
        if managed(org, rel) and file.is_file():
            files[rel] = hashlib.sha256(file.read_bytes()).hexdigest()
    return files


def checkpoint(org, root):
    write_json(git_common_dir(root) / "agent-org" / "checkpoint.json", _snapshot(org, root))


def drift(org, root):
    file = git_common_dir(root) / "agent-org" / "checkpoint.json"
    if not file.exists():
        return {"status": "uninitialized", "changes": [], "message": "No reconciled checkpoint exists yet."}
    previous = json.loads(file.read_text(encoding="utf-8"))
    current = _snapshot(org, root)
    changes = []
    for rel in sorted(previous.keys() | current.keys()):
        if previous.get(rel) == current.get(rel):
            continue
        change = "added" if rel not in previous else "removed" if rel not in current else "modified"
        changes.append({**ownership(org, rel), "change": change})
    return {"status": "drift" if changes else "ok", "changes": changes}


HOOK_WRITE_TOOLS = {
    "apply_patch", "create", "delete", "edit", "multi_edit", "str_replace", "str_replace_editor", "write",
}
MARKER_RE = re.compile(r"\AAgentOrgActingNode:[ \t]*([a-z][a-z0-9-]*)[ \t]*(?:\r?\n|$)")
STATE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*\Z")


def _allow():
    return {"permissionDecision": "allow"}


def _state_dir(cwd, sub):
    root = git_root(cwd or ".")
    if root is None:
        return None
    d = git_common_dir(root) / "agent-org" / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_id(value):
    if (
        not isinstance(value, str)
        or not STATE_ID_RE.fullmatch(value)
        or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", value, re.IGNORECASE)
    ):
        raise ValueError("session and node state identifiers must contain only letters, digits and hyphens")
    return value


def prompt_context(prompt):
    marker = MARKER_RE.match(prompt)
    if marker is None:
        return None
    context = {"node": marker.group(1)}
    for name, key in (
        ("AgentOrgRunId", "run_id"),
        ("AgentOrgWorktree", "worktree"),
        ("ParentAgentSessionId", "parent_session_id"),
    ):
        match = re.search(rf"^{name}:[ \t]*(.+?)[ \t]*\r?$", prompt, re.MULTILINE)
        if match:
            context[key] = match.group(1)
    for key in ("run_id", "parent_session_id"):
        if key in context:
            state_id(context[key])
    if "worktree" in context:
        context["worktree"] = str(Path(context["worktree"]).resolve())
    return context


def record_acting(payload):
    sid = payload.get("sessionId")
    context = prompt_context(payload.get("prompt") or "")
    if not sid or context is None:
        return None
    state_id(sid)
    root = git_root(payload.get("cwd") or ".")
    if root is None:
        return None
    d = _state_dir(payload.get("cwd"), "acting")
    file = d / sid
    if file.exists():
        previous = json.loads(file.read_text(encoding="utf-8"))
        for key in ("node", "parent_session_id", "run_id", "worktree"):
            if key in previous and key in context and previous[key] != context[key]:
                raise ValueError(f"a session cannot change its bound {key}")
        context = {**previous, **context}
    root, context = hook_workspace(payload, context)
    selected = _org_file(root, allow_uninstalled=True)
    if not selected.exists() and not (root / ".github/agent-org").exists():
        return None
    org = json.loads(selected.read_text(encoding="utf-8-sig"))
    if context["node"] not in {n["id"] for n in org["nodes"]} | {"splitter"}:
        raise ValueError(f"unknown acting node {context['node']!r}")
    context.setdefault("run_id", sid)
    write_json(file, context)
    return context


def _context_from_map(payload):
    sid = payload.get("sessionId")
    if not sid:
        return {}
    state_id(sid)
    d = _state_dir(payload.get("cwd"), "acting")
    f = (d / sid) if d else None
    if f and f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {}


def _acting_from_map(payload):
    return _context_from_map(payload).get("node")


def _write_paths(payload):
    args = payload.get("toolArgs") or {}
    if isinstance(args, str):
        if payload.get("toolName") == "apply_patch" and args.startswith("*** Begin Patch"):
            args = {"input": args}
        else:
            args = json.loads(args)
    if not isinstance(args, dict):
        raise ValueError("file-tool arguments must be an object")
    paths = [args[key] for key in ("path", "file_path", "filename") if isinstance(args.get(key), str)]
    for edit in args.get("edits", []):
        if isinstance(edit, dict):
            paths.extend(edit[key] for key in ("path", "file_path") if isinstance(edit.get(key), str))
    if payload.get("toolName") == "apply_patch":
        patch = args.get("input", args.get("patch", ""))
        paths.extend(re.findall(r"^\*\*\* (?:Add File|Delete File|Update File|Move to): (.+)$", patch, re.MULTILINE))
    return list(dict.fromkeys(paths))


def classify_write(payload, org, mode="warn", acting=None, root=None):
    if payload.get("toolName") not in HOOK_WRITE_TOOLS:
        return _allow(), []
    paths = _write_paths(payload)
    if not paths:
        return {"permissionDecision": "deny", "permissionDecisionReason": "agent-org: no file path to classify"}, []
    cwd = Path(payload.get("cwd") or ".").resolve()
    root = Path(root or cwd).resolve()
    records, denied = [], []
    for raw in paths:
        target = Path(raw)
        target = (target if target.is_absolute() else cwd / target).resolve()
        try:
            rel = target.relative_to(root).as_posix()
        except ValueError:
            denied.append(f"{raw}: outside the session workspace")
            continue
        result = ownership(org, rel)
        if result["status"] == "unmanaged":
            continue
        if acting not in {n["id"] for n in org["nodes"]} | {"splitter"}:
            denied.append(f"{rel}: missing or invalid acting-node identity")
            continue
        owner = result["owner"]
        if owner == acting:
            continue
        descendant = owner is not None and is_descendant(org, owner, acting)
        disposition = "deny" if descendant or mode == "enforce" else "warn"
        record = {"path": rel, "owner": owner, "acting": acting, "disposition": disposition}
        records.append(record)
        if descendant:
            denied.append(f"{rel}: owned by descendant '{owner}'; '{acting}' must delegate")
        elif mode == "enforce":
            denied.append(f"{rel}: owned by '{owner}' (UNOWNED if null), not '{acting}'")
    if denied:
        return {"permissionDecision": "deny", "permissionDecisionReason": "agent-org: " + "; ".join(denied)}, records
    return _allow(), records


def hook_decision(payload, org, mode="warn", acting=None):
    """Single-path compatibility API; use classify_write to inspect a multi-file tool call."""
    decision, records = classify_write(payload, org, mode, acting)
    return decision, records[0] if records else None


def _log_foreign(payload, foreign, context, phase):
    sid = state_id(payload.get("sessionId") or "unattributed")
    run_id = state_id(context.get("run_id") or sid)
    d = _state_dir(payload.get("cwd"), "foreign")
    if d is None:
        raise ValueError("foreign writes can only be recorded inside a git repository")
    target = d / run_id
    target.mkdir(parents=True, exist_ok=True)
    entry = dict(foreign, phase=phase, runId=run_id, sessionId=sid, tool=payload.get("toolName"))
    with (target / f"{sid}.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\n")


def foreign_records(root, session_id=None):
    directory = _state_dir(root, "foreign")
    if directory is None:
        return []
    if session_id:
        directory = directory / state_id(session_id)
    return [
        json.loads(line)
        for file in sorted(directory.rglob("*.jsonl"))
        for line in file.read_text(encoding="utf-8").splitlines() if line
    ]


def _task_context(payload, org, context):
    args = payload.get("toolArgs") or {}
    if isinstance(args, str):
        args = json.loads(args)
    if not isinstance(args, dict):
        raise ValueError("task arguments must be an object")
    target = args.get("agent_type")
    nodes = {n["id"]: n for n in org["nodes"]}
    if target not in nodes.keys() | {"splitter"}:
        return {}
    actor = context.get("node")
    if actor in nodes and target in nodes and nodes[target]["parent"] != actor:
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": f"agent-org: '{actor}' must route via direct children or return to its parent",
        }
    sid = state_id(payload["sessionId"])
    headers = [f"AgentOrgActingNode: {target}", f"AgentOrgRunId: {context.get('run_id', sid)}"]
    if context.get("worktree"):
        headers.append(f"AgentOrgWorktree: {context['worktree']}")
    headers.append(f"ParentAgentSessionId: {sid}")
    prompt = args.get("prompt", "")
    # Replace protocol headers, never examples quoted later in the task.
    while re.match(r"^(?:AgentOrgActingNode|AgentOrgRunId|AgentOrgWorktree|ParentAgentSessionId):", prompt):
        prompt = prompt.partition("\n")[2]
    return {"modifiedArgs": {**args, "prompt": "\n".join(headers) + "\n\n" + prompt}}


def _valid_peak(tokens):
    return isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0


def usage_record(root, agent, tokens, session, *, partial=False):
    """Record max observed inputTokens, once per (node, session), never billed or cumulative usage.

    Providers compete for an O_EXCL session lock in the Git common directory. Identical reports are
    no-ops; later turns or richer observations atomically raise the same session's peak, not append
    another sample. Legacy <node>.jsonl logs remain byte-for-byte untouched. New records live in
    usage/<node>/<session>.json, independently of completion-footer delivery.
    """
    agent, session = state_id(agent), state_id(session)
    if not _valid_peak(tokens):
        raise ValueError("peak context tokens must be an observed nonnegative integer")
    d = _state_dir(root, "usage")
    if d is None:
        raise ValueError("usage recording requires a Git workspace")
    directory = d / agent
    directory.mkdir(exist_ok=True)
    target = directory / f"{session}.json"
    lock = directory / f"{session}.lock"
    deadline = time.monotonic() + 5
    while True:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"usage recording lock is busy: {lock}")
            time.sleep(0.01)
    temporary = directory / f"{session}-{uuid.uuid4()}.tmp"
    try:
        os.close(handle)
        previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        if previous is not None:
            if previous.get("session_id") != session or not _valid_peak(previous.get("tokens")):
                raise ValueError(f"invalid peak context record: {target}")
            tokens = max(tokens, previous["tokens"])
            partial = partial or previous.get("partial", False)
        record = {"session_id": session, "tokens": tokens, "metric": "peak_input_tokens", "partial": bool(partial)}
        if record == previous:
            return {"recorded": False}
        with temporary.open("x", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record) + "\n")
        os.replace(temporary, target)
        return {"recorded": True}
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlink()


def split_advice(org, agent, root, window=200000, threshold=0.60):
    """Return a human-triaged split verdict from domain size OR peak session context occupancy.

    Each new session record is its max inputTokens; peak_session_tokens is the max across records.
    Missing observations are None, not zero. Historical untyped JSONL records are retained as legacy
    evidence, explicitly flagged because their cumulative totals cannot be converted into context peaks.
    """
    est = domain_size(org, agent, root)["est_tokens"]
    peak = None
    partial = False
    legacy = 0
    d = _state_dir(root, "usage")
    agent = state_id(agent)
    records = []
    if d:
        f = d / f"{agent}.jsonl"
        if f.exists():
            records.extend((line, True) for line in f.read_text(encoding="utf-8").splitlines())
        records.extend((f.read_text(encoding="utf-8"), False) for f in (d / agent).glob("*.json"))
    for line, historical in records:
        try:
            record = json.loads(line)
            value = record.get("tokens")
            if _valid_peak(value):
                peak = value if peak is None else max(peak, value)
                legacy += int(historical)
                partial = partial or historical or record.get("partial", False) is True
        except (ValueError, AttributeError):
            pass
    limit = threshold * window
    reasons = []
    if est >= limit:
        reasons.append(f"domain est_tokens {est} >= {limit:.0f}")
    if peak is not None and peak >= limit:
        reasons.append(f"peak session tokens {peak} >= {limit:.0f}" +
                       (" (includes unverified legacy usage)" if legacy else " (peak context occupancy)"))
    return {"agent": agent, "domain_est_tokens": est, "peak_session_tokens": peak,
            "peak_session_tokens_partial": bool(partial), "legacy_usage_records": legacy,
            "window": window, "threshold": threshold, "recommend_split": bool(reasons), "reasons": reasons}


def hook_workspace(payload, context):
    cwd = Path(payload.get("cwd") or ".").resolve()
    root = git_root(cwd) or cwd
    if not context.get("run_id") and STATE_ID_RE.fullmatch(root.name) and git_root(cwd) is not None:
        candidate = git_common_dir(root) / "agent-org" / "runs" / f"{root.name}.json"
        if candidate.exists():
            run = json.loads(candidate.read_text(encoding="utf-8"))
            if Path(run["path"]).resolve() == root:
                # Infer a missing run, but never overwrite an explicit conflicting worktree.
                context = {**context, "run_id": run["session_id"]}
                context.setdefault("worktree", str(root))
    if context.get("run_id") and git_root(cwd) is not None:
        run_file = git_common_dir(root) / "agent-org" / "runs" / f"{state_id(context['run_id'])}.json"
        if run_file.exists():
            run = json.loads(run_file.read_text(encoding="utf-8"))
            if (
                not run.get("ready") or run.get("session_id") != context["run_id"]
                or Path(run["path"]).resolve() != Path(run["repo"]).resolve() / ".worktrees" / context["run_id"]
                or git_common_dir(run["path"]) != git_common_dir(root)
            ):
                raise ValueError("Invalid bound agent-org run descriptor.")
            if context.get("worktree") and Path(context["worktree"]).resolve() != Path(run["path"]).resolve():
                raise ValueError("Acting context and run descriptor disagree about the worktree.")
            context = {**context, "worktree": run["path"]}
    if context.get("worktree"):
        target = Path(context["worktree"]).resolve()
        if git_root(target) != target or git_common_dir(target) != git_common_dir(root):
            raise ValueError("Bound worktree is not in this repository; refusing wrong-worktree routing.")
        root = target
    return root, context


def process_hook(payload, org_path=None, mode="warn", after=False):
    try:
        context = payload.get("agentOrgContext") or _context_from_map(payload)
        root, context = hook_workspace(payload, context)
        selected = _org_file(root, explicit=org_path, allow_uninstalled=True)
        if not selected.exists() and not (root / ".github/agent-org").exists():
            return {}
        org = json.loads(selected.read_text(encoding="utf-8-sig"))
    except (KeyError, OSError, TypeError, ValueError, subprocess.CalledProcessError) as error:
        message = f"agent-org: configuration audit failed: {error}"
        return {"additionalContext": message} if after else {
            "permissionDecision": "deny", "permissionDecisionReason": message,
        }
    violations = check_tree(org)
    if violations:
        message = "agent-org: invalid organization: " + "; ".join(v["evidence"] for v in violations)
        return {"additionalContext": message} if after else {
            "permissionDecision": "deny", "permissionDecisionReason": message,
        }
    acting = context.get("node") or os.environ.get("AGENT_ORG_ACTING")
    if payload.get("toolName") == "task" and not after:
        return _task_context(payload, org, context)
    decision, records = classify_write(payload, org, mode, acting, root)
    if after:
        result = payload.get("toolResult") or {}
        if isinstance(result, dict) and (result.get("resultType") == "failure" or result.get("success") is False):
            return {}
    for record in records:
        _log_foreign(payload, record, context, "completed" if after else "attempted")
    if records:
        warning = "agent-org: " + "; ".join(
            f"{record['path']} belongs to {record['owner'] or 'UNOWNED'}, not {record['acting']}"
            for record in records
        ) + ". Report the attempted change to your parent for routing and reconciliation."
        if after:
            return {"additionalContext": warning}
        if decision["permissionDecision"] == "allow":
            decision["additionalContext"] = warning
    return {} if after else decision


def _run_hook(org_path, mode="warn", after=False):
    payload = json.loads(sys.stdin.read())
    print(json.dumps(process_hook(payload, org_path, mode, after)))
    return 0


def _run_record_acting():
    record_acting(json.loads(sys.stdin.read()))
    print("{}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="agent-org owner-oracle / coverage validator")
    parser.add_argument("--org", help=f"explicit candidate path; normal runtime uses {ORG_PATH}")
    parser.add_argument("--root", default=".", help="repo root for git ls-files")
    parser.add_argument("--paths", nargs="*", help="explicit paths to check instead of git ls-files")
    parser.add_argument("--owner", help="print the owner of a single path and exit")
    parser.add_argument("--acting", help="integration-gate containment: assert changed paths are owned by this node id")
    parser.add_argument("--size", help="split self-check: report the domain-size proxy for this node id")
    parser.add_argument("--split-baseline",
                        help="validate an add-children split: compare this explicit baseline against --org")
    parser.add_argument("--window", type=int, default=200000, help="context window in tokens (default 200000)")
    parser.add_argument("--threshold", type=float, default=0.60, help="split fraction of the window (default 0.60)")
    parser.add_argument("--hook", action="store_true",
                        help="preToolUse hook: read a tool payload on stdin, print an allow/deny decision")
    parser.add_argument("--mode", choices=["warn", "enforce"], default="warn",
                        help="preToolUse hook mode: warn (allow + log a foreign write) or enforce (deny)")
    parser.add_argument("--record-acting", action="store_true",
                        help="userPromptSubmitted hook: read a payload on stdin, record sessionId -> node")
    parser.add_argument("--usage-record", metavar="NODE",
                        help="atomically record NODE's session peak input context (with --session and --tokens)")
    parser.add_argument("--tokens", type=int, help="observed max inputTokens for --usage-record, never CLI totalTokens")
    parser.add_argument("--partial", action="store_true", help="peak context observations are incomplete")
    parser.add_argument("--split-advice", metavar="NODE",
                        help="human-triaged split verdict from domain size OR peak session context")
    parser.add_argument("--root-split-check", metavar="NODE",
                        help="report whether NODE is the org root and its current split recommendation")
    parser.add_argument("--checkpoint", action="store_true", help="record the reconciled hybrid source snapshot")
    parser.add_argument("--drift", action="store_true", help="report managed files changed since reconciliation")
    parser.add_argument("--foreign", action="store_true", help="read foreign-write audit records")
    parser.add_argument("--post-hook", action="store_true", help="postToolUse: record completion and return a warning")
    parser.add_argument("--session", help="native session id for --usage-record; top-level session id for --foreign")
    args = parser.parse_args(argv)

    if args.record_acting:
        return _run_record_acting()

    if args.usage_record:
        if not args.session or args.tokens is None:
            parser.error("--usage-record requires --session and observed --tokens")
        print(json.dumps(usage_record(args.root, args.usage_record, args.tokens, args.session, partial=args.partial)))
        return 0

    if args.hook or args.post_hook:
        return _run_hook(args.org, mode=args.mode, after=args.post_hook)

    if args.foreign:
        print(json.dumps({"records": foreign_records(args.root, args.session)}, indent=2))
        return 0

    org_path = _org_file(args.root, explicit=args.org)
    org = json.loads(org_path.read_text(encoding="utf-8-sig"))

    if args.checkpoint:
        checkpoint(org, args.root)
        print(json.dumps({"status": "ok"}))
        return 0

    if args.drift:
        print(json.dumps(drift(org, args.root), indent=2))
        return 0

    if args.split_advice or args.root_split_check:
        agent = args.split_advice or args.root_split_check
        result = split_advice(org, agent, args.root, args.window, args.threshold)
        if args.root_split_check:
            result = {"agent": agent, "is_root": org["root"] == agent,
                      "recommend_split": result["recommend_split"], "reasons": result["reasons"]}
        print(json.dumps(result, indent=2))
        return 0

    if args.owner:
        result = ownership(org, args.owner)
        print(json.dumps(result))
        return 0 if result["status"] in {"owned", "unmanaged"} else 1

    if args.split_baseline:
        baseline = _org_file(args.root, explicit=args.split_baseline)
        old = json.loads(baseline.read_text(encoding="utf-8-sig"))
        paths = [normalize(p) for p in args.paths] if args.paths is not None else git_tracked(args.root)
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
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(json.dumps({"status": "error", "message": str(error)}))
        sys.exit(2)
