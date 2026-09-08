"""Eval-only bundle integrity and freshness checks.

Given a repo root and its ``.github/agent-org/org.json``, verify the static bundle invariants that
need no agent judgement. Charter globs and bundle sources remain relative to the repository root:

- **SO2 Bundle presence** — every live node has an agent-def ``.github/agents/<id>.md``.
- **SO3 Single-writer** — an artifact's declared ``owner`` matches its immutable charter owner.
- **SO6 No orphan** — every file under ``wiki/<node>/`` or ``tools/<node>/`` and every namespaced node skill
  under ``.github/skills/agent-org-<node>-<skill>/`` belongs to a live node.
- **Freshness (partial)** — an artifact's ``sources`` front-matter must resolve to existing files
  (dangling refs are caught; the "sources-changed ⇒ re-touched" half needs a diff and lives elsewhere).

These checks are called by eval preflight and grading, not by the installed plugin.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "tools"))
import owner_validator as ov  # noqa: E402
import org_config  # noqa: E402

BUNDLE_KINDS = ("tools", "wiki")
KERNEL_SKILLS = {"agent-org-design", "agent-org-wiki-curate"}
ORG_PATH = Path(org_config.ORG_PATH)


def _front_matter(text: str) -> dict:
    """Parse a leading ``---``…``---`` block into {key: scalar | list}. Minimal by design."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm, key = {}, None
    for raw in text[3:end].splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and key is not None:
            fm.setdefault(key, [])
            if isinstance(fm[key], list):
                fm[key].append(stripped[2:].strip())
        elif ":" in line and not line.startswith(" "):
            k, _, val = line.partition(":")
            key = k.strip()
            val = val.strip()
            if val.startswith("[") and val.endswith("]"):
                fm[key] = [x.strip() for x in val[1:-1].split(",") if x.strip()]
            elif val:
                fm[key] = val
            else:
                fm[key] = []
    return fm


def _node_skill_bundles(root):
    base = root / ".github" / "skills"
    if not base.is_dir():
        return
    for directory in sorted(path for path in base.iterdir() if path.is_dir()):
        if directory.name in KERNEL_SKILLS or not directory.name.startswith("agent-org-"):
            continue
        skill = directory / "SKILL.md"
        fields, error = _skill_front_matter(
            skill.read_text(encoding="utf-8", errors="replace")
        ) if skill.is_file() else ({}, None)
        yield directory, skill, fields, error


def _skill_front_matter(text):
    if not text.startswith("---"):
        return {}, None
    end = text.find("\n---", 3)
    if end == -1:
        return {}, "front matter is not terminated"
    try:
        fields = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError as error:
        return {}, str(error)
    if not isinstance(fields, dict):
        return {}, "front matter must be a mapping"
    return fields, None


def _skill_sources(fields):
    if "sources" not in fields:
        return [], None
    sources = fields["sources"]
    if not isinstance(sources, list) or any(
        not isinstance(source, str) or not source.strip() for source in sources
    ):
        return [], "sources must be a list of non-empty path strings"
    return sources, None


def _skill_source_violations(root, rel, fields, rule="bundle"):
    sources, error = _skill_sources(fields)
    if error:
        return [{"rule": rule, "path": rel, "evidence": f"invalid sources: {error}"}]
    return [
        {"rule": rule, "path": rel, "evidence": f"dangling source {source!r}"}
        for source in sources
        if not (root / source).exists()
    ]


def _metadata_violations(root, file, namespace):
    if file.suffix.lower() != ".md":
        return []
    rel = file.relative_to(root).as_posix()
    fields = _front_matter(file.read_text(encoding="utf-8", errors="replace"))
    violations = []
    owner = fields.get("owner")
    if owner and owner != namespace:
        violations.append({
            "rule": "bundle",
            "path": rel,
            "evidence": f"owner {owner!r} != namespace {namespace!r} (single-writer)",
        })
    for source in fields.get("sources") or []:
        if not (root / source).exists():
            violations.append({"rule": "bundle", "path": rel, "evidence": f"dangling source {source!r}"})
    return violations


