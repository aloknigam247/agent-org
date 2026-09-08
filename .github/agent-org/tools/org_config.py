"""Canonical configuration paths and the bounded, explicit v4 -> v5 handover.

Ownership paths are always relative to the Git worktree root, never this file's directory.
There is no legacy discovery fallback: a legacy live file requires an explicit migration
or the exact, expiring ConfigRelocation prepared by config_relocation.py.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

ORG_PATH = ".github/agent-org/org.json"
LEGACY_PATH = "org.json"
SEED_PATH = ".github/agent-org/seed/org.json"
PROTOCOL = "agent-org-config-relocation-v1"
ACTIVE_PATH = "agent-org/config-relocation.json"
LEASE_SECONDS = 24 * 60 * 60
EXPECTED_EDITS = [
    {"field": "version", "from": 4, "to": 5},
    {"node": "main", "field": "charter.domain", "replace": "/org.json", "with": "/" + ORG_PATH},
    {"node": "kernel", "field": "charter.excludes", "append": "/" + ORG_PATH,
     "after": "tests/test_bundle_validator.py"},
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def same_object(first, second):
    """Include JSON types and object/array order, not Python's bool == int equality."""
    return json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)


def relocated_org(baseline):
    """The single approved governance edit; deliberately not a general mutation API."""
    if (
        not isinstance(baseline, dict)
        or type(baseline.get("version")) is not int or baseline["version"] != 4
        or baseline.get("root") != "main"
        or not isinstance(baseline.get("nodes"), list)
        or any(not isinstance(n, dict) for n in baseline["nodes"])
        or [n.get("id") for n in baseline.get("nodes", [])] != ["main", "eval", "kernel"]
    ):
        raise ValueError("ConfigRelocation requires the v4 main -> eval/kernel baseline.")
    main, evaluation, kernel = baseline["nodes"]
    if (
        any(not isinstance(n.get("charter"), dict) for n in (main, evaluation, kernel))
        or not isinstance(main["charter"].get("domain"), list)
        or main.get("parent") is not None or main.get("mode") != "Parent"
        or main.get("children") != ["eval", "kernel"]
        or any(n.get("parent") != "main" or n.get("mode") != "Leaf" or n.get("children") != []
               for n in (evaluation, kernel))
        or main.get("charter", {}).get("domain", []).count("/org.json") != 1
        or "/" + ORG_PATH in main["charter"]["domain"]
        or kernel.get("charter", {}).get("excludes") != ["tests/test_bundle_validator.py"]
    ):
        raise ValueError("ConfigRelocation baseline topology or exact charter anchors differ.")
    result = copy.deepcopy(baseline)
    result["version"] = 5
    domain = result["nodes"][0]["charter"]["domain"]
    domain[domain.index("/org.json")] = "/" + ORG_PATH
    result["nodes"][2]["charter"]["excludes"].append("/" + ORG_PATH)
    return result


