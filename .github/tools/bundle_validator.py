"""bundle-integrity validator: the deterministic self-ownership checks (design §3.7 SO2–SO6).

Given a repo root and its ``org.json``, verify the static bundle invariants that need no agent judgement:

- **SO2 Bundle presence** — every live node has an agent-def ``.github/agents/<id>.md``.
- **SO3 Single-writer** — an artifact's declared ``owner`` matches the node whose namespace it sits in.
- **SO6 No orphan** — every file under ``wiki/``, ``skills/``, ``tools/`` sits in a *live* node's
  namespace (``wiki/<node>/…``); a namespace that is not a live node is an orphan.
- **Freshness (partial)** — an artifact's ``sources`` front-matter must resolve to existing files
  (dangling refs are caught; the "sources-changed ⇒ re-touched" half needs a diff and lives elsewhere).

Front-matter is the leading ``---`` YAML-ish block; parsed minimally (``owner:`` scalar, ``sources:``
list) so this tool keeps the kernel-tools dependency surface at zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BUNDLE_KINDS = ("wiki", "skills", "tools")


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


def check_bundle(org, root) -> dict:
    root = Path(root)
    live = {n["id"] for n in org.get("nodes", [])}
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
            if f.suffix.lower() == ".md":
                fm = _front_matter(f.read_text(encoding="utf-8", errors="replace"))
                owner = fm.get("owner")
                if owner and owner != ns:  # SO3
                    violations.append({"rule": "bundle", "path": rel,
                                       "evidence": f"owner {owner!r} != namespace {ns!r} (single-writer)"})
                for src in (fm.get("sources") or []):  # freshness (dangling half)
                    if not (root / src).exists():
                        violations.append({"rule": "bundle", "path": rel,
                                           "evidence": f"dangling source {src!r}"})
    return {"status": "ok" if not violations else "violations", "violations": violations}


def main(argv=None):
    parser = argparse.ArgumentParser(description="agentOrg bundle-integrity validator (SO2-SO6)")
    parser.add_argument("--org", default="org.json", help="path to org.json")
    parser.add_argument("--root", default=".", help="repo root")
    args = parser.parse_args(argv)
    org = json.loads(Path(args.org).read_text(encoding="utf-8"))
    result = check_bundle(org, args.root)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
