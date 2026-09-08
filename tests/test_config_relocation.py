"""Sandbox-only relocation: tests never migrate the repository's live organization."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin" / "tools"))
import bootstrap  # noqa: E402
import config_relocation as relocation  # noqa: E402
import org_config as config  # noqa: E402
import owner_validator as ov  # noqa: E402
import worktree as wt  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"


def baseline_org(storage="tracked"):
    return {
        "version": 4, "root": "main", "nodes": [
            {"id": "main", "parent": None, "children": ["eval", "kernel"], "mode": "Parent",
             "charter": {"domain": ["/.gitignore", "/AGENTS.md", "/conftest.py", "/org.json",
                                    "/pytest.ini", "/README.md"],
                         "concerns": ["repository-wide configuration and documentation"], "excludes": []}},
            {"id": "eval", "parent": "main", "children": [], "mode": "Leaf",
             "charter": {"domain": ["eval/**", "tests/test_bundle_validator.py"],
                         "concerns": ["evaluation"], "excludes": []}},
            {"id": "kernel", "parent": "main", "children": [], "mode": "Leaf",
             "charter": {"domain": [".github/**", "plugin/**", "tests/**"],
                         "concerns": ["runtime"], "excludes": ["tests/test_bundle_validator.py"]}},
        ], "collaboration": "agents", "scope": ["**"], "storage": storage,
    }


def write(root, relative, value):
    file = root / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(value if isinstance(value, bytes) else value.encode("utf-8"))
    return file


def git(root, *args):
    return wt._git(root, *args).stdout.strip()


@pytest.fixture
def legacy_run(tmp_path, monkeypatch, request):
    """Reproduce a descriptor made by the old runtime; no production fallback is installed."""
    storage = getattr(request, "param", "tracked")
    source = tmp_path / "source"
    source.mkdir()
    for args in (
        ("init", "-q", "-b", "main"), ("config", "core.autocrlf", "false"),
        ("config", "core.hooksPath", str(source / ".git/no-hooks")),
        ("config", "commit.gpgsign", "false"),
        ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Test"),
    ):
        git(source, *args)
    baseline = baseline_org(storage)
    data = (json.dumps(baseline, indent=2) + "\n").encode("utf-8")
    old_runtime = bootstrap.runtime_files(baseline)
    for relative in (
        ".github/agent-org/tools/config_relocation.py", ".github/agent-org/tools/org_config.py",
        ".github/extensions/agent-org/oracle.mjs",
    ):
        old_runtime.pop(relative)
    old_runtime[config.LEGACY_PATH] = data
    old_runtime[config.SEED_PATH] = json.dumps(bootstrap.default_org()).encode("utf-8")
    for name, content in old_runtime.items():
        write(source, name, content)
    write(source, "README.md", "baseline\n")
    write(source, "eval/example.txt", "evaluation\n")
    common = ov.git_common_dir(source)
    wt._exclude(common, {"/.github/agent-org/**/__pycache__/"})
    if storage == "local":
        wt._exclude_local(common, old_runtime)
    git(source, "add", "--all")
    git(source, "commit", "-q", "-m", "test: legacy baseline")
    # Fixture setup emulates only the old creator's file inventory and explicit config read.
    with monkeypatch.context() as patch:
        patch.setattr(wt, "_read_org", lambda root: json.loads((root / config.LEGACY_PATH).read_text()))
        patch.setattr(wt, "_overlay_names", lambda root, *orgs, **kwargs: set(old_runtime))
        run = wt.create(source, "preserved-run")
    run.update(integrated_head=git(source, "rev-parse", "HEAD"), integrated_sha=git(source, "rev-parse", "HEAD"))
    config.write_json(common / "agent-org/runs/preserved-run.json", run)
    tree = Path(run["path"])
    for name, content in bootstrap.runtime_files(baseline).items():
        write(tree, name, content)
    (tree / config.SEED_PATH).unlink()
    cache = tmp_path / "plugin-cache"
    shutil.copytree(PLUGIN / "extensions/agent-org", cache)
    plugin_entry = cache / "extension.mjs"
    descriptor = (common / "agent-org/runs/preserved-run.json").read_bytes()
    return source, tree, run, data, plugin_entry, descriptor


def prepare(fixture):
    source, tree, run, data, plugin_entry, _ = fixture
    result = relocation.prepare(source, run["session_id"], config.digest(data), plugin_entry)
    file = Path(result["proposal"])
    return file, json.loads(file.read_text(encoding="utf-8"))


def observe(proposal, target="worktree", cwd_override=None):
    """Execute real JS oracle and PowerShell hook entry points, not forged evidence files."""
    lease = proposal["handover"]
    root = lease["worktree"] if target == "worktree" else lease["repo"]
    extensions = [p for p in lease["providers"] if p["label"] in (f"project-{target}", "plugin")]
    payload = {"cwd": root, "sessionId": "observed-session", "toolName": "view",
               "toolArgs": {"path": str(Path(root) / "README.md")}}
    if cwd_override is not None:
        payload.update(cwd=str(cwd_override), agentOrgContext={
            "node": "kernel", "run_id": lease["session_id"], "worktree": root,
        })
    script = """
      for (const entry of JSON.parse(process.argv[1])) {
        const { pathToFileURL } = await import('node:url');
        const { dirname, join } = await import('node:path');
        const { createOracle } = await import(pathToFileURL(join(dirname(entry), 'oracle.mjs')));
        const call = createOracle(entry);
        for (const event of ['preToolUse', 'postToolUse']) {
          const result = await call(event, JSON.parse(process.argv[2]));
          if (result.permissionDecision === 'deny' || result.additionalContext) throw new Error(JSON.stringify(result));
        }
      }
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps([p["entry"] for p in extensions]),
         json.dumps(payload)], capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for event in ("preToolUse", "postToolUse"):
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(Path(root) / ".github/agent-org/tools/hook.ps1"), "-Event", event],
            input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == ({"permissionDecision": "allow"} if event == "preToolUse" else {})


