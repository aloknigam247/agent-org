"""Behavioral coverage for the non-destructive, configurable runtime overlay."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


PLUGIN = Path(__file__).resolve().parents[1] / "plugin"
SPEC = importlib.util.spec_from_file_location("agent_org_bootstrap", PLUGIN / "tools" / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)
ORG_PATH = bootstrap.ORG_PATH


def git(repo, *args, allowed=(0,)):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert result.returncode in allowed, result.stderr
    return result


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


def seed():
    return bootstrap.default_org()


def frontmatter(content):
    return yaml.safe_load(content.decode("utf-8-sig").split("---", 2)[1])


def loop_reference(content):
    assert "loop" not in frontmatter(content)
    body = content.decode("utf-8-sig").split("---", 2)[2]
    references = re.findall(r"^loop: (.+)$", body, re.MULTILINE)
    assert len(references) == 1
    return references[0].strip().replace("\\", "/")


def live_tree():
    return {
        "collaboration": "hybrid",
        "nodes": [
            {
                "charter": {"concerns": ["shared interface"], "domain": ["src/shared/**"], "excludes": []},
                "children": ["alpha", "beta"],
                "id": "coordinator",
                "mode": "Parent",
                "parent": None,
            },
            {
                "charter": {"concerns": ["first component"], "domain": ["src/alpha/**"], "excludes": []},
                "children": [],
                "id": "alpha",
                "mode": "Leaf",
                "parent": "coordinator",
            },
            {
                "charter": {"concerns": ["second component"], "domain": ["src/beta/**"], "excludes": []},
                "children": [],
                "id": "beta",
                "mode": "Leaf",
                "parent": "coordinator",
            },
        ],
        "root": "coordinator",
        "scope": ["src/**"],
        "storage": "local",
        "version": 7,
    }


def snapshot(repo):
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file() and path.relative_to(repo).parts[0] != ".git"
    }


def exclude_file(repo):
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return Path(common) / "info" / "exclude"


@pytest.fixture(autouse=True)
def isolated_git_configuration(tmp_path, monkeypatch):
    for name in (
        "GIT_COMMON_DIR", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "absent-global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "target with spaces"
    root.mkdir()
    git(root, "init", "-q", "-b", "test-base")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "core.hooksPath", str(root / ".git" / "no-hooks"))
    write(root / "README.md", "Human repository\n")
    write(root / "src" / "app.py", "VALUE = 1\n")
    git(root, "add", "README.md", "src/app.py")
    git(root, "-c", "user.email=tests@local", "-c", "user.name=Tests", "commit", "-q", "-m", "test: seed fixture")
    return root


@pytest.mark.parametrize(
    "unselected,options",
    [
        ("collaboration-agents", {"collaboration": "hybrid"}),
        ("collaboration-hybrid", {"collaboration": "agents"}),
        ("scope-full", {"scope": ["src/**"]}),
        ("scope-partial", {"scope": ["**"]}),
    ],
)
def test_unselected_installed_profiles_are_rejected_without_removing_user_files(repo, unselected, options):
    path = repo / ".github" / "instructions" / f"agent-org.{unselected}.instructions.md"
    write(path, "---\napplyTo: '**'\n---\npre-existing instructions\n")
    before = snapshot(repo)
    with pytest.raises(bootstrap.BootstrapError, match="unselected agent-org profile"):
        bootstrap.bootstrap_repo(repo, source=PLUGIN, **options)
    assert snapshot(repo) == before


def test_generated_node_can_resolve_loop_without_injected_frontmatter():
    org = seed()
    org["root"] = "navigator"
    org["nodes"][0]["id"] = "navigator"
    definition = bootstrap.runtime_files(org, source=PLUGIN)[".github/agents/navigator.md"]
    assert loop_reference(definition) == ".github/agent-org/loops/leaf.md"
    assert b".github\\agents\\navigator.md" not in definition


@pytest.mark.parametrize("scope", [None, ["src/**"]], ids=["full", "partial"])
@pytest.mark.parametrize("collaboration", ["agents", "hybrid"])
@pytest.mark.parametrize("storage", ["local", "tracked"])
def test_independent_configuration_axes(repo, scope, collaboration, storage):
    result = bootstrap.bootstrap_repo(
        repo, scope=scope, collaboration=collaboration, storage=storage, source=PLUGIN
    )
    org = json.loads((repo / ORG_PATH).read_text(encoding="utf-8"))
    expected_scope = ["**"] if scope is None else scope
    assert org["collaboration"] == collaboration
    assert org["scope"] == expected_scope
    assert org["storage"] == storage
    assert org["nodes"][0]["charter"]["domain"] == expected_scope
    assert org["root"] == "main"
    assert org["version"] == seed()["version"]
    assert set(result["created"]) == set(bootstrap.runtime_files(org, source=PLUGIN)) | {ORG_PATH}
    assert not (repo / "org.json").exists()
    assert not (repo / bootstrap.SEED_PATH).exists()
    assert result["existing"] == []

    profiles = {path.name for path in (repo / ".github" / "instructions").iterdir()}
    area = "full" if scope is None else "partial"
    assert profiles == {
        f"agent-org.collaboration-{collaboration}.instructions.md",
        "agent-org.instructions.md",
        f"agent-org.scope-{area}.instructions.md",
    }
    instructions = repo / ".github" / "instructions"
    host = (instructions / "agent-org.instructions.md").read_text(encoding="utf-8")
    guard = host.split("## Host procedure", 1)[0]
    for phrase in (
        "AgentOrgActingNode", "custom-agent definition", "do not apply", "do not invoke", "named org node", "splitter"
    ):
        assert phrase in guard
    scope_profile = (instructions / f"agent-org.scope-{area}.instructions.md").read_text(encoding="utf-8")
    assert "Host-only routing" in scope_profile
    assert "agent-org.instructions.md" in scope_profile
    if collaboration == "agents":
        collaboration_profile = (instructions / "agent-org.collaboration-agents.instructions.md").read_text(
            encoding="utf-8"
        )
        assert "task result" in collaboration_profile
        assert "configured root" not in collaboration_profile
    ignored = git(repo, "check-ignore", "--quiet", "--", ORG_PATH, allowed=(0, 1)).returncode == 0
    assert ignored == (storage == "local")
    if storage == "local":
        assert git(repo, "status", "--porcelain", "--untracked-files=all").stdout == ""
    else:
        assert "/" + ORG_PATH not in exclude_file(repo).read_text(encoding="utf-8").splitlines()
        assert git(repo, "diff", "--cached", "--name-only").stdout == ""

    for relative in (".github/agent-org/tools/__pycache__/bootstrap.pyc", ".worktrees/session/scratch.txt"):
        assert git(repo, "check-ignore", "--quiet", "--", relative).returncode == 0
    assert "/.github/agent-org/cache/" not in exclude_file(repo).read_text(encoding="utf-8").splitlines()


def test_custom_root_is_rendered_without_default_agent(repo):
    original_defaults = seed()
    result = bootstrap.bootstrap_repo(repo, root_name="product-root", source=PLUGIN)
    org = result["org"]
    assert org["root"] == org["nodes"][0]["id"] == "product-root"
    assert not (repo / ".github" / "agents" / "main.md").exists()
    definition = (repo / ".github" / "agents" / "product-root.md").read_bytes()
    assert frontmatter(definition)["name"] == "product-root"
    assert loop_reference(definition) == ".github/agent-org/loops/leaf.md"
    assert b".github\\agents\\product-root.md" not in definition
    assert b"Read and follow this operating file:" in definition
    assert seed() == original_defaults
    assert not (PLUGIN / "seed" / "org.json").exists()


def test_plugin_agent_assets_have_no_static_root_definition():
    assert {path.name for path in (PLUGIN / "agents").glob("*.md")} == {"splitter.md"}
    fields = frontmatter((PLUGIN / "agents" / "splitter.md").read_bytes())
    assert fields["user-invocable"] is False
    assert fields.get("disable-model-invocation", False) is False
    template = (PLUGIN / "templates" / "_node.template.md").read_bytes().replace(b"\r\n", b"\n")
    assert b"\nuser-invocable: false\n" in template


@pytest.mark.parametrize("path", sorted((PLUGIN / "skills").rglob("SKILL.md")), ids=lambda path: path.parent.name)
def test_only_bootstrap_skill_is_user_invocable(path):
    fields = frontmatter(path.read_bytes())
    assert fields["user-invocable"] is (path.parent.name == "bootstrap")
    assert fields.get("disable-model-invocation", False) is False


def test_plugin_registers_only_bootstrap_skill():
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["skills"] == ["skills/bootstrap"]


@pytest.mark.parametrize("root_name", ["main", "navigator"])
def test_bootstrap_preserves_only_public_entry_points(repo, root_name):
    bootstrap.bootstrap_repo(repo, root_name=root_name, source=PLUGIN)
    for file in (repo / ".github" / "agents").glob("*.md"):
        assert frontmatter(file.read_bytes())["user-invocable"] is (file.stem == root_name)
    copied = repo / ".github" / "skills"
    assert {file.parent.name for file in copied.rglob("SKILL.md")} == {
        "agent-org-design", "agent-org-wiki-curate"
    }
    for file in copied.rglob("SKILL.md"):
        fields = frontmatter(file.read_bytes())
        assert fields["user-invocable"] is False
        assert fields["name"] == file.parent.name
    assert not (copied / "bootstrap").exists()


def test_non_root_promotion_and_new_children_remain_internal(repo):
    org = live_tree()
    write(repo / ORG_PATH, json.dumps(org))
    bootstrap.bootstrap_repo(
        repo, root_name=org["root"], scope=org["scope"], collaboration=org["collaboration"], source=PLUGIN
    )
    alpha = next(node for node in org["nodes"] if node["id"] == "alpha")
    alpha["mode"] = "Parent"
    alpha["children"] = ["alpha-one", "alpha-two"]
    alpha["charter"]["domain"] = ["src/alpha/shared/**"]
    for child in alpha["children"]:
        org["nodes"].append({
            "id": child, "parent": "alpha", "children": [], "mode": "Leaf",
            "charter": {"domain": [f"src/alpha/{child}/**"], "concerns": [], "excludes": []},
        })
    org["version"] += 1
    rendered = bootstrap.runtime_files(org, source=repo / ".github" / "agent-org")
    for node in org["nodes"]:
        definition = rendered[f".github/agents/{node['id']}.md"]
        fields = frontmatter(definition)
        assert fields["user-invocable"] is (node["id"] == org["root"])
        assert loop_reference(definition).endswith(f"/{node['mode'].lower()}.md")
    assert frontmatter(rendered[".github/agents/splitter.md"])["user-invocable"] is False


def test_renderer_rejects_a_template_that_makes_children_user_invocable(tmp_path):
    source = tmp_path / "plugin"
    shutil.copytree(PLUGIN, source)
    template = source / "templates" / "_node.template.md"
    write(template, template.read_text(encoding="utf-8").replace("user-invocable: false", "user-invocable: true"))
    with pytest.raises(bootstrap.BootstrapError, match="user-invocable: false"):
        bootstrap.runtime_files(seed(), source=source)


def test_template_presence_alone_does_not_misclassify_a_source(tmp_path):
    source = tmp_path / "ambiguous source"
    write(source / "templates" / "_node.template.md", "not a runtime")
    with pytest.raises(bootstrap.BootstrapError, match="neither a plugin root nor an installed"):
        bootstrap.runtime_files(seed(), source=source)


def test_runtime_rendering_uses_live_roles_and_stable_scope(tmp_path):
    org = live_tree()
    before = json.dumps(org)
    files = bootstrap.runtime_files(org, source=PLUGIN)
    assert bootstrap.runtime_files(org) == files
    assert bootstrap.runtime_files(org, PLUGIN) == files
    assert "org.json" not in files
    assert ORG_PATH not in files
    assert bootstrap.SEED_PATH not in files
    assert json.dumps(org) == before
    for node in org["nodes"]:
        loop = loop_reference(files[f".github/agents/{node['id']}.md"])
        assert loop == f".github/agent-org/loops/{node['mode'].lower()}.md"
        assert loop in files
    assert ".github/instructions/agent-org.scope-partial.instructions.md" in files
    assert ".github/instructions/agent-org.scope-full.instructions.md" not in files
    assert ".github/agents/_node.template.md" not in files
    assert ".github/agent-org/templates/_node.template.md" in files
    assert {name for name in files if name.startswith(".github/skills/")} == {
        ".github/skills/agent-org-design/SKILL.md",
        ".github/skills/agent-org-wiki-curate/SKILL.md",
    }
    design = files[".github/skills/agent-org-design/SKILL.md"].replace(b"\r\n", b"\n")
    assert b"](..\\..\\agent-org\\org.schema.json)" in design
    assert b"](..\\..\\agent-org\\tools\\README.md)" in design
    assert not any("/bootstrap/" in name for name in files)
    assert {name for name in files if name.startswith(".github/agent-org/loops/")} == {
        ".github/agent-org/loops/leaf.md", ".github/agent-org/loops/parent.md"
    }
    assert all(Path(name).name != "AGENTS.md" for name in files)
    assert not any("bundle_validator" in path or "__pycache__" in path for path in files)
    assert files[".github/hooks/agent-org.json"] == (PLUGIN / "hooks.json").read_bytes()
    assert files[".github/extensions/agent-org/extension.mjs"] == (
        PLUGIN / "extensions" / "agent-org" / "extension.mjs"
    ).read_bytes()
    assert files[".github/agent-org/tools/hook.ps1"] == (PLUGIN / "tools" / "hook.ps1").read_bytes()
    assert files[".github/extensions/agent-org/runtime.mjs"] == (
        PLUGIN / "extensions" / "agent-org" / "runtime.mjs"
    ).read_bytes()
    for relative, content in files.items():
        write(tmp_path.joinpath(*relative.split("/")), content)
    validator_spec = importlib.util.spec_from_file_location(
        "agent_org_eval_bundle_checks", PLUGIN.parent / "eval" / "bundle_validator.py"
    )
    validator = importlib.util.module_from_spec(validator_spec)
    validator_spec.loader.exec_module(validator)
    assert validator.check_agent_roles(org, tmp_path) == []


def test_runtime_allowlist_and_optional_hook(tmp_path):
    source = tmp_path / "plugin source"
    shutil.copytree(PLUGIN, source)
    (source / "tools" / "hook.ps1").unlink(missing_ok=True)
    for relative in (
        "eval/private.py",
        "extensions/agent-org/__tests__/fixture.mjs", "extensions/agent-org/eval.mjs",
        "extensions/agent-org/test.mjs", "extensions/agent-org/test_runtime.mjs",
        "extensions/agent-org/tests/fixture.mjs",
        "tools/__pycache__/owner_validator.pyc", "tools/bundle_validator.py",
    ):
        write(source.joinpath(*relative.split("/")), "must not ship")
    helper = source / "extensions" / "agent-org" / "helpers" / "audit.mjs"
    write(helper, "export const marker = true;\n")
    files = bootstrap.runtime_files(seed(), source=source)
    assert ".github/agent-org/tools/hook.ps1" not in files
    assert files[".github/extensions/agent-org/helpers/audit.mjs"] == helper.read_bytes()
    assert all(b"must not ship" not in content for content in files.values())
    assert all(not path.startswith(("eval/", "tests/")) for path in files)


def test_idempotence_preserves_files_and_exclude_text(repo):
    exclude = exclude_file(repo)
    prefix = b"# human exclusions\r\n/private-notes"
    exclude.write_bytes(prefix)
    first = bootstrap.bootstrap_repo(repo, source=PLUGIN)
    files_before = snapshot(repo)
    excludes_before = exclude.read_bytes()
    second = bootstrap.bootstrap_repo(repo, source=PLUGIN)
    assert second["created"] == []
    assert second["exclude_added"] == []
    assert set(second["existing"]) == set(first["created"])
    assert snapshot(repo) == files_before
    assert exclude.read_bytes() == excludes_before
    assert excludes_before.startswith(prefix + b"\r\n")
    lines = excludes_before.splitlines()
    assert len(lines) == len(set(lines))


def test_local_overlay_does_not_hide_other_github_files(repo):
    unrelated = (
        ".github/agent-org/cache/user-note.md",
        ".github/agent-org/user-note.md",
        ".github/agents/human.md",
        ".github/extensions/other/extension.mjs",
        ".github/instructions/team.instructions.md",
        ".github/skills/human-tool/SKILL.md",
    )
    for relative in unrelated:
        write(repo.joinpath(*relative.split("/")), "human-owned\n")
    bootstrap.bootstrap_repo(repo, source=PLUGIN)
    for relative in unrelated:
        assert git(repo, "check-ignore", "--quiet", "--", relative, allowed=(1,)).returncode == 1
        assert repo.joinpath(*relative.split("/")).read_text(encoding="utf-8") == "human-owned\n"


@pytest.mark.parametrize("relative", [
    ".github/agent-org/tools/worktree.py",
    ".github/agents/main.md",
    ".github/hooks/agent-org.json",
    ".github/instructions/agent-org.instructions.md",
    ".github/skills/agent-org-design/SKILL.md",
    ORG_PATH,
])
def test_conflicts_are_detected_before_any_writes(repo, relative):
    write(repo.joinpath(*relative.split("/")), "unrelated user file\n")
    before = snapshot(repo)
    exclude_before = exclude_file(repo).read_bytes()
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.bootstrap_repo(repo, source=PLUGIN)
    assert snapshot(repo) == before
    assert exclude_file(repo).read_bytes() == exclude_before


def test_non_directory_destination_parent_is_a_conflict(repo):
    write(repo / ".github", "a user file, not a directory")
    before = snapshot(repo)
    with pytest.raises(bootstrap.BootstrapError, match="parent is not a directory"):
        bootstrap.bootstrap_repo(repo, source=PLUGIN)
    assert snapshot(repo) == before


def test_matching_generated_file_and_line_endings_are_preserved(repo):
    relative = ".github/agents/main.md"
    expected = bootstrap.runtime_files(seed(), source=PLUGIN)[relative].replace(b"\r\n", b"\n")
    path = repo.joinpath(*relative.split("/"))
    write(path, expected.replace(b"\n", b"\r\n"))
    content, modified = path.read_bytes(), path.stat().st_mtime_ns
    result = bootstrap.bootstrap_repo(repo, source=PLUGIN)
    assert relative in result["existing"]
    assert path.read_bytes() == content
    assert path.stat().st_mtime_ns == modified


@pytest.mark.parametrize("storage", ["local", "tracked"])
def test_dirty_repository_and_index_are_not_changed(repo, storage):
    write(repo / "README.md", "staged human changes\n")
    git(repo, "add", "README.md")
    write(repo / "README.md", "unstaged human changes\n")
    write(repo / "human-notes.txt", "not for agents\n")
    staged = git(repo, "diff", "--cached", "--binary").stdout
    unstaged = git(repo, "diff", "--binary").stdout
    branch = git(repo, "branch", "--show-current").stdout
    head = git(repo, "rev-parse", "HEAD").stdout
    bootstrap.bootstrap_repo(repo, storage=storage, source=PLUGIN)
    assert git(repo, "diff", "--cached", "--binary").stdout == staged
    assert git(repo, "diff", "--binary").stdout == unstaged
    assert git(repo, "branch", "--show-current").stdout == branch
    assert git(repo, "rev-parse", "HEAD").stdout == head
    assert (repo / "human-notes.txt").read_text(encoding="utf-8") == "not for agents\n"


@pytest.mark.parametrize("relative", [".github/agents/main.md", ORG_PATH])
def test_local_mode_rejects_tracked_generated_files(repo, relative):
    content = (json.dumps(seed()) + "\n").encode("utf-8") if relative == ORG_PATH else (
        bootstrap.runtime_files(seed(), source=PLUGIN)[relative]
    )
    write(repo.joinpath(*relative.split("/")), content)
    git(repo, "add", relative)
    before = snapshot(repo)
    index = git(repo, "diff", "--cached", "--binary").stdout
    excludes = exclude_file(repo).read_bytes()
    with pytest.raises(bootstrap.BootstrapError, match="already tracked"):
        bootstrap.bootstrap_repo(repo, source=PLUGIN)
    assert snapshot(repo) == before
    assert git(repo, "diff", "--cached", "--binary").stdout == index
    assert exclude_file(repo).read_bytes() == excludes


def test_tracked_mode_rejects_existing_ignore_conflict(repo):
    exclude = exclude_file(repo)
    exclude.write_bytes(b"# user setting\n/.github/instructions/\n")
    before, excludes = snapshot(repo), exclude.read_bytes()
    with pytest.raises(bootstrap.BootstrapError, match="ignored path"):
        bootstrap.bootstrap_repo(repo, storage="tracked", source=PLUGIN)
    assert snapshot(repo) == before
    assert exclude.read_bytes() == excludes


@pytest.mark.parametrize("changed", [
    {"collaboration": "hybrid"},
    {"root_name": "other-root"},
    {"scope": ["src/**"]},
    {"storage": "tracked"},
])
def test_conflicting_configuration_never_resets_initialized_org(repo, changed):
    bootstrap.bootstrap_repo(repo, source=PLUGIN)
    before, excludes = snapshot(repo), exclude_file(repo).read_bytes()
    with pytest.raises(bootstrap.BootstrapError, match="conflicts with requested"):
        bootstrap.bootstrap_repo(repo, source=PLUGIN, **changed)
    assert snapshot(repo) == before
    assert exclude_file(repo).read_bytes() == excludes


def test_legacy_configuration_defaults_preserve_existing_org_bytes(repo):
    original = json.dumps(seed(), indent=4).encode("utf-8")
    write(repo / "org.json", original)
    result = bootstrap.bootstrap_repo(repo, source=PLUGIN, migrate_legacy=True)
    assert (repo / ORG_PATH).read_bytes() == original
    assert not (repo / "org.json").exists()
    assert result["org"] == seed()
    assert result["migrated"] == {"source": "org.json", "destination": ORG_PATH}
    assert ".github/instructions/agent-org.collaboration-agents.instructions.md" in result["created"]
    assert ".github/instructions/agent-org.scope-full.instructions.md" in result["created"]


@pytest.mark.parametrize("storage", ["local", "tracked"])
def test_explicit_legacy_migration_preserves_split_state_storage_and_index(repo, storage):
    org = live_tree()
    org["storage"] = storage
    original = b"\xef\xbb\xbf" + json.dumps(org, indent=4).replace("\n", "\r\n").encode("utf-8")
    write(repo / "org.json", original)
    write(repo / bootstrap.SEED_PATH, json.dumps(seed()))
    write(repo / ".github/agent-org/user-note.md", "preserve target-specific state\n")
    if storage == "tracked":
        git(repo, "add", "org.json", bootstrap.SEED_PATH)
    before_index = git(repo, "diff", "--cached", "--binary").stdout
    before_exclude = exclude_file(repo).read_bytes()
    result = bootstrap.bootstrap_repo(repo, source=PLUGIN, migrate_legacy=True)
    assert (repo / ORG_PATH).read_bytes() == original
    assert result["org"] == org
    assert result["removed"] == [bootstrap.SEED_PATH]
    assert not (repo / "org.json").exists()
    assert not (repo / bootstrap.SEED_PATH).exists()
    assert (repo / ".github/agent-org/user-note.md").read_text() == "preserve target-specific state\n"
    assert git(repo, "diff", "--cached", "--binary").stdout == before_index
    assert exclude_file(repo).read_bytes().startswith(before_exclude)
    ignored = git(repo, "check-ignore", "--quiet", ORG_PATH, allowed=(0, 1)).returncode == 0
    assert ignored == (storage == "local")
    assert bootstrap.bootstrap_repo(repo, source=PLUGIN)["created"] == []


@pytest.mark.parametrize("migrate", [False, True])
@pytest.mark.parametrize("canonical", [b"{", b"{}", None])
def test_dual_live_candidates_never_select_even_equal_or_malformed(repo, migrate, canonical):
    legacy = json.dumps(seed()).encode("utf-8")
    write(repo / "org.json", legacy)
    write(repo / ORG_PATH, legacy if canonical is None else canonical)
    before, excludes = snapshot(repo), exclude_file(repo).read_bytes()
    with pytest.raises(bootstrap.BootstrapError, match="Conflicting live"):
        bootstrap.bootstrap_repo(repo, source=PLUGIN, migrate_legacy=migrate)
    assert snapshot(repo) == before
    assert exclude_file(repo).read_bytes() == excludes


def test_legacy_requires_explicit_migration_and_rejects_nondefault_seed(repo):
    write(repo / "org.json", json.dumps(live_tree()))
    before = snapshot(repo)
    with pytest.raises(bootstrap.BootstrapError, match="explicit --migrate-legacy"):
        bootstrap.bootstrap_repo(repo, source=PLUGIN)
    assert snapshot(repo) == before
    write(repo / bootstrap.SEED_PATH, json.dumps(live_tree()))
    before = snapshot(repo)
    with pytest.raises(bootstrap.BootstrapError, match="non-default legacy seed"):
        bootstrap.bootstrap_repo(repo, source=PLUGIN, migrate_legacy=True)
    assert snapshot(repo) == before


def test_installed_evolved_runtime_still_bootstraps_stable_fresh_defaults(repo, tmp_path):
    write(repo / ORG_PATH, json.dumps(live_tree()))
    bootstrap.bootstrap_repo(repo, source=PLUGIN)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    git(fresh, "init", "-q")
    # Same selected profiles as the installed assets; no live-state or org-seed source.
    result = bootstrap.bootstrap_repo(
        fresh, source=repo / ".github/agent-org", collaboration="hybrid", scope=["src/**"],
    )
    assert result["org"]["version"] == seed()["version"]
    assert result["org"]["root"] == "main"
    assert [n["id"] for n in result["org"]["nodes"]] == ["main"]
    assert not (fresh / "org.json").exists()
    assert not (fresh / bootstrap.SEED_PATH).exists()


def test_existing_split_tree_is_not_reset(repo):
    org = live_tree()
    original = json.dumps(org, indent=4).encode("utf-8")
    write(repo / ORG_PATH, original)
    result = bootstrap.bootstrap_repo(
        repo, root_name=org["root"], scope=org["scope"], collaboration=org["collaboration"], source=PLUGIN
    )
    assert result["org"] == org
    assert (repo / ORG_PATH).read_bytes() == original
    assert loop_reference((repo / ".github" / "agents" / "coordinator.md").read_bytes()) == (
        ".github/agent-org/loops/parent.md"
    )


def test_installed_source_can_render_promoted_roles_and_rebootstrap(repo):
    bootstrap.bootstrap_repo(
        repo, root_name="coordinator", scope=["src/**"], collaboration="hybrid", source=PLUGIN
    )
    org = live_tree()
    installed_source = repo / ".github" / "agent-org"
    rendered = bootstrap.runtime_files(org, source=installed_source)
    assert rendered == bootstrap.runtime_files(org, source=PLUGIN)
    for node in org["nodes"]:
        fields = frontmatter(rendered[f".github/agents/{node['id']}.md"])
        assert fields["user-invocable"] is (node["id"] == org["root"])
    write(repo / ORG_PATH, json.dumps(org, indent=2))
    for node in org["nodes"]:
        relative = f".github/agents/{node['id']}.md"
        write(repo.joinpath(*relative.split("/")), rendered[relative])
    result = bootstrap.bootstrap_repo(
        repo, root_name="coordinator", scope=["src/**"], collaboration="hybrid", source=installed_source
    )
    assert result["org"] == org
    assert result["created"] == []
    assert "/.github/agents/alpha.md" in result["exclude_added"]
    assert "/.github/agents/beta.md" in result["exclude_added"]


def test_installed_cli_locates_its_own_assets(repo, tmp_path):
    source = tmp_path / "original plugin fixture"
    shutil.copytree(PLUGIN, source)
    bootstrap.bootstrap_repo(
        repo, root_name="entry", scope=["src/**"], collaboration="hybrid", storage="tracked", source=source
    )
    shutil.rmtree(source)
    tool = repo / ".github" / "agent-org" / "tools" / "bootstrap.py"
    result = subprocess.run(
        [sys.executable, "-B", str(tool), "--repo", str(repo), "--root-name", "entry", "--scope", "src/**",
         "--collaboration", "hybrid", "--storage", "tracked"],
        capture_output=True, cwd=repo, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["created"] == []


@pytest.mark.parametrize("storage", ["local", "tracked"])
def test_installed_runtime_shared_session_contract(repo, storage):
    result = bootstrap.bootstrap_repo(
        repo, root_name="coordinator", scope=["src/**"], collaboration="hybrid", storage=storage, source=PLUGIN
    )
    if storage == "tracked":
        git(repo, "add", "--", *result["created"])
        git(repo, "-c", "user.email=tests@local", "-c", "user.name=Tests",
            "commit", "-q", "-m", "test: record tracked overlay")
    base_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    base_branch = git(repo, "branch", "--show-current").stdout.strip()
    tool = repo / ".github" / "agent-org" / "tools" / "worktree.py"
    run_id = "bootstrap-contract"
    created = subprocess.run(
        [sys.executable, "-B", str(tool), "create", "--repo", str(repo), "--session", run_id],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    run = json.loads(created.stdout)
    assert {"base_branch", "base_sha", "branch", "path", "session_id"} <= run.keys()
    assert run["session_id"] == run_id
    assert run["base_branch"] == base_branch
    assert run["base_sha"] == base_sha
    tree = Path(run["path"])
    assert tree.is_dir()
    assert json.loads((tree / ORG_PATH).read_text(encoding="utf-8")) == result["org"]
    for relative, content in bootstrap.runtime_files(result["org"], PLUGIN).items():
        assert tree.joinpath(*relative.split("/")).read_bytes() == content, relative
    for command in ("integrate", "cleanup"):
        completed = subprocess.run(
            [sys.executable, "-B", str(tool), command, "--repo", str(repo), "--session", run_id],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not tree.exists()
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == base_sha


def test_linked_worktree_exclusions_use_git_common_dir(repo, tmp_path):
    linked = tmp_path / "linked worktree with spaces"
    git(repo, "worktree", "add", "-q", "--detach", str(linked), "HEAD")
    assert (linked / ".git").is_file()
    common_exclude = exclude_file(repo)
    common_exclude.write_bytes(b"# human excludes without a final newline")
    result = bootstrap.bootstrap_repo(linked, root_name="linked-root", source=PLUGIN)
    assert Path(result["exclude_file"]) == common_exclude
    assert common_exclude == exclude_file(linked)
    assert common_exclude.read_bytes().startswith(b"# human excludes without a final newline\n")
    assert not (repo / "org.json").exists()
    assert not (repo / ORG_PATH).exists()
    assert git(linked, "status", "--porcelain", "--untracked-files=all").stdout == ""
    again = bootstrap.bootstrap_repo(linked, root_name="linked-root", source=PLUGIN)
    assert again["exclude_added"] == []


@pytest.mark.parametrize("root_name", ["Bad", "con", "host", "main/root", "splitter", "with space"])
def test_invalid_or_reserved_root_is_rejected_without_writes(repo, root_name):
    before = snapshot(repo)
    with pytest.raises(bootstrap.BootstrapError, match="Node ids|Reserved node id"):
        bootstrap.bootstrap_repo(repo, root_name=root_name, source=PLUGIN)
    assert snapshot(repo) == before


@pytest.mark.parametrize("scope", [[], "", [" "], ["src/**", ""], ["src/**\n"]])
def test_invalid_scope_is_rejected_without_writes(repo, scope):
    before = snapshot(repo)
    with pytest.raises(bootstrap.BootstrapError, match="scope must"):
        bootstrap.bootstrap_repo(repo, scope=scope, source=PLUGIN)
    assert snapshot(repo) == before


def test_non_git_directory_is_not_initialized(tmp_path):
    target = tmp_path / "not a repository"
    target.mkdir()
    with pytest.raises(bootstrap.BootstrapError, match="does not run git init"):
        bootstrap.bootstrap_repo(target, source=PLUGIN)
    assert list(target.iterdir()) == []


def test_cli_reports_non_git_error_without_mutating_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", str(PLUGIN / "tools" / "bootstrap.py"), "--repo", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode != 0
    assert "existing Git worktree" in json.loads(result.stderr)["error"]
    assert not (tmp_path / ".git").exists()
    assert not (tmp_path / "org.json").exists()
    assert not (tmp_path / ORG_PATH).exists()


def test_manifest_and_schema_configuration():
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    schema = json.loads((PLUGIN / "org.schema.json").read_text(encoding="utf-8"))
    assert manifest["author"] == "Alok Nigam"
    assert manifest["keywords"] == ["multi-agents", "self-organizing"]
    assert schema["properties"]["collaboration"]["default"] == "agents"
    assert schema["properties"]["scope"]["default"] == ["**"]
    assert schema["properties"]["storage"]["default"] == "local"