def _skill_metadata_violations(root, file, namespace):
    if file.suffix.lower() != ".md":
        return []
    rel = file.relative_to(root).as_posix()
    fields, error = _skill_front_matter(file.read_text(encoding="utf-8", errors="replace"))
    if error:
        return [{"rule": "bundle", "path": rel, "evidence": f"invalid YAML front matter: {error}"}]
    violations = []
    owner = fields.get("owner")
    if owner and owner != namespace:
        violations.append({
            "rule": "bundle",
            "path": rel,
            "evidence": f"owner {owner!r} != charter owner {namespace!r} (single-writer)",
        })
    violations.extend(_skill_source_violations(root, rel, fields))
    return violations


def check_bundle(org, root) -> dict:
    root = Path(root)
    live = {n["id"] for n in org.get("nodes", [])}
    compiled = ov.compile_nodes(org.get("nodes", []))
    violations = []

    for nid in sorted(live):  # SO2
        if not (root / ".github" / "agents" / f"{nid}.md").exists():
            violations.append({"rule": "bundle", "node": nid,
                               "evidence": "missing agent-def .github/agents/<id>.md"})

    for kind in BUNDLE_KINDS:
        base = root / kind
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(root).as_posix()
            parts = f.relative_to(base).parts
            ns = parts[0] if len(parts) > 1 else None  # kind/<ns>/...
            if ns not in live:  # SO6
                violations.append({"rule": "bundle", "path": rel,
                                   "evidence": f"namespace {ns!r} is not a live node (orphan)"})
                continue
            violations.extend(_metadata_violations(root, f, ns))

    for directory, skill, fields, error in _node_skill_bundles(root):
        rel = directory.relative_to(root).as_posix()
        if not skill.is_file():
            violations.append({"rule": "bundle", "path": rel, "evidence": "node skill is missing SKILL.md"})
            continue
        skill_rel = skill.relative_to(root).as_posix()
        if error:
            violations.append({
                "rule": "bundle",
                "path": skill_rel,
                "evidence": f"invalid YAML front matter: {error}",
            })
            continue
        matches = ov.owners_of(compiled, skill_rel)
        owner = matches[0] if len(matches) == 1 else None
        if owner is None:
            status = "overlap" if matches else "unowned"
            violations.append({
                "rule": "bundle",
                "path": skill_rel,
                "evidence": (
                    f"node skill has no single live charter owner (orphan): "
                    f"{status} {matches}"
                ),
            })
            continue
        declared_owner = fields.get("owner")
        if declared_owner != owner:
            violations.append({
                "rule": "bundle",
                "path": skill_rel,
                "evidence": f"owner {declared_owner!r} != charter owner {owner!r} (single-writer)",
            })
        expected_prefix = f"agent-org-{owner}-"
        if not directory.name.startswith(expected_prefix) or len(directory.name) == len(expected_prefix):
            violations.append({
                "rule": "bundle",
                "path": skill_rel,
                "evidence": f"skill folder does not match charter owner {owner!r} (single-writer)",
            })
        if fields.get("name") != directory.name:
            violations.append({
                "rule": "bundle",
                "path": skill_rel,
                "evidence": f"skill name {fields.get('name')!r} != folder {directory.name!r}",
            })
        if fields.get("user-invocable") is not False:
            violations.append({
                "rule": "bundle",
                "path": skill_rel,
                "evidence": "node skill must set user-invocable: false",
            })
        if (
            "disable-model-invocation" in fields
            and fields["disable-model-invocation"] is not False
        ):
            violations.append({
                "rule": "bundle",
                "path": skill_rel,
                "evidence": "node skill must allow model invocation",
            })
        violations.extend(_skill_source_violations(root, skill_rel, fields))
        for file in sorted(path for path in directory.rglob("*") if path.is_file() and path != skill):
            file_rel = file.relative_to(root).as_posix()
            file_matches = ov.owners_of(compiled, file_rel)
            if file_matches != [owner]:
                violations.append({
                    "rule": "bundle",
                    "path": file_rel,
                    "evidence": (
                        f"charter ownership {file_matches!r} != owning skill {owner!r} "
                        "(single-writer)"
                    ),
                })
            violations.extend(_skill_metadata_violations(root, file, owner))
    return {"status": "ok" if not violations else "violations", "violations": violations}


