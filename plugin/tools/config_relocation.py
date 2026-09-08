"""The one approved v4 -> v5 ConfigRelocation; not a general organization editor.

Host prepares the bounded handover and switches CLI cwd to its ready shared worktree.
Only the Host-invoked splitter applies it. Root integrates normally; Host verifies both
reloaded providers at source before finishing. No command installs or disables a plugin.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap  # noqa: E402
import org_config as config  # noqa: E402
import owner_validator as ov  # noqa: E402
import worktree as wt  # noqa: E402


def _proposal_path(common, identifier):
    return config.safe_path(common, f"agent-org/relocations/{identifier}.json")


def _legacy(root, sha256):
    file = config.config_path(root, explicit=config.LEGACY_PATH)
    if (root / config.ORG_PATH).exists() or not file.is_file():
        raise ValueError("ConfigRelocation requires one legacy candidate and no destination collision.")
    data = file.read_bytes()
    if config.digest(data) != sha256:
        raise ValueError(f"ConfigRelocation baseline SHA-256 mismatch: {root}")
    return data, json.loads(data.decode("utf-8-sig"))


def _runtime(root, org_path):
    result = bootstrap.check_runtime(root, org_path=org_path)
    if result["status"] != "ok":
        raise ValueError("Complete runtime readiness before activation: " + ", ".join(result["drift"]))


def _coverage(root, baseline, proposed, run):
    paths = wt._paths(root, run["base_sha"])
    result = ov.check_config_relocation(baseline, proposed, paths + [config.ORG_PATH])
    if result["status"] != "ok":
        raise ValueError("ConfigRelocation ownership preflight failed: " + json.dumps(result["violations"]))


def prepare(repo, session, baseline_sha256, plugin_entry):
    """Create only a GUID proposal and activation marker in common Git metadata."""
    root = wt._root(repo)
    common = ov.git_common_dir(root)
    with wt._lock(common):
        if config.safe_path(common, config.ACTIVE_PATH).exists():
            raise ValueError("A ConfigRelocation is already active; inspect it, never overwrite it.")
        run = wt._load(root, common, session)
        wt._ready(run)
        wt._source_ready(run)
        source, tree = Path(run["repo"]), Path(run["path"])
        data, baseline = _legacy(source, baseline_sha256.lower())
        tree_data, _ = _legacy(tree, baseline_sha256.lower())
        if data != tree_data or not config.same_object(run["base_org"], baseline):
            raise ValueError("The existing run descriptor and both live baselines must agree exactly.")
        proposed = config.relocated_org(baseline)
        _runtime(tree, config.LEGACY_PATH)
        _coverage(tree, baseline, proposed, run)
        # Entries may still contain the old code. Expected fingerprints come from READY assets,
        # not from those old disk files. Rebinding a changed plugin location checks actual bytes.
        extension = ".github/extensions/agent-org/extension.mjs"
        command = ".github/agent-org/tools/hook.ps1"
        providers = []
        for label, entry, kind in (
            ("project-worktree", tree / extension, "extension"),
            ("project-source", source / extension, "extension"),
            ("plugin", Path(plugin_entry).resolve(), "extension"),
            ("command-worktree", tree / command, "command"),
            ("command-source", source / command, "command"),
        ):
            reference = tree / (extension if kind == "extension" else command)
            providers.append({"label": label, "entry": str(entry), "kind": kind,
                              "fingerprint": config.provider_hash(reference, kind)})
        if len({p["entry"] for p in providers}) != len(providers):
            raise ValueError("The plugin and project must be distinct provider entries.")
        identifier, now = str(uuid.uuid4()), time.time()
        proposal = {
            "kind": "ConfigRelocation", "source": config.LEGACY_PATH, "destination": config.ORG_PATH,
            "baseline": {"path": config.LEGACY_PATH, "sha256": config.digest(data), "org": baseline},
            "expected_edits": copy.deepcopy(config.EXPECTED_EDITS), "proposed_org": proposed,
            "handover": {
                "id": identifier, "protocol": config.PROTOCOL, "phase": "prepared",
                "prepared_at": now, "expires_at": now + config.LEASE_SECONDS,
                "repo": str(source), "worktree": str(tree), "session_id": run["session_id"],
                "descriptor": run, "providers": providers,
            },
        }
        config.validate_proposal(proposal)
        file = _proposal_path(common, identifier)
        config.write_json(file, proposal)
        config.write_json(config.safe_path(common, config.ACTIVE_PATH), {"id": identifier})
        return {"proposal": str(file), "phase": "prepared", "expires_at": now + config.LEASE_SECONDS}


def _active(repo, proposal_file=None, allow_expired=False):
    root = wt._root(repo)
    proposal = config.active_handover(root, allow_expired=allow_expired)
    if proposal is None:
        raise ValueError("No active ConfigRelocation.")
    lease = proposal["handover"]
    if str(root) not in (lease["repo"], lease["worktree"]):
        raise ValueError("This is not the ConfigRelocation source or shared worktree.")
    file = _proposal_path(ov.git_common_dir(root), lease["id"])
    if proposal_file is not None and Path(proposal_file).resolve() != file:
        raise ValueError("Use the exact active GUID proposal, not a different candidate.")
    return proposal, file


def bind_plugin(repo, plugin_entry):
    """Host-only operational rebind after supported local plugin replacement; no org edits."""
    root = wt._root(repo)
    with wt._lock(ov.git_common_dir(root)):
        proposal, file = _active(root)
        entry = str(Path(plugin_entry).resolve())
        providers = proposal["handover"]["providers"]
        plugin = next(p for p in providers if p["label"] == "plugin")
        if any(p["entry"] == entry for p in providers if p["label"] != "plugin"):
            raise ValueError("The plugin must remain a distinct provider.")
        if config.provider_hash(entry, "extension") != plugin["fingerprint"]:
            raise ValueError("The plugin has not been updated to the ready runtime.")
        old = _evidence_file(file.parent.parent.parent, proposal, plugin)
        old.unlink(missing_ok=True)
        plugin["entry"] = entry
        config.write_json(file, proposal)
        return {"plugin_entry": entry, "phase": proposal["handover"]["phase"]}


def _evidence_file(common, proposal, provider):
    return config.safe_path(
        common, f"agent-org/relocations/{proposal['handover']['id']}/"
        f"{config.digest(provider['entry'].encode('utf-8'))}.json",
    )


def evidence(proposal, target):
    lease = proposal["handover"]
    root = Path(lease["worktree"] if target == "worktree" else lease["repo"])
    common = ov.git_common_dir(root)
    labels = {f"project-{target}", "plugin", f"command-{target}"}
    missing = []
    for provider in lease["providers"]:
        if provider["label"] not in labels:
            continue
        file = _evidence_file(common, proposal, provider)
        events = json.loads(file.read_text(encoding="utf-8"))["events"] if file.exists() else {}
        for event in ("preToolUse", "postToolUse"):
            value = events.get(f"{lease['phase']}:{event}:{root}", {})
            selected = config.LEGACY_PATH if lease["phase"] == "prepared" else config.ORG_PATH
            if (
                value.get("fingerprint") != provider["fingerprint"]
                or value.get("protocol") != config.PROTOCOL
                or value.get("org") != str(root / selected)
                or value.get("time", 0) < lease.get("applied_at", lease["prepared_at"])
                or not value.get("session_id")
                or (provider["kind"] == "extension" and not value.get("pid"))
            ):
                missing.append(f"{provider['label']}:{event}:{root}")
    return missing


def status(repo, require=None):
    proposal, file = _active(repo)
    lease = proposal["handover"]
    selected = {label: str(config.config_path(lease[key])) for label, key in (
        ("source", "repo"), ("worktree", "worktree"),
    )}
    missing = evidence(proposal, require) if require else []
    return {"status": "waiting" if missing else "ok", "phase": lease["phase"],
            "proposal": str(file), "selected": selected, "missing_evidence": missing,
            "providers": lease["providers"]}


def apply(repo, proposal_file, acting):
    """Splitter-only live move, after exact baseline, runtime, storage and provider checks."""
    if acting != "splitter":
        raise ValueError("Only the Host-invoked splitter may apply ConfigRelocation.")
    root = wt._root(repo)
    common = ov.git_common_dir(root)
    with wt._lock(common):
        proposal, file = _active(root, proposal_file)
        lease = proposal["handover"]
        if lease["phase"] != "prepared":
            raise ValueError("ConfigRelocation was already applied; it cannot bump version again.")
        source, tree = Path(lease["repo"]), Path(lease["worktree"])
        run = wt._load(root, common, lease["session_id"])
        if not config.same_object(run, lease["descriptor"]):
            raise ValueError("Run descriptor changed after preparation; preserve it and stop.")
        wt._source_ready(run)
        _legacy(source, proposal["baseline"]["sha256"])
        data, baseline = _legacy(tree, proposal["baseline"]["sha256"])
        if not config.same_object(baseline, proposal["baseline"]["org"]):
            raise ValueError("Executable proposal baseline differs from the live baseline.")
        _runtime(tree, config.LEGACY_PATH)
        _coverage(tree, baseline, proposal["proposed_org"], run)
        missing = evidence(proposal, "worktree")
        if missing:
            raise ValueError("Reload and observe every worktree provider before applying: " + ", ".join(missing))
        canonical, legacy = config.safe_path(tree, config.ORG_PATH), tree / config.LEGACY_PATH
        if baseline.get("storage", "local") == "local":
            if config.LEGACY_PATH in wt._tracked(tree) or config.ORG_PATH in wt._tracked(tree):
                raise ValueError("Local relocation cannot hide a tracked live config.")
            if config.LEGACY_PATH not in run["local_files"]:
                raise ValueError("The local descriptor must already inventory the legacy live file.")
            wt._exclude_local(common, [config.ORG_PATH])
        elif wt._git(tree, "check-ignore", "--no-index", "-q", config.ORG_PATH, check=False).returncode != 1:
            raise ValueError("Tracked relocation destination must not be ignored.")
        new_data = (json.dumps(proposal["proposed_org"], indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        # Rename first: never have two live candidates, even while writing the new content.
        legacy.rename(canonical)
        try:
            wt._write_bytes(canonical, new_data)
            lease.update(phase="applied", applied_at=time.time())
            config.write_json(file, proposal)
        except (OSError, ValueError):
            if not legacy.exists() and canonical.is_file() and canonical.read_bytes() in (data, new_data):
                wt._write_bytes(canonical, data)
                canonical.rename(legacy)
            raise
        return {"applied": True, "proposal": str(file), "changed": [config.LEGACY_PATH, config.ORG_PATH]}


def finish(repo):
    """Remove only this completed handover's metadata after root integration and final reload."""
    root = wt._root(repo)
    common = ov.git_common_dir(root)
    with wt._lock(common):
        proposal, file = _active(root, allow_expired=True)
        lease = proposal["handover"]
        if lease["phase"] != "applied":
            raise ValueError("Apply and integrate the relocation before finishing.")
        source, tree = Path(lease["repo"]), Path(lease["worktree"])
        run = wt._load(root, common, lease["session_id"])
        if (
            run.get("integrated_head") != wt._git(tree, "rev-parse", "HEAD").stdout.strip()
            or run.get("integrated_sha") != wt._git(source, "rev-parse", "HEAD").stdout.strip()
            or not wt._ancestor(source, run["integrated_head"], "HEAD")
            or wt._status(source) or wt._status(tree)
        ):
            raise ValueError("Root must integrate the existing descriptor before handover cleanup.")
        for directory in (source, tree):
            if (directory / config.LEGACY_PATH).exists():
                raise ValueError("A legacy live file remains; no handover cleanup.")
            value = json.loads(config.safe_path(directory, config.ORG_PATH).read_text(encoding="utf-8-sig"))
            if not config.same_object(value, proposal["proposed_org"]):
                raise ValueError("Integrated canonical config differs from the approved proposal.")
            _runtime(directory, config.ORG_PATH)
        missing = evidence(proposal, "source")
        if missing:
            raise ValueError("Observe the final source project/plugin and command hooks: " + ", ".join(missing))
        if any(config.provider_hash(p["entry"], p["kind"]) != p["fingerprint"] for p in lease["providers"]):
            raise ValueError("Provider disk assets changed after readiness.")
        directory = file.with_suffix("")
        known = {_evidence_file(common, proposal, provider) for provider in lease["providers"]}
        if directory.exists() and set(directory.iterdir()) - known:
            raise ValueError("Unknown handover metadata was preserved; inspect it before cleanup.")
        config.safe_path(common, config.ACTIVE_PATH).unlink()
        for provider in lease["providers"]:
            _evidence_file(common, proposal, provider).unlink(missing_ok=True)
        if directory.exists():
            directory.rmdir()  # Never delete unknown metadata.
        file.unlink()
        return {"finished": True, "removed_proposal": str(file), "org_path": config.ORG_PATH}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "bind-plugin", "status", "apply", "finish"):
        sub = commands.add_parser(name)
        sub.add_argument("--repo", required=True)
        if name == "prepare":
            sub.add_argument("--session", required=True)
            sub.add_argument("--baseline-sha256", required=True)
        if name in ("prepare", "bind-plugin"):
            sub.add_argument("--plugin-entry", required=True, help="actual extension entry from extensions_manage list")
        if name == "apply":
            sub.add_argument("--proposal", required=True, help="Host-approved executable GUID proposal")
            sub.add_argument("--acting", required=True, choices=("splitter",))
        if name == "status":
            sub.add_argument("--require", choices=("worktree", "source"))
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    if command == "apply":
        args["proposal_file"] = args.pop("proposal")
    try:
        result = globals()[command.replace("-", "_")](**args)
    except (KeyError, OSError, TypeError, ValueError, subprocess.CalledProcessError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 1 if result.get("status") == "waiting" else 0


if __name__ == "__main__":
    sys.exit(main())