def safe_path(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe relative path: {relative}")
    result = root / relative
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError(f"Redirected configuration/runtime path: {current}")
    if result.resolve() != result:
        raise ValueError(f"Redirected configuration/runtime path: {result}")
    return result


def git_common(root):
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Do not append a second UUID to long evidence filenames on Windows.
    temporary = path.with_name(f"{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def provider_hash(entry, kind):
    entry = Path(entry).resolve()
    names = ("extension.mjs", "oracle.mjs", "runtime.mjs") if kind == "extension" else (
        "hook.ps1", "org_config.py", "owner_validator.py",
    )
    content = b"".join(
        name.encode("utf-8") + b"\0" + (entry.parent / name).read_bytes().replace(b"\r\n", b"\n") + b"\0"
        for name in names
    )
    return digest(content)


def validate_proposal(proposal):
    """Validate the executable proposal, including its immutable expected edit list."""
    if not isinstance(proposal, dict) or proposal.get("kind") != "ConfigRelocation":
        raise ValueError("Expected kind ConfigRelocation, not SplitProposal.")
    if proposal.get("source") != LEGACY_PATH or proposal.get("destination") != ORG_PATH:
        raise ValueError("ConfigRelocation source/destination must be the exact repository-relative paths.")
    baseline = proposal.get("baseline", {})
    if (
        not isinstance(baseline, dict) or baseline.get("path") != LEGACY_PATH
        or not isinstance(baseline.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", baseline["sha256"])
    ):
        raise ValueError("ConfigRelocation requires an explicit baseline path and SHA-256.")
    expected = relocated_org(baseline.get("org"))
    if not same_object(proposal.get("expected_edits"), EXPECTED_EDITS):
        raise ValueError("ConfigRelocation expected edits differ from the approved transition.")
    if not same_object(proposal.get("proposed_org"), expected):
        raise ValueError("ConfigRelocation may change only the exact version/domain/exclusion entries.")
    return expected


def active_handover(root, *, allow_expired=False):
    common = git_common(root)
    if common is None:
        return None
    active = safe_path(common, ACTIVE_PATH)
    if not active.exists():
        return None
    marker = json.loads(active.read_text(encoding="utf-8"))
    identifier = str(uuid.UUID(marker["id"]))
    if marker != {"id": identifier}:
        raise ValueError("Invalid ConfigRelocation activation marker.")
    file = safe_path(common, f"agent-org/relocations/{identifier}.json")
    proposal = json.loads(file.read_text(encoding="utf-8"))
    validate_proposal(proposal)
    lease = proposal["handover"]
    if (
        lease["id"] != identifier or lease["protocol"] != PROTOCOL
        or lease["phase"] not in ("prepared", "applied")
        or lease["expires_at"] != lease["prepared_at"] + LEASE_SECONDS
        or (not allow_expired and time.time() >= lease["expires_at"])
    ):
        raise ValueError("Invalid or expired ConfigRelocation handover; enforcement remains closed.")
    source, tree = Path(lease["repo"]), Path(lease["worktree"])
    if (
        not source.is_absolute() or source.resolve() != source
        or tree != source / ".worktrees" / lease["session_id"]
        or tree.resolve() != tree
        or git_common(source) != common or git_common(tree) != common
    ):
        raise ValueError("ConfigRelocation is bound to a different source/worktree pair.")
    descriptor = safe_path(common, f"agent-org/runs/{lease['session_id']}.json")
    run = json.loads(descriptor.read_text(encoding="utf-8"))
    if (
        run.get("repo") != str(source) or run.get("path") != str(tree)
        or run.get("session_id") != lease["session_id"] or not run.get("ready")
        or not same_object(run.get("base_org"), proposal["baseline"]["org"])
    ):
        raise ValueError("ConfigRelocation does not match the existing ready run descriptor.")
    original = lease["descriptor"]
    # Integration alone updates these three fields. Never reset the preserved base or inventory.
    for key in set(run) | set(original):
        if key not in {"integrated_head", "integrated_sha", "local_files"} and not same_object(
            run.get(key), original.get(key)
        ):
            raise ValueError(f"ConfigRelocation run descriptor changed field {key}.")
    expected = [
        ("project-worktree", tree / ".github/extensions/agent-org/extension.mjs", "extension"),
        ("project-source", source / ".github/extensions/agent-org/extension.mjs", "extension"),
        ("plugin", None, "extension"),
        ("command-worktree", tree / ".github/agent-org/tools/hook.ps1", "command"),
        ("command-source", source / ".github/agent-org/tools/hook.ps1", "command"),
    ]
    providers = lease["providers"]
    if (
        not isinstance(providers, list) or len(providers) != len(expected)
        or any(not isinstance(p, dict) for p in providers)
        or len({p.get("entry") for p in providers}) != len(expected)
    ):
        raise ValueError("ConfigRelocation must inventory both project roots, plugin and command providers.")
    fingerprints = {
        "extension": provider_hash(expected[0][1], "extension"),
        "command": provider_hash(expected[3][1], "command"),
    }
    for provider, (label, entry, kind) in zip(providers, expected):
        if (
            set(provider) != {"label", "entry", "kind", "fingerprint"}
            or provider["label"] != label or provider["kind"] != kind
            or not Path(provider["entry"]).is_absolute()
            or (entry is not None and provider["entry"] != str(entry))
            or provider["fingerprint"] != fingerprints[kind]
        ):
            raise ValueError("ConfigRelocation provider inventory or ready revision changed.")
    return proposal


def config_path(root, *, explicit=None, allow_uninstalled=False):
    root = Path(root).resolve()
    canonical, legacy = safe_path(root, ORG_PATH), safe_path(root, LEGACY_PATH)
    if canonical.exists() and legacy.exists():
        raise ValueError("Conflicting live organization files: org.json and .github/agent-org/org.json.")
    if explicit is not None:
        path = Path(explicit)
        return path if path.is_absolute() else safe_path(root, explicit)
    proposal = active_handover(root)
    if proposal is not None:
        lease = proposal["handover"]
        if str(root) in (lease["repo"], lease["worktree"]):
            if legacy.is_file():
                if lease["phase"] == "applied" and str(root) == lease["worktree"]:
                    raise ValueError("Applied ConfigRelocation cannot return to the legacy worktree file.")
                if digest(legacy.read_bytes()) != proposal["baseline"]["sha256"]:
                    raise ValueError("ConfigRelocation legacy baseline changed; refusing selection.")
                return legacy
            if canonical.is_file() and lease["phase"] == "applied":
                value = json.loads(canonical.read_text(encoding="utf-8-sig"))
                if not same_object(value, proposal["proposed_org"]):
                    raise ValueError("ConfigRelocation canonical state differs from the exact proposal.")
                return canonical
            raise ValueError("ConfigRelocation candidate missing or present in the wrong phase.")
    if canonical.is_file():
        return canonical
    if legacy.exists():
        raise ValueError("Legacy org.json requires explicit bootstrap --migrate-legacy or ConfigRelocation.")
    if allow_uninstalled and not (root / ".github/agent-org").exists():
        return None
    raise ValueError(f"Missing canonical organization: {canonical}")


def record_provider(payload, root, selected, event):
    """Operational activation evidence, not a proof of authorship or a security boundary."""
    provider = payload.get("agentOrgProvider")
    if not provider:
        return
    proposal = active_handover(root)
    if proposal is None:
        return
    lease = proposal["handover"]
    if str(root) not in (lease["repo"], lease["worktree"]):
        return
    entry = str(Path(provider["entry"]).resolve())
    expected = next((p for p in lease["providers"] if p["entry"] == entry), None)
    if (
        expected is None or provider.get("protocol") != PROTOCOL
        or provider.get("fingerprint") != expected["fingerprint"]
        or provider_hash(entry, expected["kind"]) != expected["fingerprint"]
    ):
        raise ValueError(f"Unexpected or stale agent-org provider: {entry}")
    common = git_common(root)
    file = safe_path(common, f"agent-org/relocations/{lease['id']}/{digest(entry.encode('utf-8'))}.json")
    record = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {"entry": entry, "events": {}}
    key = f"{lease['phase']}:{event}:{root}"
    record["events"][key] = {
        "fingerprint": provider["fingerprint"], "protocol": PROTOCOL,
        "org": str(selected), "time": time.time(), "pid": provider.get("pid"),
        "session_id": payload.get("sessionId"),
    }
    write_json(file, record)