def check_freshness(root, changed_paths) -> dict:
    """SO5 (freshness): if a run changed a file that a bundle artifact lists as a `source`, the artifact
    must be re-touched in the same run. `checked` counts artifacts with a non-empty sources list (so a
    caller can tell whether the check applied at all)."""
    root = Path(root)
    changed = {str(p).replace("\\", "/") for p in changed_paths}
    violations, checked = [], 0
    for kind in BUNDLE_KINDS:
        base = root / kind
        if not base.exists():
            continue
        for f in sorted(base.rglob("*.md")):
            if not f.is_file():
                continue
            sources = _front_matter(f.read_text(encoding="utf-8", errors="replace")).get("sources") or []
            if not sources:
                continue
            checked += 1
            rel = f.relative_to(root).as_posix()
            changed_sources = [s for s in sources if s.replace("\\", "/") in changed]
            if changed_sources and rel not in changed:
                violations.append({"rule": "freshness", "path": rel,
                                   "evidence": f"sources changed {changed_sources} but not re-touched"})
    for directory, skill, fields, error in _node_skill_bundles(root):
        if not skill.is_file() or error:
            continue
        for file in sorted(path for path in directory.rglob("*.md") if path.is_file()):
            file_fields, file_error = _skill_front_matter(file.read_text(encoding="utf-8", errors="replace"))
            rel = file.relative_to(root).as_posix()
            if file_error:
                continue
            sources, sources_error = _skill_sources(file_fields)
            if sources_error:
                violations.append({
                    "rule": "freshness",
                    "path": rel,
                    "evidence": f"invalid sources: {sources_error}",
                })
                continue
            if not sources:
                continue
            checked += 1
            changed_sources = [source for source in sources if source.replace("\\", "/") in changed]
            if changed_sources and rel not in changed:
                violations.append({
                    "rule": "freshness",
                    "path": rel,
                    "evidence": f"sources changed {changed_sources} but not re-touched",
                })
    return {"status": "ok" if not violations else "violations", "violations": violations, "checked": checked}


def check_agent_roles(org, root):
    problems = []
    for node in org["nodes"]:
        file = Path(root) / ".github" / "agents" / f"{node['id']}.md"
        if not file.exists():
            problems.append(f"missing agent definition for {node['id']}")
            continue
        text = file.read_text(encoding="utf-8")
        header = text.split("---", 2)
        try:
            fields = yaml.safe_load(header[1]) if len(header) == 3 and not header[0].strip() else {}
        except yaml.YAMLError as error:
            problems.append(f"invalid agent definition {node['id']}: {error}")
            continue
        body = header[2] if len(header) == 3 and not header[0].strip() else ""
        references = re.findall(r"^loop:[ \t]*(.*?)[ \t]*\r?$", body, re.MULTILINE)
        expected = f".github/agent-org/loops/{node['mode'].lower()}.md"
        if (
            not isinstance(fields, dict) or "loop" in fields or len(references) != 1
            or references[0].replace("\\", "/") != expected
        ):
            problems.append(f"{node['id']} must reference {expected}")
        elif not Path(root).joinpath(*expected.split("/")).is_file():
            problems.append(f"missing loop file for {node['id']}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="Eval-only agent-org bundle checks")
    parser.add_argument("--org", default=None,
                        help=f"organization file, absolute or relative to --root (default: {ORG_PATH.as_posix()}; "
                             "no legacy fallback)")
    parser.add_argument("--root", default=".", help="repo root")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    org_path = org_config.config_path(root, explicit=args.org)
    org = json.loads(org_path.read_text(encoding="utf-8-sig"))
    result = check_bundle(org, root)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