@pytest.mark.parametrize("storage", ["local", "tracked"])
def test_exact_transition_preserves_every_other_entry_and_repo_relative_owners(storage):
    old = baseline_org(storage)
    before = json.dumps(old)
    new = config.relocated_org(old)
    assert json.dumps(old) == before
    assert list(new) == list(old)
    assert new["nodes"][0]["charter"]["domain"][3] == "/" + config.ORG_PATH
    assert new["nodes"][2]["charter"]["excludes"] == ["tests/test_bundle_validator.py", "/" + config.ORG_PATH]
    paths = ["org.json", config.ORG_PATH, "README.md", "plugin/tools/hook.ps1",
             ".github/agents/main.md", "eval/example.txt", "tests/test_bundle_validator.py"]
    assert ov.check_config_relocation(old, new, paths)["status"] == "ok"
    assert ov.ownership(new, config.ORG_PATH)["owner"] == "main"
    assert ov.ownership(new, "plugin/tools/hook.ps1")["owner"] == "kernel"
    assert ov.check_split(old, new)["status"] == "violations"


@pytest.mark.parametrize("change", [
    lambda org: org.update(version=6),
    lambda org: org.update(version=True),
    lambda org: org.update(root="kernel"),
    lambda org: org.update(storage="local"),
    lambda org: org.update(scope=["plugin/**"]),
    lambda org: org.update(collaboration="hybrid"),
    lambda org: org["nodes"].reverse(),
    lambda org: org["nodes"][0].update(children=["kernel", "eval"]),
    lambda org: org["nodes"][1].update(parent="kernel"),
    lambda org: org["nodes"][1].update(mode="Parent"),
    lambda org: org["nodes"][0]["charter"]["domain"].reverse(),
    lambda org: org["nodes"][1]["charter"]["concerns"].append("unrelated"),
    lambda org: org["nodes"][2]["charter"].update(excludes=["tests/test_bundle_validator.py"]),
    lambda org: org["nodes"][2]["charter"].update(excludes=["/" + config.ORG_PATH, "tests/test_bundle_validator.py"]),
    lambda org: org["nodes"][2]["charter"].update(excludes=["tests/test_bundle_validator.py", config.ORG_PATH]),
])
def test_relocation_rejects_unrelated_edits_wrong_exclusion_and_version(change):
    old = baseline_org()
    new = config.relocated_org(old)
    change(new)
    paths = [config.LEGACY_PATH, config.ORG_PATH, "README.md", "eval/example.txt", "plugin/tools/hook.ps1"]
    assert ov.check_config_relocation(old, new, paths)["status"] == "violations"


