"""Shared-session lifecycle scenarios using only local Git repositories and deterministic edits."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "tools"))
import bootstrap  # noqa: E402
import owner_validator as ov  # noqa: E402
import worktree as wt  # noqa: E402

TOOL = Path(wt.__file__).resolve()


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, encoding="utf-8", text=True
    ).stdout


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def organization():
    return {
        "nodes": [
            {
                "charter": {"domain": [".github/**", ".gitignore", "org.json", "shared/**"]},
                "children": ["audit", "inventory"],
                "id": "coordinator",
                "mode": "Parent",
                "parent": None,
            },
            {
                "charter": {"domain": ["audit/**"]},
                "children": [],
                "id": "audit",
                "mode": "Leaf",
                "parent": "coordinator",
            },
            {
                "charter": {"domain": ["inventory/**"]},
                "children": [],
                "id": "inventory",
                "mode": "Leaf",
                "parent": "coordinator",
            },
        ],
        "root": "coordinator",
        "version": 1,
    }


@pytest.fixture(autouse=True)
def isolated_git_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for args in (
        ("init", "-q", "-b", "release"),
        ("config", "commit.gpgsign", "false"),
        ("config", "core.autocrlf", "false"),
        ("config", "core.hooksPath", str(root / ".git" / "hooks")),
        ("config", "user.email", "inventory-test@example.invalid"),
        ("config", "user.name", "Inventory Test"),
    ):
        git(root, *args)
    write(root, "org.json", json.dumps(organization()))
    for name in ("audit/ledger.txt", "inventory/stock.txt", "shared/notes.txt"):
        write(root, name, "baseline\n")
    git(root, "add", "--all")
    git(root, "commit", "-q", "-m", "test: seed inventory repository")
    assert ov.validate(organization(), ov.git_tracked(root))["status"] == "ok"
    return root


def save_org(root, org):
    write(root, "org.json", json.dumps(org))


def commit(root, message="test: prepare inventory change"):
    git(root, "add", "--all")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD").strip()


def inventory_split(org):
    org = copy.deepcopy(org)
    org["version"] = 2
    inventory = next(node for node in org["nodes"] if node["id"] == "inventory")
    inventory.update(
        charter={"domain": ["inventory/stock.txt"]},
        children=["receipts", "warehouse"],
        mode="Parent",
    )
    for node_id in ("receipts", "warehouse"):
        org["nodes"].append({
            "charter": {"domain": [f"inventory/{node_id}/**"]},
            "children": [],
            "id": node_id,
            "mode": "Leaf",
            "parent": "inventory",
        })
    return org


def assert_preserved(repo, run):
    assert Path(run["path"]).is_dir()
    assert git(repo, "show-ref", "--verify", f"refs/heads/{run['branch']}")
    assert (ov.git_common_dir(repo) / "agent-org" / "runs" / f"{run['session_id']}.json").is_file()


def test_shared_reuse_from_source_child_and_grandchild(repo):
    run = wt.create(repo, "run-shared")
    tree = Path(run["path"])
    child = tree / "inventory"
    grandchild = child / "nested"
    grandchild.mkdir()
    assert wt.create(repo, "run-shared") == run
    assert wt.create(child, "run-shared") == run
    assert wt.create(grandchild) == run
    assert wt.create(child, "a-child-session") == run
    assert not (tree / ".worktrees").exists()
    assert run["branch"] == "agent-org/run-shared"
    assert run["path"] == str(repo / ".worktrees" / "run-shared")
    assert run["repo"] == str(repo)
    assert run["base_branch"] == "release"
    assert run["base_sha"] == git(repo, "rev-parse", "HEAD").strip()
    assert len(wt._worktrees(repo)) == 2


def test_different_root_sessions_are_isolated_and_merge_serially(repo):
    first, second = wt.create(repo), wt.create(repo)
    assert first["session_id"] != second["session_id"]
    assert first["path"] != second["path"]
    write(Path(first["path"]), "inventory/first.txt", "first\n")
    write(Path(second["path"]), "audit/second.txt", "second\n")
    assert not (Path(second["path"]) / "inventory" / "first.txt").exists()
    assert wt.integrate(repo, first["session_id"])["integrated"]
    assert wt.integrate(repo, second["session_id"])["integrated"]
    assert (repo / "inventory" / "first.txt").read_text() == "first\n"
    assert (repo / "audit" / "second.txt").read_text() == "second\n"
    wt.cleanup(repo, first["session_id"])
    wt.cleanup(repo, second["session_id"])
    assert (repo / ".git").is_dir()


def test_combined_root_and_two_children_integrate_on_non_main_branch(repo):
    run = wt.create(repo, "combined")
    tree = Path(run["path"])
    write(tree, "shared/notes.txt", "root contribution\n")
    write(tree, "audit/ledger.txt", "audit contribution\n")
    write(tree, "inventory/stock.txt", "inventory contribution\n")
    result = wt.integrate(tree / "inventory", run["session_id"])
    assert result["integrated"], result
    assert set(result["changed"]) == {"audit/ledger.txt", "inventory/stock.txt", "shared/notes.txt"}
    assert git(repo, "branch", "--show-current").strip() == "release"
    assert git(repo, "log", "-1", "--format=%an <%ae>").strip() == "Inventory Test <inventory-test@example.invalid>"
    assert git(repo, "log", "-1", "--format=%s").strip() == "feat: integrate agent-org session"
    assert len(git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3
    wt.cleanup(repo, run["session_id"])
    assert not tree.exists()


def test_integration_lock_contends_with_a_separate_process(repo):
    run = wt.create(repo, "locked-run")
    tree = Path(run["path"])
    write(tree, "inventory/pending.txt", "pending\n")
    before = git(tree, "rev-parse", "HEAD")
    with wt._lock(ov.git_common_dir(repo)):
        with pytest.raises(subprocess.CalledProcessError) as failure:
            subprocess.run(
                [sys.executable, str(TOOL), "integrate", "--repo", str(repo), "--session", run["session_id"]],
                capture_output=True, check=True, encoding="utf-8", text=True,
            )
        result = json.loads(failure.value.stdout)
        assert result["reason"] == "busy"
        assert "lock" in failure.value.stderr
        assert git(tree, "rev-parse", "HEAD") == before
        assert (tree / "inventory" / "pending.txt").exists()
    assert wt.integrate(repo, run["session_id"])["integrated"]


def test_descendant_split_and_shared_scope_changes_are_allowed(repo):
    run = wt.create(repo, "split-descendants")
    tree = Path(run["path"])
    save_org(tree, inventory_split(organization()))
    write(tree, "inventory/receipts/new.txt", "receipt\n")
    write(tree, "inventory/warehouse/new.txt", "warehouse\n")
    write(tree, "audit/new.txt", "audit\n")
    write(tree, "shared/new.txt", "coordinator\n")
    result = wt.integrate(repo, run["session_id"])
    assert result["integrated"], result
    assert ov.validate(json.loads((repo / "org.json").read_text()), ov.git_tracked(repo))["status"] == "ok"


@pytest.mark.parametrize("kind", ["committed", "staged", "unstaged", "untracked"])
def test_unowned_managed_files_are_rejected_without_losing_data(repo, kind):
    if kind == "unstaged":
        write(repo, "unowned.txt", "old\n")
        commit(repo)
    run = wt.create(repo, f"unowned-{kind}")
    tree = Path(run["path"])
    write(tree, "unowned.txt", "preserve this\n")
    if kind in ("committed", "staged"):
        git(tree, "add", "unowned.txt")
    if kind == "committed":
        git(tree, "commit", "-q", "-m", "test: add an unowned file")
    before = git(tree, "status", "--porcelain=v1", "-z")
    head = git(repo, "rev-parse", "HEAD")
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"] and result["reason"] == "coverage", result
    assert any(item.get("path") == "unowned.txt" for item in result["violations"])
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(tree, "status", "--porcelain=v1", "-z") == before
    assert (tree / "unowned.txt").read_text() == "preserve this\n"
    assert_preserved(repo, run)


def test_partial_scope_allows_unmanaged_files(repo):
    org = organization()
    org["scope"] = ["inventory/**"]
    save_org(repo, org)
    commit(repo)
    run = wt.create(repo, "partial")
    write(Path(run["path"]), "human-notes.txt", "outside managed scope\n")
    result = wt.integrate(repo, run["session_id"])
    assert result["integrated"], result
    assert (repo / "human-notes.txt").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [("collaboration", "hybrid"), ("root", "inventory"), ("scope", ["inventory/**"]), ("storage", "tracked")],
)
def test_split_cannot_change_runtime_policy(repo, field, value):
    run = wt.create(repo, f"policy-{field}")
    tree = Path(run["path"])
    org = inventory_split(organization())
    org[field] = value
    save_org(tree, org)
    before = git(repo, "rev-parse", "HEAD")
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"], result
    assert result["reason"] in ("coverage", "split")
    assert git(repo, "rev-parse", "HEAD") == before
    assert json.loads((tree / "org.json").read_text())[field] == value
    assert_preserved(repo, run)


@pytest.mark.parametrize("kind", ["staged", "unstaged", "untracked"])
def test_dirty_source_is_refused_before_committing_session(repo, kind):
    run = wt.create(repo, f"dirty-source-{kind}")
    tree = Path(run["path"])
    write(tree, "inventory/session.txt", "session work\n")
    name = "shared/untracked.txt" if kind == "untracked" else "shared/notes.txt"
    write(repo, name, "user work\n")
    if kind == "staged":
        git(repo, "add", name)
    source_status = git(repo, "status", "--porcelain=v1", "-z")
    session_status = git(tree, "status", "--porcelain=v1", "-z")
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"] and result["reason"] == "dirty-source", result
    assert git(repo, "status", "--porcelain=v1", "-z") == source_status
    assert git(tree, "status", "--porcelain=v1", "-z") == session_status
    assert (repo / name).read_text() == "user work\n"
    assert_preserved(repo, run)


def test_create_refuses_dirty_source_instead_of_using_a_stale_baseline(repo):
    write(repo, "shared/notes.txt", "uncommitted user edit\n")
    write(repo, "personal.txt", "untracked user file\n")
    before = git(repo, "status", "--porcelain=v1", "-z")
    with pytest.raises(wt.WorktreeError, match="source changes"):
        wt.create(repo, "dirty-create")
    assert (repo / "shared" / "notes.txt").read_text() == "uncommitted user edit\n"
    assert not (repo / ".worktrees" / "dirty-create").exists()
    assert git(repo, "status", "--porcelain=v1", "-z") == before


def test_create_preserves_unregistered_worktree_paths(repo):
    path = write(repo, ".worktrees/preexisting/human.txt", "existing user file\n")
    with pytest.raises(wt.WorktreeError):
        wt.create(repo, "preexisting")
    assert path.read_text() == "existing user file\n"
    assert len(wt._worktrees(repo)) == 1


def test_create_preserves_unregistered_session_branches(repo):
    git(repo, "branch", "agent-org/preexisting")
    before = git(repo, "rev-parse", "agent-org/preexisting")
    with pytest.raises(wt.WorktreeError):
        wt.create(repo, "preexisting")
    assert git(repo, "rev-parse", "agent-org/preexisting") == before
    assert not (repo / ".worktrees" / "preexisting").exists()


def test_preexisting_agent_commits_are_merged_not_amended_or_dropped(repo):
    run = wt.create(repo, "already-committed")
    tree = Path(run["path"])
    write(tree, "inventory/agent.txt", "committed by agent\n")
    agent_commit = commit(tree, "feat: record inventory movement")
    assert not git(tree, "status", "--porcelain")
    result = wt.integrate(repo, run["session_id"])
    assert result["integrated"], result
    assert "inventory/agent.txt" in result["changed"]
    git(repo, "merge-base", "--is-ancestor", agent_commit, "HEAD")
    assert git(tree, "rev-parse", "HEAD").strip() == agent_commit
    assert git(repo, "show", "-s", "--format=%s", agent_commit).strip() == "feat: record inventory movement"


def test_integration_reports_files_added_by_the_session_commit_hook(repo, monkeypatch):
    run = wt.create(repo, "hook-inventory")
    tree = Path(run["path"])
    write(tree, "inventory/requested.txt", "requested\n")
    original = wt._git

    def committing(root, *args, **kwargs):
        if Path(root) == tree and args and args[0] == "commit":
            write(tree, "inventory/generated.txt", "added during commit\n")
            original(tree, "add", "inventory/generated.txt")
        return original(root, *args, **kwargs)

    monkeypatch.setattr(wt, "_git", committing)
    result = wt.integrate(repo, run["session_id"])
    assert result["integrated"], result
    assert {"inventory/requested.txt", "inventory/generated.txt"} <= set(result["changed"])


def test_conflict_preserves_source_other_sessions_and_agent_commit(repo):
    first, second = wt.create(repo, "first-conflict"), wt.create(repo, "second-conflict")
    write(Path(first["path"]), "inventory/stock.txt", "first session\n")
    write(Path(second["path"]), "inventory/stock.txt", "second session\n")
    assert wt.integrate(repo, first["session_id"])["integrated"]
    before = git(repo, "rev-parse", "HEAD")
    result = wt.integrate(repo, second["session_id"])
    assert not result["integrated"] and result["reason"] == "conflict", result
    assert git(repo, "rev-parse", "HEAD") == before
    assert not git(repo, "status", "--porcelain")
    assert (repo / "inventory" / "stock.txt").read_text() == "first session\n"
    assert (Path(second["path"]) / "inventory" / "stock.txt").read_text() == "second session\n"
    assert_preserved(repo, first)
    assert_preserved(repo, second)


def test_actual_merged_inventory_is_validated_and_only_owned_merge_is_aborted(repo):
    run = wt.create(repo, "merged-coverage")
    tree = Path(run["path"])
    write(tree, "inventory/session.txt", "session\n")
    write(repo, "unowned-source.txt", "keep the source commit\n")
    source_commit = commit(repo)
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"] and result["reason"] == "coverage", result
    assert git(repo, "rev-parse", "HEAD").strip() == source_commit
    assert (repo / "unowned-source.txt").read_text() == "keep the source commit\n"
    assert not (repo / "inventory" / "session.txt").exists()
    assert not git(repo, "status", "--porcelain")
    assert_preserved(repo, run)


def test_individually_valid_branches_cannot_merge_into_a_coverage_gap(repo):
    run = wt.create(repo, "split-merge-gap")
    tree = Path(run["path"])
    save_org(tree, inventory_split(organization()))
    write(tree, "inventory/receipts/new.txt", "receipt\n")
    write(repo, "inventory/source-addition.txt", "concurrent valid inventory file\n")
    source_commit = commit(repo)
    assert ov.validate(organization(), ov.git_tracked(repo))["status"] == "ok"
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"] and result["reason"] == "coverage", result
    assert any(item.get("path") == "inventory/source-addition.txt" for item in result["violations"])
    assert git(repo, "rev-parse", "HEAD").strip() == source_commit
    assert (repo / "inventory" / "source-addition.txt").read_text() == "concurrent valid inventory file\n"
    assert json.loads((repo / "org.json").read_text()) == organization()
    assert json.loads((tree / "org.json").read_text()) == inventory_split(organization())
    assert not git(repo, "status", "--porcelain")
    assert_preserved(repo, run)


def test_post_commit_validation_keeps_rejected_merge_data_in_session(repo, monkeypatch):
    run = wt.create(repo, "post-commit-coverage")
    tree = Path(run["path"])
    write(tree, "inventory/session.txt", "session contribution\n")
    source_commit = git(repo, "rev-parse", "HEAD")
    original_git = wt._git

    def commit_side_effect(root, *args, **kwargs):
        if Path(root) == repo and args[0] == "commit":
            write(repo, "unowned-hook-output.txt", "preserve hook-generated data\n")
            git(repo, "add", "unowned-hook-output.txt")
        return original_git(root, *args, **kwargs)

    monkeypatch.setattr(wt, "_git", commit_side_effect)
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"] and result["reason"] == "coverage", result
    assert git(repo, "rev-parse", "HEAD") == source_commit
    assert not (repo / "unowned-hook-output.txt").exists()
    assert (tree / "unowned-hook-output.txt").read_text() == "preserve hook-generated data\n"
    assert (tree / "inventory" / "session.txt").read_text() == "session contribution\n"
    assert not git(repo, "status", "--porcelain")
    assert_preserved(repo, run)


def test_commit_failure_never_bypasses_hooks_or_loses_session_data(repo, monkeypatch):
    run = wt.create(repo, "commit-rejected")
    tree = Path(run["path"])
    write(tree, "inventory/pending.txt", "keep pending data\n")
    before = git(repo, "rev-parse", "HEAD")
    original_git = wt._git

    def rejected_commit(root, *args, **kwargs):
        assert "--amend" not in args and "--no-verify" not in args
        assert not any("user.email=" in arg or "user.name=" in arg for arg in args)
        if args[0] == "commit":
            raise subprocess.CalledProcessError(1, ["git", *args], stderr="repository hook rejected the commit")
        return original_git(root, *args, **kwargs)

    monkeypatch.setattr(wt, "_git", rejected_commit)
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"] and "hook rejected" in result["error"], result
    assert git(repo, "rev-parse", "HEAD") == before
    assert (tree / "inventory" / "pending.txt").read_text() == "keep pending data\n"
    assert_preserved(repo, run)


def test_both_rename_endpoints_are_validated(repo):
    run = wt.create(repo, "renamed-unowned")
    tree = Path(run["path"])
    git(tree, "mv", "inventory/stock.txt", "unowned-stock.txt")
    assert {"inventory/stock.txt", "unowned-stock.txt"} <= set(wt._paths(tree, run["base_sha"]))
    before = git(tree, "status", "--porcelain=v1", "-z")
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"] and result["reason"] == "coverage", result
    assert git(tree, "status", "--porcelain=v1", "-z") == before
    assert (repo / "inventory" / "stock.txt").exists()
    assert (tree / "unowned-stock.txt").read_text() == "baseline\n"


def test_existing_source_merge_is_not_aborted(repo):
    run = wt.create(repo, "existing-merge")
    write(Path(run["path"]), "inventory/session.txt", "session\n")
    git_dir = ov.git_common_dir(repo)
    marker = git_dir / "MERGE_HEAD"
    marker.write_text(git(repo, "rev-parse", "HEAD"), encoding="utf-8")
    before = marker.read_bytes()
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"] and result["reason"] == "dirty-source", result
    assert marker.read_bytes() == before
    assert_preserved(repo, run)


def test_source_branch_switch_is_refused_without_checkout(repo):
    run = wt.create(repo, "branch-guard")
    write(Path(run["path"]), "inventory/new.txt", "new\n")
    git(repo, "switch", "-q", "-c", "human-work")
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"] and result["reason"] == "branch", result
    assert git(repo, "branch", "--show-current").strip() == "human-work"
    assert_preserved(repo, run)


@pytest.mark.parametrize("kind", ["committed", "dirty", "pristine"])
def test_default_cleanup_refuses_unintegrated_session(repo, kind):
    run = wt.create(repo, f"cleanup-{kind}")
    tree = Path(run["path"])
    if kind != "pristine":
        write(tree, "inventory/keep.txt", "do not lose\n")
    if kind == "committed":
        commit(tree)
    with pytest.raises(wt.WorktreeError):
        wt.cleanup(repo, run["session_id"])
    assert_preserved(repo, run)
    if kind != "pristine":
        assert (tree / "inventory" / "keep.txt").read_text() == "do not lose\n"


def test_explicit_discard_removes_only_registered_session(repo, tmp_path):
    run, other = wt.create(repo, "discard-me"), wt.create(repo, "keep-me")
    unrelated = tmp_path / "unrelated"
    git(repo, "worktree", "add", "-q", "-b", "human-linked", str(unrelated))
    write(Path(run["path"]), "inventory/discard.txt", "explicitly discarded\n")
    write(Path(other["path"]), "audit/keep.txt", "other session\n")
    write(unrelated, "human.txt", "unrelated tree\n")
    result = wt.cleanup(repo, run["session_id"], discard=True)
    assert result["cleaned"] and result["discarded"]
    assert not Path(run["path"]).exists()
    assert_preserved(repo, other)
    assert (unrelated / "human.txt").read_text() == "unrelated tree\n"
    assert (repo / ".git").is_dir()
    assert run["branch"] not in git(repo, "branch", "--format=%(refname:short)").splitlines()


def test_explicit_discard_still_preserves_nested_registered_worktrees(repo):
    run = wt.create(repo, "nested-guard")
    tree = Path(run["path"])
    nested = tree / "human-linked"
    git(repo, "worktree", "add", "-q", "-b", "human-nested", str(nested))
    write(nested, "human.txt", "nested user work\n")
    with pytest.raises(wt.WorktreeError):
        wt.cleanup(repo, run["session_id"], discard=True)
    assert (nested / "human.txt").read_text() == "nested user work\n"
    assert_preserved(repo, run)


def test_no_op_is_explicit_and_can_be_cleaned_up(repo):
    run = wt.create(repo, "no-op")
    before = git(repo, "rev-parse", "HEAD")
    result = wt.integrate(repo, run["session_id"])
    assert result == {"changed": [], "integrated": False, "reason": "no-op"}
    assert git(repo, "rev-parse", "HEAD") == before
    wt.cleanup(repo, run["session_id"])
    assert not Path(run["path"]).exists()


def test_cleanup_refuses_changes_after_successful_integration(repo):
    run = wt.create(repo, "late-edit")
    tree = Path(run["path"])
    assert wt.integrate(repo, run["session_id"])["reason"] == "no-op"
    write(tree, "audit/late.txt", "late child contribution\n")
    with pytest.raises(wt.WorktreeError):
        wt.cleanup(repo, run["session_id"])
    assert (tree / "audit" / "late.txt").exists()


def test_cleanup_preserves_dirty_source_work(repo):
    run = wt.create(repo, "cleanup-source-dirty")
    assert wt.integrate(repo, run["session_id"])["reason"] == "no-op"
    write(repo, "shared/notes.txt", "unsaved user work\n")
    write(repo, "personal.txt", "untracked user work\n")
    before = git(repo, "status", "--porcelain=v1", "-z")
    wt.cleanup(repo, run["session_id"])
    assert git(repo, "status", "--porcelain=v1", "-z") == before
    assert (repo / "shared" / "notes.txt").read_text() == "unsaved user work\n"
    assert (repo / "personal.txt").read_text() == "untracked user work\n"


@pytest.fixture
def local_repo(repo):
    git(repo, "rm", "--cached", "org.json")
    git(repo, "commit", "-q", "-m", "test: use a local organization")
    bootstrap.bootstrap_repo(repo, root_name="coordinator")
    write(repo, ".github/agent-org/tools/__pycache__/unwanted.pyc", "not copied\n")
    write(ov.git_common_dir(repo), "agent-org/proposals/example.json", "local proposal scratch\n")
    write(repo, ".github/agents/human.md", "not generated\n")
    write(repo, ".github/instructions/agent-org-local.instructions.md", "local instructions\n")
    write(repo, ".github/instructions/human.instructions.md", "not generated\n")
    write(repo, "private.txt", "not copied\n")
    exclude = ov.git_common_dir(repo) / "info" / "exclude"
    exclude.write_text(
        exclude.read_text(encoding="utf-8")
        + "\n/.github/agents/human.md\n/.github/instructions/agent-org-local.instructions.md\n"
        + "/.github/instructions/human.instructions.md\n/private.txt\n",
        encoding="utf-8",
    )
    assert not git(repo, "status", "--porcelain")
    return repo


def test_local_overlay_is_provisioned_without_status_noise_and_runs(local_repo):
    repo = local_repo
    run = wt.create(repo, "local-copy")
    tree = Path(run["path"])
    assert (tree / "org.json").exists()
    assert (tree / ".github" / "agents" / "coordinator.md").exists()
    assert (tree / ".github" / "agents" / "splitter.md").exists()
    assert not (tree / "private.txt").exists()
    assert not (tree / ".github" / "agents" / "human.md").exists()
    assert not (tree / ".github" / "instructions" / "human.instructions.md").exists()
    assert not (tree / ".github" / "agent-org" / "tools" / "__pycache__" / "unwanted.pyc").exists()
    assert not (tree / ".github" / "agent-org" / "cache" / "proposal.json").exists()
    result = subprocess.run(
        [sys.executable, str(tree / ".github" / "agent-org" / "tools" / "worktree.py"),
         "create", "--repo", str(tree / "inventory"), "--session", "local-copy"],
        capture_output=True, check=True, encoding="utf-8", text=True,
    )
    assert json.loads(result.stdout)["path"] == str(tree)
    assert not git(tree, "status", "--porcelain")
    assert not git(repo, "status", "--porcelain")
    assert run["local_files"]["org.json"] == wt._digest((repo / "org.json").read_bytes())
    descriptor = ov.git_common_dir(repo) / "agent-org" / "runs" / "local-copy.json"
    assert json.loads(descriptor.read_text()) == run
    assert set(bootstrap.runtime_files(organization())) <= set(run["runtime_files"])
    patterns = (ov.git_common_dir(repo) / "info" / "exclude").read_text().splitlines()
    assert "/.github/agent-org/" not in patterns
    assert "/.github/extensions/agent-org/" not in patterns


def test_cleanup_cli_can_run_inside_its_own_linked_worktree(local_repo):
    run = wt.create(local_repo, "inside-cleanup")
    tree = Path(run["path"])
    assert wt.integrate(local_repo, run["session_id"])["reason"] == "no-op"
    result = subprocess.run(
        [sys.executable, str(tree / ".github" / "agent-org" / "tools" / "worktree.py"),
         "cleanup", "--repo", str(tree), "--session", run["session_id"]],
        capture_output=True, check=True, cwd=tree, encoding="utf-8", text=True,
    )
    assert json.loads(result.stdout)["cleaned"]
    assert not tree.exists()
    assert (local_repo / ".git").is_dir()


def test_local_split_configuration_and_new_definitions_persist_before_cleanup(local_repo):
    repo = local_repo
    run = wt.create(repo, "local-split")
    tree = Path(run["path"])
    save_org(tree, inventory_split(organization()))
    write(tree, ".github/agents/inventory.md", "inventory parent definition\n")
    write(tree, ".github/agents/receipts.md", "receipts definition\n")
    write(tree, ".github/agents/warehouse.md", "warehouse definition\n")
    write(tree, "inventory/receipts/new.txt", "receipt data\n")
    result = wt.integrate(repo, run["session_id"])
    assert result["integrated"], result
    assert json.loads((repo / "org.json").read_text())["version"] == 2
    assert (repo / ".github" / "agents" / "receipts.md").read_text() == "receipts definition\n"
    assert (repo / ".github" / "agents" / "inventory.md").read_text() == "inventory parent definition\n"
    assert not git(repo, "status", "--porcelain")
    assert not git(tree, "status", "--porcelain")
    assert not set(run["local_files"]) & set(git(repo, "ls-files").splitlines())
    wt.cleanup(repo, run["session_id"])
    assert (repo / ".github" / "agents" / "warehouse.md").exists()
    assert not tree.exists()


def test_overlay_only_edits_and_deletions_are_reconciled(local_repo):
    repo = local_repo
    run = wt.create(repo, "overlay-only")
    tree = Path(run["path"])
    write(tree, ".github/agents/inventory.md", "updated definition\n")
    (tree / ".github" / "instructions" / "agent-org-local.instructions.md").unlink()
    before = git(repo, "rev-parse", "HEAD")
    result = wt.integrate(repo, run["session_id"])
    assert result["integrated"], result
    assert git(repo, "rev-parse", "HEAD") == before
    assert (repo / ".github" / "agents" / "inventory.md").read_text() == "updated definition\n"
    assert not (repo / ".github" / "instructions" / "agent-org-local.instructions.md").exists()
    wt.cleanup(repo, run["session_id"])


def test_cleanup_does_not_overwrite_a_later_sessions_reconciled_overlay(local_repo):
    repo = local_repo
    first, second = wt.create(repo, "earlier-run"), wt.create(repo, "later-run")
    write(Path(first["path"]), "inventory/first.txt", "first contribution\n")
    assert wt.integrate(repo, first["session_id"])["integrated"]
    write(Path(second["path"]), ".github/agents/audit.md", "later definition\n")
    assert wt.integrate(repo, second["session_id"])["integrated"]
    wt.cleanup(repo, first["session_id"])
    assert (repo / ".github" / "agents" / "audit.md").read_text() == "later definition\n"
    wt.cleanup(repo, second["session_id"])


def test_known_unignored_overlay_is_excluded_without_hiding_other_agent_definitions(repo):
    git(repo, "rm", "--cached", "org.json")
    git(repo, "commit", "-q", "-m", "test: prepare unignored local overlay")
    write(repo, ".github/agents/coordinator.md", "generated definition\n")
    write(repo, ".github/agents/human.md", "unrelated definition\n")
    write(repo, ".github/agent-org/tools/requirements.txt", "pathspec\n")
    with pytest.raises(wt.WorktreeError, match="Commit or set aside source changes"):
        wt.create(repo, "exact-overlay")
    assert "?? .github/agents/human.md" in git(repo, "status", "--porcelain", "--untracked-files=all")
    with pytest.raises(subprocess.CalledProcessError):
        git(repo, "check-ignore", ".github/agents/human.md")
    git(repo, "add", ".github/agents/human.md")
    git(repo, "commit", "-q", "-m", "test: keep unrelated agent tracked")
    run = wt.create(repo, "exact-overlay")
    tree = Path(run["path"])
    assert not git(tree, "status", "--porcelain")
    assert not git(repo, "status", "--porcelain")
    assert (tree / ".github" / "agents" / "human.md").read_text() == "unrelated definition\n"
    git(repo, "check-ignore", "org.json", ".github/agents/coordinator.md", ".github/agent-org/tools/requirements.txt")


@pytest.mark.parametrize("namespace", [".github/agent-org", ".github/extensions/agent-org"])
def test_create_does_not_hide_or_copy_unrelated_runtime_namespace_files(local_repo, namespace):
    repo = local_repo
    name = f"{namespace}/user-note.md"
    write(repo, name, "existing user note\n")
    before = git(repo, "status", "--porcelain=v1", "-z")
    with pytest.raises(wt.WorktreeError, match="Commit or set aside source changes"):
        wt.create(repo, "namespace-collision")
    assert git(repo, "status", "--porcelain=v1", "-z") == before
    assert (repo / name).read_text() == "existing user note\n"
    with pytest.raises(subprocess.CalledProcessError):
        git(repo, "check-ignore", name)
    exclude = ov.git_common_dir(repo) / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as stream:
        stream.write(f"\n/{name}\n")
    run = wt.create(repo, "namespace-collision")
    assert not (Path(run["path"]) / name).exists()
    assert name not in run["local_files"]
    assert name not in run["runtime_files"]
    assert wt.integrate(repo, run["session_id"])["reason"] == "no-op"
    wt.cleanup(repo, run["session_id"])
    assert (repo / name).read_text() == "existing user note\n"


def test_unrelated_runtime_namespace_file_is_regular_git_data(local_repo):
    repo = local_repo
    run = wt.create(repo, "namespace-data")
    tree = Path(run["path"])
    name = ".github/agent-org/user-note.md"
    write(tree, name, "ordinary repository data\n")
    assert name in git(tree, "status", "--porcelain", "--untracked-files=all")
    assert wt.integrate(repo, run["session_id"])["integrated"]
    assert name in git(repo, "ls-files").splitlines()
    assert (repo / name).read_text() == "ordinary repository data\n"
    wt.cleanup(repo, run["session_id"])


def test_runtime_catalog_preserves_known_deletions_and_git_local_proposal_scratch(local_repo):
    repo = local_repo
    run = wt.create(repo, "runtime-catalog")
    tree = Path(run["path"])
    readme = Path(".github") / "agent-org" / "tools" / "README.md"
    (tree / readme).unlink()
    result = subprocess.run(
        [sys.executable, str(tree / ".github" / "agent-org" / "tools" / "worktree.py"),
         "integrate", "--repo", str(tree), "--session", run["session_id"]],
        capture_output=True, check=True, encoding="utf-8", text=True,
    )
    assert json.loads(result.stdout)["integrated"]
    assert not (repo / readme).exists()
    proposal = ov.git_common_dir(repo) / "agent-org" / "proposals" / "example.json"
    assert proposal.read_text() == "local proposal scratch\n"
    wt.cleanup(repo, run["session_id"])


def test_tracked_overlay_uses_git_instead_of_local_reconciliation(repo):
    org = organization()
    org["storage"] = "tracked"
    save_org(repo, org)
    write(repo, ".github/agents/coordinator.md", "tracked definition\n")
    write(repo, ".github/agent-org/tools/helper.py", "print('tracked runtime')\n")
    commit(repo)
    run = wt.create(repo, "tracked-overlay")
    tree = Path(run["path"])
    assert run["local_files"] == {}
    assert (tree / ".github" / "agents" / "coordinator.md").read_text() == "tracked definition\n"
    write(tree, ".github/agents/coordinator.md", "updated tracked definition\n")
    write(tree, ".github/agent-org/tools/new.py", "print('new tracked runtime')\n")
    assert wt.integrate(repo, run["session_id"])["integrated"]
    assert ".github/agent-org/tools/new.py" in git(repo, "ls-files").splitlines()
    assert (repo / ".github" / "agents" / "coordinator.md").read_text() == "updated tracked definition\n"
    wt.cleanup(repo, run["session_id"])


def test_concurrent_source_overlay_changes_are_not_overwritten(local_repo):
    repo = local_repo
    run = wt.create(repo, "overlay-conflict")
    tree = Path(run["path"])
    write(tree, ".github/agents/inventory.md", "session definition\n")
    write(tree, "inventory/session.txt", "session data\n")
    write(repo, ".github/agents/inventory.md", "concurrent source definition\n")
    before = git(repo, "rev-parse", "HEAD")
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"] and result["reason"] == "overlay-conflict", result
    assert git(repo, "rev-parse", "HEAD") == before
    assert (repo / ".github" / "agents" / "inventory.md").read_text() == "concurrent source definition\n"
    assert (tree / ".github" / "agents" / "inventory.md").read_text() == "session definition\n"
    with pytest.raises(wt.WorktreeError):
        wt.cleanup(repo, run["session_id"])
    assert_preserved(repo, run)


def test_cleanup_refuses_late_local_overlay_edits(local_repo):
    run = wt.create(local_repo, "overlay-late")
    tree = Path(run["path"])
    assert wt.integrate(local_repo, run["session_id"])["reason"] == "no-op"
    write(tree, ".github/agents/audit.md", "late local edit\n")
    with pytest.raises(wt.WorktreeError):
        wt.cleanup(local_repo, run["session_id"])
    assert (tree / ".github" / "agents" / "audit.md").read_text() == "late local edit\n"


def test_cleanup_preserves_unrelated_ignored_session_files(repo):
    write(repo, ".gitignore", "personal.cache\n")
    commit(repo)
    run = wt.create(repo, "ignored-data")
    tree = Path(run["path"])
    assert wt.integrate(repo, run["session_id"])["reason"] == "no-op"
    write(tree, "personal.cache", "user data\n")
    with pytest.raises(wt.WorktreeError):
        wt.cleanup(repo, run["session_id"])
    assert (tree / "personal.cache").read_text() == "user data\n"
    wt.cleanup(repo, run["session_id"], discard=True)


def test_hybrid_checkpoint_is_written_only_after_success(repo):
    org = organization()
    org["collaboration"] = "hybrid"
    save_org(repo, org)
    commit(repo)
    run = wt.create(repo, "hybrid-run")
    tree = Path(run["path"])
    checkpoint = ov.git_common_dir(repo) / "agent-org" / "checkpoint.json"
    write(tree, "unowned.txt", "invalid\n")
    assert not wt.integrate(repo, run["session_id"])["integrated"]
    assert not checkpoint.exists()
    (tree / "unowned.txt").unlink()
    write(tree, "audit/reconciled.txt", "reconciled\n")
    assert wt.integrate(repo, run["session_id"])["integrated"]
    assert checkpoint.exists()
    assert ov.drift(org, repo)["status"] == "ok"


@pytest.mark.parametrize("session_id", ["", "../escape", r"..\escape", "/absolute", r"C:\escape", "CON", "a.b", "a/b"])
def test_unsafe_session_ids_cannot_escape_repository(repo, session_id):
    before = git(repo, "worktree", "list", "--porcelain")
    with pytest.raises(wt.WorktreeError):
        wt.create(repo, session_id)
    result = wt.integrate(repo, session_id)
    assert not result["integrated"] and result["reason"] == "session", result
    with pytest.raises(wt.WorktreeError):
        wt.cleanup(repo, session_id, discard=True)
    assert git(repo, "worktree", "list", "--porcelain") == before


def test_descriptor_path_tampering_cannot_remove_source(repo):
    run = wt.create(repo, "tampered")
    record = ov.git_common_dir(repo) / "agent-org" / "runs" / "tampered.json"
    value = json.loads(record.read_text())
    value["path"] = str(repo)
    record.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(wt.WorktreeError):
        wt.cleanup(repo, run["session_id"], discard=True)
    assert (repo / ".git").is_dir()
    assert Path(run["path"]).is_dir()


def test_descriptor_cannot_reconcile_unrelated_source_files(local_repo):
    repo = local_repo
    run = wt.create(repo, "unrelated-overlay")
    tree = Path(run["path"])
    write(tree, "private.txt", "unrelated session content\n")
    record = ov.git_common_dir(repo) / "agent-org" / "runs" / "unrelated-overlay.json"
    value = json.loads(record.read_text())
    value["local_files"]["private.txt"] = wt._digest((repo / "private.txt").read_bytes())
    record.write_text(json.dumps(value), encoding="utf-8")
    result = wt.integrate(repo, run["session_id"])
    assert not result["integrated"] and result["reason"] == "overlay", result
    assert (repo / "private.txt").read_text() == "not copied\n"
    assert (tree / "private.txt").read_text() == "unrelated session content\n"


def test_cli_reports_json_success_no_op_rejection_and_explicit_discard(repo):
    def command(*args):
        return subprocess.run(
            [sys.executable, str(TOOL), *args, "--repo", str(repo)],
            capture_output=True, check=True, encoding="utf-8", text=True,
        )

    run = json.loads(command("create", "--session", "cli-run").stdout)
    assert json.loads(command("integrate", "--session", "cli-run").stdout)["reason"] == "no-op"
    write(Path(run["path"]), "unowned.txt", "do not discard implicitly\n")
    with pytest.raises(subprocess.CalledProcessError) as failure:
        command("integrate", "--session", "cli-run")
    assert json.loads(failure.value.stdout)["reason"] == "coverage"
    assert failure.value.stderr.strip()
    with pytest.raises(subprocess.CalledProcessError):
        command("cleanup", "--session", "cli-run")
    assert json.loads(command("cleanup", "--session", "cli-run", "--discard").stdout)["cleaned"]


def test_non_git_repository_is_rejected(tmp_path):
    with pytest.raises(wt.WorktreeError):
        wt.create(tmp_path, "not-git")
