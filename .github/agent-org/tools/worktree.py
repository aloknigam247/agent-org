"""One linked worktree per top-level agent-org session, shared by all descendants.

Descriptors and the integration lock live in the common Git directory. Integration validates
combined ownership coverage, not the identity of an individual writer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap  # noqa: E402
import owner_validator as ov  # noqa: E402


class WorktreeError(ValueError):
    def __init__(self, reason, message, violations=None):
        super().__init__(message)
        self.reason = reason
        self.violations = violations or []


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=check, encoding="utf-8", text=True
    )


def _session_id(value):
    reserved = r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])"
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", value)
        or re.fullmatch(reserved, value, re.IGNORECASE)
    ):
        raise WorktreeError("session", "Use a session ID containing only letters, digits and hyphens.")
    return value


def _root(repo):
    root = ov.git_root(repo)
    if root is None:
        raise WorktreeError("repository", f"Not a Git working tree: {repo}")
    return root


def _safe_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise WorktreeError("path", f"Unsafe relative path: {relative}")
    path = root / relative
    if path.resolve() != path or not path.is_relative_to(root):
        raise WorktreeError("path", f"Refusing a redirected path: {path}")
    return path


def _read_org(root):
    org = json.loads(_safe_path(root, "org.json").read_text(encoding="utf-8-sig"))
    if not isinstance(org, dict) or not isinstance(org.get("nodes"), list):
        raise WorktreeError("organization", "org.json must contain an organization object with a nodes array.")
    return org


def _state(common):
    state = _safe_path(common, "agent-org")
    state.mkdir(exist_ok=True)
    return state


def _record_path(common, session_id):
    directory = _safe_path(_state(common), "runs")
    directory.mkdir(exist_ok=True)
    return _safe_path(directory, f"{_session_id(session_id)}.json")


@contextmanager
def _lock(common):
    path = _safe_path(_state(common), "integration.lock")
    token = f"{os.getpid()}:{uuid.uuid4()}"
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise WorktreeError("busy", f"Agent-org lifecycle is busy; lock held at {path}.") from exc
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(token)
        yield
    finally:
        if path.exists() and path.read_text(encoding="utf-8") == token:
            path.unlink()


def _worktrees(root):
    records = {}
    current = None
    for field in _git(root, "worktree", "list", "--porcelain", "-z").stdout.split("\0"):
        if field.startswith("worktree "):
            current = Path(field.removeprefix("worktree ")).resolve()
            records[current] = {}
        elif current is not None and field:
            key, _, value = field.partition(" ")
            records[current][key] = value
    return records


def _load(root, common, session_id):
    session_id = _session_id(session_id["session_id"] if isinstance(session_id, dict) else session_id)
    path = _record_path(common, session_id)
    if not path.is_file():
        raise WorktreeError("session", f"No registered agent-org session: {session_id}")
    run = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(run, dict)
        or not isinstance(run.get("base_org"), dict)
        or not isinstance(run.get("local_files"), dict)
    ):
        raise WorktreeError("session", "The session descriptor is invalid; leave its worktree intact.")
    source, tree = Path(run["repo"]), Path(run["path"])
    if (
        run["session_id"] != session_id
        or not source.is_absolute()
        or source.resolve() != source
        or tree != _safe_path(source, Path(".worktrees") / session_id)
        or root not in (source, tree)
        or run["branch"] != f"agent-org/{session_id}"
        or ov.git_root(source) != source
        or ov.git_common_dir(source) != common
        or ov.git_root(tree) != tree
        or ov.git_common_dir(tree) != common
    ):
        raise WorktreeError("session", "The descriptor does not identify this repository's managed worktree.")
    records = _worktrees(source)
    if records.get(tree, {}).get("branch") != f"refs/heads/{run['branch']}":
        raise WorktreeError("session", "The registered worktree is missing or is on a different branch.")
    if not re.fullmatch(r"[0-9a-f]{40,64}", run["base_sha"]):
        raise WorktreeError("session", "The descriptor has an invalid base commit.")
    _git(source, "check-ref-format", "--branch", run["base_branch"])
    for name in set(run["local_files"]) | set(run.get("runtime_files", [])):
        _safe_path(source, name)
        _safe_path(tree, name)
    return run


def _current_run(root, common):
    if root.parent == root.parent.parent / ".worktrees" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", root.name):
        if _record_path(common, root.name).exists():
            return _load(root, common, root.name)
    return None


def _tracked(root):
    index = _git(root, "ls-files", "--cached", "-z").stdout
    head = _git(root, "ls-tree", "-r", "--name-only", "-z", "HEAD").stdout
    return {name for name in (index + head).split("\0") if name}


def _ephemeral(name):
    return "__pycache__" in Path(name).parts


def _overlay_names(root, *orgs, runtime_names=None):
    names = set(bootstrap.runtime_files(orgs[0]) if runtime_names is None else runtime_names)
    names.add("org.json")
    for org in orgs:
        for node in org.get("nodes", []):
            node_id = _session_id(node["id"])
            names.add(f".github/agents/{node_id}.md")
    directory = _safe_path(root, ".github/instructions")
    if directory.exists():
        names.update(path.relative_to(root).as_posix() for path in directory.glob("agent-org*.instructions.md"))
    return {name for name in names if not _ephemeral(name)}


def _overlay_allowed(name, known):
    if _ephemeral(name):
        return False
    return name in known or re.fullmatch(r"\.github/instructions/agent-org[^/]*\.instructions\.md", name) is not None


def _bytes(root, name):
    path = _safe_path(root, name)
    return path.read_bytes() if path.exists() else None


def _digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _write_bytes(path, data):
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4()}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _exclude(common, patterns):
    path = _safe_path(common, Path("info") / "exclude")
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = sorted(set(patterns) - set(content.splitlines()))
    if missing:
        prefix = content + ("\n" if content and not content.endswith("\n") else "")
        _write_bytes(path, (prefix + "\n".join(missing) + "\n").encode("utf-8"))


def _exclude_local(common, names):
    patterns = {"/.github/agent-org/**/__pycache__/"}
    for name in names:
        literal = re.sub(r"([\\*?\[\] ])", r"\\\1", name)
        patterns.add(f"/{literal}")
    _exclude(common, patterns)


def _ready(run):
    if not run.get("ready"):
        raise WorktreeError("session", "Session creation was incomplete; preserve it or clean up with --discard.")


def create(repo, session_id=None):
    """Create a top-level session, or reuse its registered tree from any descendant cwd."""
    if session_id is not None:
        _session_id(session_id)
    root = _root(repo)
    common = ov.git_common_dir(root)
    with _lock(common):
        existing = _current_run(root, common)
        if existing is not None:
            _ready(existing)
            return existing
        session_id = session_id or uuid.uuid4().hex
        record = _record_path(common, session_id)
        if record.exists():
            existing = _load(root, common, session_id)
            _ready(existing)
            return existing
        branch = f"agent-org/{session_id}"
        tree = _safe_path(root, Path(".worktrees") / session_id)
        if tree.exists() or _git(root, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0:
            raise WorktreeError("session", "The session path or branch already exists without a matching descriptor.")
        base_branch = _git(root, "symbolic-ref", "--quiet", "HEAD", check=False)
        if base_branch.returncode:
            raise WorktreeError("branch", "Create a session from a named branch, not detached HEAD.")
        base_sha = _git(root, "rev-parse", "HEAD").stdout.strip()
        org = _read_org(root)
        names = _overlay_names(root, org)
        tracked = _tracked(root)
        copied = {name: _bytes(root, name) for name in sorted(names - tracked)}
        copied = {name: data for name, data in copied.items() if data is not None}
        local = org.get("storage", "local") == "local"
        _exclude(common, {f"/.worktrees/{session_id}/"})
        if local:
            _exclude_local(common, names)
        if _status(root) or _ongoing(root):
            raise WorktreeError(
                "dirty-source", "Commit or set aside source changes before starting a session; they are not copied."
            )
        _git(root, "worktree", "add", "-q", "-b", branch, str(tree), base_sha)
        run = {
            "base_branch": base_branch.stdout.strip().removeprefix("refs/heads/"),
            "base_org": org,
            "base_sha": base_sha,
            "branch": branch,
            "local_files": {name: _digest(data) for name, data in copied.items()} if local else {},
            "path": str(tree),
            "ready": False,
            "repo": str(root),
            "runtime_files": sorted(names),
            "session_id": session_id,
        }
        ov.write_json(record, run)
        for name, data in copied.items():
            target = _safe_path(tree, name)
            if target.exists():
                raise WorktreeError("overlay", f"Refusing to replace an existing worktree artifact: {name}")
            _write_bytes(target, data)
        run.update(base_org=_read_org(tree), ready=True)
        ov.write_json(record, run)
        return run


def _status(root):
    return _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout


def _ongoing(root):
    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    return any(
        (git_dir / name).exists()
        for name in ("CHERRY_PICK_HEAD", "MERGE_HEAD", "REVERT_HEAD", "rebase-apply", "rebase-merge", "sequencer")
    )


def _ancestor(root, before, after):
    result = _git(root, "merge-base", "--is-ancestor", before, after, check=False)
    if result.returncode not in (0, 1):
        result.check_returncode()
    return result.returncode == 0


def _source_ready(run):
    source = Path(run["repo"])
    branch = _git(source, "symbolic-ref", "--quiet", "HEAD", check=False)
    if branch.returncode or branch.stdout.strip() != f"refs/heads/{run['base_branch']}":
        raise WorktreeError("branch", f"Source must remain on its original branch: {run['base_branch']}")
    if _status(source) or _ongoing(source):
        raise WorktreeError(
            "dirty-source", "Source has local changes or a Git operation in progress; leave them intact."
        )
    if not _ancestor(source, run["base_sha"], "HEAD"):
        raise WorktreeError("branch", "Source history no longer contains the session's base commit.")


def _paths(root, baseline):
    staged = _git(root, "diff", "--cached", "--name-only", "--no-renames", "-z").stdout
    return sorted(
        set(ov.git_tracked(root)) | set(ov.git_changed(root, baseline)) | set(filter(None, staged.split("\0")))
    )


def _gate(org, paths, old):
    verdict = ov.validate(org, paths)
    if verdict["status"] != "ok":
        raise WorktreeError("coverage", "Final organization coverage is invalid.", verdict["violations"])
    if old != org:
        verdict = ov.check_split(old, org, paths)
        if verdict["status"] != "ok":
            raise WorktreeError("split", "The organization change is not a valid split.", verdict["violations"])


def _local_plan(run, common, org):
    source, tree = Path(run["repo"]), Path(run["path"])
    if run["base_org"].get("storage", "local") != "local":
        return {}
    source_tracked, tree_tracked = _tracked(source), _tracked(tree)
    known = _overlay_names(tree, run["base_org"], org, runtime_names=run.get("runtime_files"))
    names = (known - tree_tracked) | set(run["local_files"])
    if any(not _overlay_allowed(name, known) for name in names):
        raise WorktreeError("overlay", "The descriptor contains a file outside the known local overlay.")
    _exclude_local(common, names)
    plan = {}
    for name in sorted(names):
        before, after = _bytes(source, name), _bytes(tree, name)
        if name not in run["local_files"] and after is None:
            continue
        if (
            name in source_tracked
            or name in tree_tracked
            or _digest(before) != run["local_files"].get(name)
        ):
            raise WorktreeError("overlay-conflict", f"Source overlay changed since this session was copied: {name}")
        plan[name] = (before, after)
    return plan


def _apply_local(source, plan, applied):
    for name, (before, after) in plan.items():
        if _bytes(source, name) != before:
            raise WorktreeError("overlay-conflict", f"Source overlay changed during integration: {name}")
        if before != after:
            _write_bytes(_safe_path(source, name), after)
            applied[name] = (before, after)


def _restore_local(source, applied):
    for name, (before, after) in reversed(list(applied.items())):
        if _bytes(source, name) != after:
            raise WorktreeError("overlay-conflict", f"Preserved concurrent overlay edits; inspect {name}.")
        _write_bytes(_safe_path(source, name), before)


def _abort_merge(source, before, session_head):
    head = _git(source, "rev-parse", "HEAD").stdout.strip()
    merge = _git(source, "rev-parse", "--quiet", "--verify", "MERGE_HEAD", check=False)
    if head == before and merge.returncode == 0 and merge.stdout.strip() == session_head:
        _git(source, "merge", "--abort")


def _rollback_commit(source, tree, before, session_head, merged):
    parents = _git(source, "rev-list", "--parents", "-n", "1", merged).stdout.split()
    if _git(source, "rev-parse", "HEAD").stdout.strip() != merged or parents != [merged, before, session_head]:
        raise WorktreeError("rollback", "Source history changed; the integration attempt was left intact for recovery.")
    if _status(source) or _status(tree) or _git(tree, "rev-parse", "HEAD").stdout.strip() != session_head:
        raise WorktreeError("rollback", "New edits were preserved; inspect the source and session before retrying.")
    # Keep even hook-produced commit contents reachable in the session before undoing our merge.
    _git(tree, "merge", "--ff-only", "--no-edit", merged)
    _git(source, "reset", "--merge", before)


def _message(message):
    header = message.splitlines()[0] if isinstance(message, str) and message else ""
    pattern = r"(?:build|chore|ci|docs|feat|fix|perf|refactor|revert|test)(?:\([a-z0-9-]+\))?!?: \S.*"
    if not re.fullmatch(pattern, header) or header.endswith(".") or len(header) > 72:
        raise WorktreeError(
            "message", "Use a conventional commit subject, at most 72 characters without a final period."
        )


def _integrate(root, common, session_id, message):
    with _lock(common):
        run = _load(root, common, session_id)
        _ready(run)
        _message(message)
        source, tree = Path(run["repo"]), Path(run["path"])
        org = _read_org(tree)
        plan = _local_plan(run, common, org)
        _source_ready(run)
        if _ongoing(tree) or not _ancestor(tree, run["base_sha"], "HEAD"):
            raise WorktreeError("session", "Finish the session's Git operation and preserve its base history first.")
        _gate(org, _paths(tree, run["base_sha"]), run["base_org"])
        _git(tree, "add", "--all")
        staged = _git(tree, "diff", "--cached", "--quiet", check=False)
        if staged.returncode == 1:
            _git(tree, "commit", "-q", "-m", message)
        elif staged.returncode:
            staged.check_returncode()
        if _status(tree):
            raise WorktreeError("dirty-session", "A hook or concurrent writer left session edits; integrate again.")
        org = _read_org(tree)
        plan = _local_plan(run, common, org)
        paths = _paths(tree, run["base_sha"])
        _gate(org, paths, run["base_org"])
        _source_ready(run)
        before = _git(source, "rev-parse", "HEAD").stdout.strip()
        old_org = _read_org(source)
        session_head = _git(tree, "rev-parse", "HEAD").stdout.strip()
        merge_needed = not _ancestor(source, session_head, before)
        applied = {}
        checkpoint_before = None
        checkpoint_written = None
        checkpoint_path = _safe_path(_state(common), "checkpoint.json")
        merged = None
        try:
            if merge_needed:
                merge = _git(source, "merge", "--no-ff", "--no-commit", run["branch"], check=False)
                if merge.returncode:
                    raise WorktreeError("conflict", (merge.stderr + merge.stdout).strip())
            final_org = org if "org.json" in plan else _read_org(source)
            _gate(final_org, sorted(set(paths) | set(_paths(source, before))), old_org)
            if merge_needed:
                _git(source, "commit", "-q", "-m", message)
                merged = _git(source, "rev-parse", "HEAD").stdout.strip()
                final_org = org if "org.json" in plan else _read_org(source)
                _gate(final_org, sorted(set(paths) | set(_paths(source, before))), old_org)
                if _status(source):
                    raise WorktreeError("dirty-source", "A hook or concurrent writer changed the merged source.")
            _apply_local(source, plan, applied)
            final_org = _read_org(source)
            _gate(final_org, sorted(set(paths) | set(_paths(source, before))), old_org)
            if _status(source):
                raise WorktreeError("dirty-source", "Concurrent source edits were preserved; integrate again.")
            run.update(
                integrated_head=session_head,
                integrated_sha=_git(source, "rev-parse", "HEAD").stdout.strip(),
                local_files={name: _digest(after) for name, (_, after) in plan.items()},
            )
            if final_org.get("collaboration", "agents") == "hybrid":
                checkpoint_before = checkpoint_path.read_bytes() if checkpoint_path.exists() else None
                ov.checkpoint(final_org, source)
                checkpoint_written = checkpoint_path.read_bytes()
            ov.write_json(_record_path(common, run["session_id"]), run)
        except (KeyError, OSError, subprocess.CalledProcessError, TypeError, ValueError):
            if checkpoint_written is not None and checkpoint_path.read_bytes() == checkpoint_written:
                _write_bytes(checkpoint_path, checkpoint_before)
            _restore_local(source, applied)
            if merged is not None:
                _rollback_commit(source, tree, before, session_head, merged)
            elif merge_needed:
                _abort_merge(source, before, session_head)
            raise
        if not merge_needed and not applied:
            return {"changed": [], "integrated": False, "reason": "no-op"}
        changed = set(ov.git_changed(tree, run["base_sha"])) | set(ov.git_changed(source, before))
        return {"changed": sorted(changed | set(applied)), "integrated": True}


def _failure(exc):
    result = {"error": str(exc), "reason": getattr(exc, "reason", "error")}
    if isinstance(exc, subprocess.CalledProcessError):
        result["error"] = (exc.stderr or exc.stdout or str(exc)).strip()
    if getattr(exc, "violations", None):
        result["violations"] = exc.violations
    return result


def integrate(repo, session_id, message="feat: integrate agent-org session"):
    """Reconcile a shared session; return an explicit no-op or rejection without removing its tree."""
    try:
        root = _root(repo)
        return _integrate(root, ov.git_common_dir(root), session_id, message)
    except (KeyError, OSError, subprocess.CalledProcessError, TypeError, ValueError) as exc:
        return {"integrated": False, **_failure(exc)}


def cleanup(repo, session_id, discard=False):
    """Remove only this registered tree and branch; unintegrated data requires explicit discard."""
    root = _root(repo)
    common = ov.git_common_dir(root)
    with _lock(common):
        run = _load(root, common, session_id)
        source, tree = Path(run["repo"]), Path(run["path"])
        records = _worktrees(source)
        if any(path != tree and path.is_relative_to(tree) for path in records):
            raise WorktreeError("worktree", "An unrelated linked worktree is nested here; leave both intact.")
        if any(path != tree and item.get("branch") == records[tree].get("branch") for path, item in records.items()):
            raise WorktreeError("worktree", "An unrelated linked worktree also uses this branch; leave both intact.")
        if "locked" in records[tree]:
            raise WorktreeError("worktree", "The registered worktree is locked; leave it intact.")
        if not discard:
            _ready(run)
            head = _git(tree, "rev-parse", "HEAD").stdout.strip()
            if _status(tree) or _ongoing(tree):
                raise WorktreeError("dirty-session", "Session has unintegrated edits; integrate or use --discard.")
            if head != run.get("integrated_head") or not _ancestor(source, head, f"refs/heads/{run['base_branch']}"):
                raise WorktreeError(
                    "unintegrated", "Integrate this session successfully before cleanup, or use --discard."
                )
            org = _read_org(tree)
            known = _overlay_names(tree, run["base_org"], org, runtime_names=run.get("runtime_files"))
            names = (known - _tracked(tree)) | set(run["local_files"])
            local = {
                name: _digest(_bytes(tree, name))
                for name in names if name in run["local_files"] or _bytes(tree, name) is not None
            } if run["base_org"].get("storage", "local") == "local" else {}
            if local != run["local_files"]:
                raise WorktreeError(
                    "unintegrated", "Session overlay has unintegrated edits; integrate or use --discard."
                )
            ignored = _git(tree, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").stdout
            for name in filter(None, ignored.split("\0")):
                namespace = name.startswith((".github/agent-org/", ".github/extensions/agent-org/"))
                cache = namespace and _ephemeral(name)
                if name not in local and not cache:
                    raise WorktreeError("unintegrated", f"Preserved an ignored session file: {name}; use --discard.")
        args = ["worktree", "remove"]
        if discard:
            args.append("--force")
        cwd = Path.cwd().resolve()
        if cwd.is_relative_to(tree):
            os.chdir(source)
        try:
            _git(source, *args, str(tree))
        except (OSError, subprocess.CalledProcessError):
            if cwd.is_relative_to(tree) and cwd.exists():
                os.chdir(cwd)
            raise
        _git(source, "branch", "-D", run["branch"])
        _record_path(common, run["session_id"]).unlink()
        return {"cleaned": True, "discarded": discard, "session_id": run["session_id"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Shared agent-org session worktree lifecycle")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("cleanup", "create", "integrate"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--repo", required=True, help="Source repository or a directory in its session worktree")
        subparser.add_argument("--session", required=command != "create", help="Top-level session identifier")
        if command == "cleanup":
            subparser.add_argument("--discard", action="store_true")
        elif command == "integrate":
            subparser.add_argument("--message", default="feat: integrate agent-org session")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create(args.repo, args.session)
        elif args.command == "integrate":
            result = integrate(args.repo, args.session, args.message)
        else:
            result = cleanup(args.repo, args.session, args.discard)
    except (KeyError, OSError, subprocess.CalledProcessError, TypeError, ValueError) as exc:
        result = _failure(exc)
    print(json.dumps(result, indent=2))
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