def test_relocation_rejects_wrong_original_endpoint_owner_despite_valid_projection():
    old = baseline_org()
    old["nodes"][0]["charter"]["excludes"].append("/org.json")
    old["nodes"][1]["charter"]["domain"].append("/org.json")
    new = config.relocated_org(old)
    paths = [config.LEGACY_PATH, config.ORG_PATH, "eval/example.txt"]
    assert ov.ownership(old, config.LEGACY_PATH)["owner"] == "eval"
    assert ov.validate(new, [config.ORG_PATH, "eval/example.txt"])["status"] == "ok"
    result = ov.check_config_relocation(old, new, paths)
    assert result["status"] == "violations"
    assert any(v["evidence"] == "The legacy live file must belong to main." for v in result["violations"])


def test_discovery_is_canonical_only_and_dual_candidates_conflict_before_parsing(tmp_path):
    write(tmp_path, "org.json", "{}")
    with pytest.raises(ValueError, match="requires explicit"):
        config.config_path(tmp_path)
    assert config.config_path(tmp_path, explicit="org.json") == tmp_path / "org.json"
    write(tmp_path, config.ORG_PATH, "{malformed")
    for explicit in (None, "org.json", config.ORG_PATH):
        with pytest.raises(ValueError, match="Conflicting live"):
            config.config_path(tmp_path, explicit=explicit)
    (tmp_path / "org.json").unlink()
    assert config.config_path(tmp_path) == tmp_path / config.ORG_PATH


def test_preparation_preserves_live_files_descriptor_and_blocks_unobserved_apply(legacy_run):
    source, tree, run, data, _, descriptor = legacy_run
    file, proposal = prepare(legacy_run)
    assert file.parent == ov.git_common_dir(source) / "agent-org/relocations"
    assert config.validate_proposal(proposal) == config.relocated_org(run["base_org"])
    with pytest.raises(ValueError, match="Reload and observe"):
        relocation.apply(tree, file, "splitter")
    with pytest.raises(ValueError, match="Only the Host-invoked splitter"):
        relocation.apply(tree, file, "main")
    assert config.config_path(source) == source / "org.json"
    assert config.config_path(tree) == tree / "org.json"
    assert (source / "org.json").read_bytes() == (tree / "org.json").read_bytes() == data
    assert not (tree / config.ORG_PATH).exists()
    assert (ov.git_common_dir(source) / "agent-org/runs/preserved-run.json").read_bytes() == descriptor
    assert wt.create(source, run["session_id"]) == run
    with pytest.raises(wt.WorktreeError, match="before creating"):
        wt.create(source, "another-run")
    with pytest.raises(ValueError, match="already active"):
        prepare(legacy_run)


