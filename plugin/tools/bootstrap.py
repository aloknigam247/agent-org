"""Render and safely install the repository-local agent-org runtime."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


class BootstrapError(ValueError):
    """The requested overlay cannot be installed without a conflict."""


def _node_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", value):
        raise BootstrapError("Node ids must match ^[a-z][a-z0-9-]*$.")
    if value in {"host", "splitter"} or re.fullmatch(r"aux|com[1-9]|con|lpt[1-9]|nul|prn", value):
        raise BootstrapError(f"Reserved node id: {value}")
    return value


def _configuration(org):
    root = _node_id(org.get("root"))
    scope = org.get("scope", ["**"])
    if not isinstance(scope, list) or not scope or any(
        not isinstance(pattern, str) or not pattern.strip() or any(c in pattern for c in "\r\n\0")
        for pattern in scope
    ):
        raise BootstrapError("scope must be a nonempty list of nonempty glob strings.")
    collaboration = org.get("collaboration", "agents")
    storage = org.get("storage", "local")
    if collaboration not in ("agents", "hybrid"):
        raise BootstrapError("collaboration must be agents or hybrid.")
    if storage not in ("local", "tracked"):
        raise BootstrapError("storage must be local or tracked.")
    return {"collaboration": collaboration, "root": root, "scope": scope, "storage": storage}


def _validate_org(org):
    if not isinstance(org, dict):
        raise BootstrapError("org.json must contain an organization object.")
    config = _configuration(org)
    nodes = org.get("nodes")
    if not isinstance(org.get("version"), int) or isinstance(org["version"], bool):
        raise BootstrapError("org.json must have an integer version.")
    if not isinstance(nodes, list) or not nodes:
        raise BootstrapError("org.json must contain live nodes.")
    ids = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise BootstrapError("Every live node must be an object.")
        node_id = _node_id(node.get("id"))
        if node_id in ids:
            raise BootstrapError(f"Duplicate node id: {node_id}")
        ids.add(node_id)
        if node.get("mode") not in ("Leaf", "Parent"):
            raise BootstrapError(f"Node {node_id} must have mode Leaf or Parent.")
        charter = node.get("charter")
        if not isinstance(charter, dict) or not isinstance(charter.get("domain"), list) or not charter["domain"]:
            raise BootstrapError(f"Node {node_id} must have a nonempty charter domain.")
    if config["root"] not in ids:
        raise BootstrapError("org.root must name a live node.")
    return config


def _read(path):
    try:
        return path.read_bytes()
    except OSError as error:
        raise BootstrapError(
            f"Cannot read runtime asset {path}. Use the original plugin source for a different profile: {error}"
        ) from error


def _assets(source):
    root = Path(source).resolve() if source is not None else Path(__file__).resolve().parents[1]
    plugin = (root / "plugin.json").is_file()
    installed = (
        not plugin
        and (root / "tools" / "bootstrap.py").is_file()
        and (root.parent / "agents" / "splitter.md").is_file()
        and (root.parent / "instructions" / "agent-org.instructions.md").is_file()
    )
    if not plugin and not installed:
        raise BootstrapError(f"Runtime source is neither a plugin root nor an installed agent-org runtime: {root}")
    return {
        "agents": root.parent / "agents" if installed else root / "agents",
        "extension": root.parent / "extensions" / "agent-org" if installed else root / "extensions" / "agent-org",
        "hooks": root.parent / "hooks" / "agent-org.json" if installed else root / "hooks.json",
        "instructions": root.parent / "instructions" if installed else root / "instructions",
        "loops": root / "loops",
        "root": root,
        "skills": root.parent / "skills" if installed else root / "skills",
        "template": root / "templates" / "_node.template.md",
    }


def runtime_files(org, source=None) -> dict[str, bytes]:
    """Render an overlay without org.json; source may be a plugin or an installed runtime root."""
    config = _validate_org(org)
    assets = _assets(source)
    files = {}

    def copy(relative, path):
        files[relative] = _read(path)

    template = _read(assets["template"]).decode("utf-8-sig").replace("\r\n", "\n")
    internal_visibility = "\nuser-invocable: false\n"
    if internal_visibility not in template:
        raise BootstrapError("The node template must default to user-invocable: false.")
    files[".github/agent-org/templates/_node.template.md"] = template.encode("utf-8")
    for node in org["nodes"]:
        replacements = {
            "id": node["id"],
            "loop": f".github\\agent-org\\loops\\{node['mode'].lower()}.md",
            "role": node["mode"],
        }
        rendered = template
        for key, value in replacements.items():
            rendered = rendered.replace("{{" + key + "}}", value)
        if node["id"] == config["root"]:
            rendered = rendered.replace(internal_visibility, "\nuser-invocable: true\n", 1)
        if "{{" in rendered:
            raise BootstrapError("Unresolved placeholder in the node template.")
        files[f".github/agents/{node['id']}.md"] = rendered.encode("utf-8")
    copy(".github/agents/splitter.md", assets["agents"] / "splitter.md")

    scope_profile = "full" if config["scope"] == ["**"] else "partial"
    for name in (
        f"agent-org.collaboration-{config['collaboration']}.instructions.md",
        "agent-org.instructions.md",
        f"agent-org.scope-{scope_profile}.instructions.md",
    ):
        copy(f".github/instructions/{name}", assets["instructions"] / name)

    for name in ("leaf.md", "parent.md"):
        copy(f".github/agent-org/loops/{name}", assets["loops"] / name)
    for name in ("agent-org-design", "agent-org-wiki-curate"):
        copy(f".github/skills/{name}/SKILL.md", assets["skills"] / name / "SKILL.md")
    for name in ("README.md", "bootstrap.py", "owner_validator.py", "requirements.txt", "worktree.py"):
        copy(f".github/agent-org/tools/{name}", assets["root"] / "tools" / name)
    hook = assets["root"] / "tools" / "hook.ps1"
    if hook.is_file():
        copy(".github/agent-org/tools/hook.ps1", hook)
    copy(".github/agent-org/org.schema.json", assets["root"] / "org.schema.json")
    copy(".github/agent-org/seed/org.json", assets["root"] / "seed" / "org.json")
    copy(".github/hooks/agent-org.json", assets["hooks"])

    extension = assets["extension"]
    copy(".github/extensions/agent-org/extension.mjs", extension / "extension.mjs")
    for path in sorted(extension.rglob("*.mjs")):
        relative = path.relative_to(extension)
        if any(
            part in {"__pycache__", "__tests__", "eval", "fixtures", "node_modules", "test", "tests"}
            for part in relative.parts
        ):
            continue
        if (
            path.stem in {"eval", "test", "tests"}
            or path.name.startswith(("eval_", "test_"))
            or path.name.endswith((".spec.mjs", ".test.mjs"))
        ):
            continue
        copy(f".github/extensions/agent-org/{relative.as_posix()}", path)
    return dict(sorted(files.items()))


def _git(repo, *args, input_bytes=None, allowed=(0,)):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], input=input_bytes, capture_output=True, check=False
    )
    if result.returncode not in allowed:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise BootstrapError(f"Git {' '.join(args)} failed: {detail}")
    return result.stdout


def _git_paths(repo):
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise BootstrapError(f"Repository directory does not exist: {repo}")
    try:
        top = Path(_git(repo, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
        common = Path(
            _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").decode("utf-8").strip()
        ).resolve()
    except BootstrapError as error:
        raise BootstrapError("Bootstrap requires an existing Git worktree; it does not run git init.") from error
    if top != repo:
        raise BootstrapError(f"--repo must name the repository root: {top}")
    return repo, common


def _destination_problem(root, relative):
    parts = relative.split("/")
    current = root
    for index, part in enumerate(parts):
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            return f"{relative}: symlink or junction in the destination"
        if current.exists():
            if index != len(parts) - 1 and not current.is_dir():
                return f"{relative}: parent is not a directory"
            if index == len(parts) - 1 and not current.is_file():
                return f"{relative}: destination is not a regular file"
    return None


def _same_content(actual, expected):
    return actual.replace(b"\r\n", b"\n") == expected.replace(b"\r\n", b"\n")


def _ignore_entry(relative):
    return "/" + "".join("\\" + char if char in "\\!*?[]# " else char for char in relative)


def _exclude_append(existing, entries):
    present = set(existing.splitlines())
    missing = [entry for entry in sorted(set(entries)) if entry.encode("utf-8") not in present]
    if not missing:
        return b"", []
    newline = b"\r\n" if b"\r\n" in existing else b"\n"
    prefix = newline if existing and not existing.endswith((b"\r", b"\n")) else b""
    return prefix + newline.join(entry.encode("utf-8") for entry in missing) + newline, missing


def bootstrap_repo(repo, *, root_name="main", scope=None, collaboration="agents", storage="local", source=None) -> dict:
    """Preflight and install without overwriting files, changing the index, or resetting a live tree."""
    requested = _configuration({
        "collaboration": collaboration,
        "root": root_name,
        "scope": ["**"] if scope is None else scope,
        "storage": storage,
    })
    repo, common = _git_paths(repo)
    problem = _destination_problem(repo, "org.json")
    if problem:
        raise BootstrapError(problem)
    org_file = repo / "org.json"
    if org_file.exists():
        org_bytes = org_file.read_bytes()
        try:
            org = json.loads(org_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BootstrapError(
                "Existing org.json is not a valid organization; it will not be overwritten."
            ) from error
        actual = _validate_org(org)
        conflicts = [field for field in requested if requested[field] != actual[field]]
        if conflicts:
            raise BootstrapError("Existing organization conflicts with requested " + ", ".join(conflicts) + ".")
    else:
        seed = _assets(source)["root"] / "seed" / "org.json"
        org = json.loads(_read(seed).decode("utf-8-sig"))
        _validate_org(org)
        if len(org["nodes"]) != 1 or org["nodes"][0]["mode"] != "Leaf":
            raise BootstrapError("The canonical bootstrap seed must contain one Leaf.")
        org["root"] = root_name
        org["nodes"][0]["id"] = root_name
        org["nodes"][0]["charter"]["domain"] = list(requested["scope"])
        for field in ("collaboration", "scope", "storage"):
            org[field] = requested[field]
        org_bytes = (json.dumps(org, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    desired = runtime_files(org, source=source)
    desired["org.json"] = org_bytes
    desired = dict(sorted(desired.items()))
    tracked = {
        path.decode("utf-8").casefold()
        for path in _git(repo, "ls-files", "--cached", "-z").split(b"\0") if path
    }
    conflicts = []
    for kind, values in (("collaboration", ("agents", "hybrid")), ("scope", ("full", "partial"))):
        for value in values:
            relative = f".github/instructions/agent-org.{kind}-{value}.instructions.md"
            path = repo / relative
            if relative not in desired and (path.exists() or path.is_symlink()):
                conflicts.append(f"{relative}: unselected agent-org profile is still installed")
    existing = []
    pending = []
    for relative, content in desired.items():
        path = repo.joinpath(*relative.split("/"))
        problem = _destination_problem(repo, relative)
        if problem:
            conflicts.append(problem)
        elif path.exists():
            if _same_content(path.read_bytes(), content):
                existing.append(relative)
            else:
                conflicts.append(f"{relative}: pre-existing content differs")
        else:
            pending.append(relative)
        if storage == "local" and relative.casefold() in tracked:
            conflicts.append(f"{relative}: already tracked; local excludes cannot hide tracked files")

    if storage == "tracked":
        ignored = _git(
            repo, "check-ignore", "--no-index", "-z", "--stdin",
            input_bytes=b"\0".join(path.encode("utf-8") for path in desired) + b"\0", allowed=(0, 1),
        )
        conflicts.extend(
            f"{path.decode('utf-8')}: ignored path conflicts with tracked storage"
            for path in ignored.split(b"\0") if path
        )

    exclude = common / "info" / "exclude"
    problem = _destination_problem(common, "info/exclude")
    if problem:
        conflicts.append(problem)
    if conflicts:
        raise BootstrapError("Bootstrap preflight failed:\n" + "\n".join(sorted(conflicts)))

    entries = ["/.github/agent-org/**/__pycache__/", "/.worktrees/"]
    if storage == "local":
        entries.extend(_ignore_entry(path) for path in desired)
    exclude_bytes = exclude.read_bytes() if exclude.exists() else b""
    addition, exclude_added = _exclude_append(exclude_bytes, entries)
    created = []
    try:
        for relative in pending:
            path = repo.joinpath(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as file:
                created.append(relative)
                file.write(desired[relative])
        if addition:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("ab") as file:
                file.write(addition)
    except OSError as error:
        for relative in reversed(created):
            path = repo.joinpath(*relative.split("/"))
            if path.is_file() and path.read_bytes() == desired[relative]:
                path.unlink()
        raise BootstrapError(f"Bootstrap could not finish writing the overlay: {error}") from error

    return {
        "created": created,
        "exclude_added": exclude_added,
        "exclude_file": str(exclude),
        "existing": existing,
        "org": org,
        "repo": str(repo),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collaboration", choices=("agents", "hybrid"), default="agents")
    parser.add_argument("--repo", default=".", help="existing Git worktree root")
    parser.add_argument("--root-name", default="main", help="root node id (default: main)")
    parser.add_argument("--scope", nargs="+", help="managed gitignore globs (default: **)")
    parser.add_argument("--storage", choices=("local", "tracked"), default="local")
    args = parser.parse_args(argv)
    try:
        result = bootstrap_repo(
            args.repo, root_name=args.root_name, scope=args.scope,
            collaboration=args.collaboration, storage=args.storage,
        )
    except (BootstrapError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