@pytest.mark.parametrize("damage", ["source", "worktree", "descriptor", "proposal", "collision", "hash", "providers"])
def test_apply_rejects_wrong_baselines_and_collision_without_writes(legacy_run, damage):
    source, tree, _, data, _, _ = legacy_run
    file, proposal = prepare(legacy_run)
    if damage in ("source", "worktree"):
        write(source if damage == "source" else tree, "org.json", data + b" ")
    elif damage == "descriptor":
        descriptor = ov.git_common_dir(source) / "agent-org/runs/preserved-run.json"
        run = json.loads(descriptor.read_text())
        run["base_sha"] = "0" * 40
        config.write_json(descriptor, run)
    elif damage == "proposal":
        proposal["proposed_org"]["storage"] = "local"
        config.write_json(file, proposal)
    elif damage == "hash":
        proposal["baseline"]["sha256"] = "0" * 64
        config.write_json(file, proposal)
    elif damage == "providers":
        proposal["handover"]["providers"].pop()
        config.write_json(file, proposal)
    else:
        write(tree, config.ORG_PATH, "{")
    before = (source / "org.json").read_bytes(), (tree / "org.json").read_bytes(), file.read_bytes()
    with pytest.raises(ValueError):
        relocation.apply(tree, file, "splitter")
    assert ((source / "org.json").read_bytes(), (tree / "org.json").read_bytes(), file.read_bytes()) == before


def test_expired_handover_fails_closed_in_both_roots(legacy_run, monkeypatch):
    source, tree, *_ = legacy_run
    _, proposal = prepare(legacy_run)
    monkeypatch.setattr(config.time, "time", lambda: proposal["handover"]["expires_at"] + 1)
    for root in (source, tree):
        with pytest.raises(ValueError, match="expired"):
            config.config_path(root)
        response = ov.process_hook({"cwd": str(root), "toolName": "view"})
        assert response["permissionDecision"] == "deny"


def test_applied_unstaged_relocation_passes_owner_validation_before_integration(legacy_run, monkeypatch):
    source, tree, run, data, _, descriptor = legacy_run
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "0")

    def index_contents(root):
        # Same-content fixture writes can trigger Git stat-cache refreshes without staging.
        return (
            wt._git(root, "ls-files", "--stage", "-z").stdout,
            wt._git(root, "diff", "--cached", "--raw", "--no-abbrev", "--no-renames", "-z", "HEAD", "--").stdout,
        )

    index_before, source_index_before = index_contents(tree), index_contents(source)
    assert index_before[1] == source_index_before[1] == ""
    source_head = git(source, "rev-parse", "HEAD")
    file, proposal = prepare(legacy_run)
    observe(proposal)
    assert relocation.apply(tree, file, "splitter")["applied"]

    # A real unstaged deletion + untracked destination, not a pre-staged rename.
    assert index_contents(tree) == index_before
    status = wt._git(tree, "status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines()
    assert " D org.json" in status
    assert "?? " + config.ORG_PATH in status
    assert (source / config.LEGACY_PATH).read_bytes() == data
    assert (ov.git_common_dir(source) / "agent-org/runs/preserved-run.json").read_bytes() == descriptor
    assert config.config_path(tree) == tree / config.ORG_PATH
    proposal_before = file.read_bytes()
    proposal = json.loads(proposal_before)
    old = proposal["baseline"]["org"]
    new = json.loads((tree / proposal["destination"]).read_text())
    assert config.digest(data) == proposal["baseline"]["sha256"]
    assert config.same_object(json.loads(data), old)
    assert config.same_object(new, config.validate_proposal(proposal))

    # Cached deletion endpoints intentionally remain in every unprojected inventory.
    endpoints = {config.LEGACY_PATH, config.ORG_PATH}
    inventory = ov.git_tracked(tree)
    paths = wt._paths(tree, run["base_sha"])
    assert endpoints <= set(inventory)
    assert endpoints <= set(ov.git_changed(tree, run["base_sha"]))
    assert endpoints <= set(paths)
    command = [sys.executable, "-B", str(PLUGIN / "tools/owner_validator.py"), "--root", str(tree)]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert {(v["rule"], v["path"]) for v in json.loads(result.stdout)["violations"]} == {
        ("uncovered", proposal["source"]),
    }

    # Check the original deletion owner and exact transition BEFORE candidate projection.
    assert ov.check_containment(old, "main", [proposal["source"]])["status"] == "ok"
    assert ov.ownership(new, proposal["destination"])["owner"] == "main"
    assert ov.check_config_relocation(old, new, paths)["status"] == "ok"
    projected = sorted({proposal["destination"] if p == proposal["source"] else p for p in paths})
    assert set(projected) == (set(paths) - {proposal["source"]}) | {proposal["destination"]}
    assert config.SEED_PATH in projected  # An unrelated cached deletion is not filtered.
    assert not (tree / config.SEED_PATH).exists()
    assert ov.validate(new, projected)["status"] == "ok"
    result = subprocess.run(
        command + ["--paths", *projected], capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"status": "ok", "violations": []}
    assert index_contents(tree) == index_before
    assert file.read_bytes() == proposal_before

    # Only the sandbox index is staged, after the projected checks; default semantics stay intact.
    git(tree, "add", "--", proposal["source"], proposal["destination"])
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"status": "ok", "violations": []}
    assert config.LEGACY_PATH not in ov.git_tracked(tree)
    assert config.ORG_PATH in ov.git_tracked(tree)
    assert endpoints <= set(ov.git_changed(tree, run["base_sha"]))
    assert endpoints <= set(wt._paths(tree, run["base_sha"]))
    assert (ov.git_common_dir(source) / "agent-org/runs/preserved-run.json").read_bytes() == descriptor
    assert index_contents(source) == source_index_before
    assert git(source, "rev-parse", "HEAD") == source_head
    assert git(source, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert (source / config.LEGACY_PATH).read_bytes() == data
    assert not (source / config.ORG_PATH).exists()


def test_relocation_projection_preserves_unrelated_deletions_unowned_paths_and_containment(legacy_run):
    _, tree, run, _, _, _ = legacy_run
    file, proposal = prepare(legacy_run)
    observe(proposal)
    assert relocation.apply(tree, file, "splitter")["applied"]
    old, new = proposal["baseline"]["org"], proposal["proposed_org"]

    # Stage this unrelated path only in the sandbox to reproduce a cached, unowned deletion.
    deleted = write(tree, "unowned-deleted.txt", "not approved\n")
    git(tree, "add", "--", deleted.name)
    deleted.unlink()
    (tree / "eval/example.txt").unlink()
    write(tree, "unowned-new.txt", "not approved either\n")
    unrelated = {"unowned-deleted.txt", "unowned-new.txt", "eval/example.txt"}
    paths = wt._paths(tree, run["base_sha"])
    assert unrelated <= set(ov.git_tracked(tree))
    assert unrelated - {deleted.name} <= set(ov.git_changed(tree, run["base_sha"]))
    # A staged addition deleted on disk cancels out of the HEAD diff, but not the staged inventory.
    assert deleted.name in git(tree, "diff", "--cached", "--name-only", "--no-renames", "-z").split("\0")
    assert unrelated <= set(paths)
    projected = sorted({proposal["destination"] if p == proposal["source"] else p for p in paths})
    assert unrelated <= set(projected)
    for result in (ov.check_config_relocation(old, new, paths), ov.validate(new, projected)):
        assert result["status"] == "violations"
        assert {"unowned-deleted.txt", "unowned-new.txt"} <= {
            v["path"] for v in result["violations"] if v["rule"] == "uncovered"
        }
    result = subprocess.run(
        [sys.executable, "-B", str(PLUGIN / "tools/owner_validator.py"), "--root", str(tree),
         "--paths", *projected],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    containment = ov.check_containment(new, "kernel", projected)
    assert containment["status"] == "violations"
    assert {("eval/example.txt", "eval"), (proposal["destination"], "main")} <= {
        (v["path"], v["owner"]) for v in containment["violations"] if v["rule"] == "containment"
    }
    removed = ov.check_containment(old, "kernel", [proposal["source"]])
    assert removed["status"] == "violations"
    assert removed["violations"][0]["rule"] == "containment"
    assert removed["violations"][0]["owner"] == "main"


@pytest.mark.parametrize("legacy_run", ["tracked", "local"], indirect=True)
def test_real_providers_splitter_integration_and_final_cleanup(legacy_run):
    source, tree, run, data, _, descriptor = legacy_run
    file, proposal = prepare(legacy_run)
    observe(proposal)
    assert relocation.status(tree, require="worktree")["status"] == "ok"
    assert relocation.apply(tree, file, "splitter")["applied"]
    assert (source / "org.json").read_bytes() == data
    assert not (tree / "org.json").exists()
    assert json.loads((tree / config.ORG_PATH).read_text()) == proposal["proposed_org"]
    assert (ov.git_common_dir(source) / "agent-org/runs/preserved-run.json").read_bytes() == descriptor
    with pytest.raises(ValueError, match="already applied"):
        relocation.apply(tree, file, "splitter")
    assert relocation.status(tree, require="worktree")["status"] == "waiting"
    # Source still has the legacy org: bound tools must nevertheless read THIS worktree's canonical org.
    observe(json.loads(file.read_text()), cwd_override=source)
    assert relocation.status(tree, require="worktree")["status"] == "ok"
    result = wt.integrate(source, run["session_id"])
    assert result["integrated"], result
    with pytest.raises(wt.WorktreeError, match="Finish the active"):
        wt.cleanup(source, run["session_id"])
    with pytest.raises(ValueError, match="Observe the final source"):
        relocation.finish(source)
    observe(json.loads(file.read_text()), "source")
    assert relocation.status(source, require="source")["status"] == "ok"
    assert relocation.finish(source)["finished"]
    assert not file.exists()
    assert not (ov.git_common_dir(source) / config.ACTIVE_PATH).exists()
    for root in (source, tree):
        assert not (root / "org.json").exists()
        assert not (root / config.SEED_PATH).exists()
        assert bootstrap.check_runtime(root)["status"] == "ok"
        assert config.config_path(root) == root / config.ORG_PATH
    current = json.loads((ov.git_common_dir(source) / "agent-org/runs/preserved-run.json").read_text())
    for field in ("base_org", "base_sha", "base_branch", "runtime_files", "branch", "ready"):
        assert current[field] == run[field]
    if run["base_org"]["storage"] == "local":
        assert current["local_files"]["org.json"] is None
        assert current["local_files"][config.ORG_PATH] == config.digest((tree / config.ORG_PATH).read_bytes())
    assert wt.cleanup(source, run["session_id"])["cleaned"]


def test_plugin_rebind_accepts_only_same_ready_revision_and_keeps_proposal(legacy_run, tmp_path):
    source, tree, *_ = legacy_run
    file, proposal = prepare(legacy_run)
    observe(proposal)
    replacement = tmp_path / "replacement"
    shutil.copytree(PLUGIN / "extensions/agent-org", replacement)
    unbound = json.loads(file.read_text())
    next(p for p in unbound["handover"]["providers"] if p["label"] == "plugin")["entry"] = str(
        replacement / "extension.mjs"
    )
    # Even byte-identical new entries fail closed if launched before the Host binds them.
    before = file.read_bytes()
    with pytest.raises(AssertionError, match="Unexpected or stale agent-org provider"):
        observe(unbound)
    assert file.read_bytes() == before
    relocation.bind_plugin(tree, replacement / "extension.mjs")
    changed = json.loads(file.read_text())
    assert changed["baseline"] == proposal["baseline"]
    assert changed["proposed_org"] == proposal["proposed_org"]
    observe(changed)
    assert relocation.status(tree, require="worktree")["status"] == "ok"
    write(replacement, "runtime.mjs", "wrong revision")
    before = file.read_bytes()
    with pytest.raises(ValueError, match="not been updated"):
        relocation.bind_plugin(source, replacement / "extension.mjs")
    assert file.read_bytes() == before


@pytest.mark.parametrize("state", ["legacy", "missing", "dual"])
def test_command_hook_denies_unprepared_legacy_missing_or_dual_configuration(legacy_run, state):
    _, tree, *_ = legacy_run
    if state == "missing":
        (tree / "org.json").unlink()
    elif state == "dual":
        write(tree, config.ORG_PATH, "{malformed")
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(tree / ".github/agent-org/tools/hook.ps1"), "-Event", "preToolUse"],
        input=json.dumps({"cwd": str(tree), "toolName": "view", "sessionId": "probe"}),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["permissionDecision"] == "deny"
    assert "configuration audit failed" in response["permissionDecisionReason"]


def test_extension_discovery_never_walks_out_of_a_linked_worktree(legacy_run):
    source, tree, _, data, _, _ = legacy_run
    (tree / "org.json").unlink()
    nested = tree / ".github/agents"
    script = """
      const { pathToFileURL } = await import('node:url');
      const { findOracle, createOracle } = await import(pathToFileURL(process.argv[1]));
      console.log(JSON.stringify({
        found: findOracle(process.argv[3]),
        result: await createOracle(process.argv[2])('preToolUse', {cwd: process.argv[3], toolName: 'view'})
      }));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script,
         str(tree / ".github/extensions/agent-org/oracle.mjs"),
         str(tree / ".github/extensions/agent-org/extension.mjs"), str(nested)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert Path(response["found"]["directory"]) == tree
    assert response["result"]["permissionDecision"] == "deny"
    assert "Missing canonical organization" in response["result"]["permissionDecisionReason"]
    assert (source / "org.json").read_bytes() == data


def test_wrong_worktree_context_is_denied_without_using_its_org(legacy_run, tmp_path):
    _, tree, *_ = legacy_run
    other = tmp_path / "unrelated"
    other.mkdir()
    git(other, "init", "-q")
    write(other, config.ORG_PATH, json.dumps(bootstrap.default_org()))
    response = ov.process_hook({"cwd": str(tree), "toolName": "view", "agentOrgContext": {
        "node": "kernel", "worktree": str(other),
    }})
    assert response["permissionDecision"] == "deny"
    assert "disagree about the worktree" in response["permissionDecisionReason"]


def test_relocation_cli_candidate_baseline_is_repo_relative_not_process_cwd(tmp_path):
    old = baseline_org()
    write(tmp_path, "org.json", json.dumps(old))
    candidate = write(tmp_path, "candidate.json", json.dumps(config.relocated_org(old)))
    command = [sys.executable, "-B", str(PLUGIN / "tools/owner_validator.py"), "--org", str(candidate),
               "--relocation-baseline", "org.json", "--root", str(tmp_path),
               "--paths", config.ORG_PATH, "org.json", "plugin/tools/hook.ps1"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "ok"
    result = subprocess.run(
        command + ["--split-baseline", "org.json"], capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode != 0
    assert "distinct transitions" in result.stderr


def test_repository_installed_mirrors_contain_no_live_or_seed_duplicates():
    repo = PLUGIN.parent
    # Readiness-only explicit baseline override; completed installations select the canonical file.
    explicit = config.LEGACY_PATH if (repo / config.LEGACY_PATH).exists() else config.ORG_PATH
    org = json.loads(config.config_path(repo, explicit=explicit).read_text(encoding="utf-8-sig"))
    files = bootstrap.runtime_files(org, source=PLUGIN)
    assert all(name not in files for name in (config.ORG_PATH, config.LEGACY_PATH, config.SEED_PATH))
    assert not (PLUGIN / "seed/org.json").exists()
    assert not (repo / config.SEED_PATH).exists()
    for name, content in files.items():
        # Git may convert generated template/node line endings on checkout. Shared loops have
        # their separate byte-identical check in test_role_instructions.py.
        assert bootstrap._same_content((repo / name).read_bytes(), content), name
