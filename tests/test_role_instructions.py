import json
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"


def section(text, heading):
    title = f"## {heading}\n"
    assert text.count(title) == 1
    return text.partition(title)[2].split("\n## ", 1)[0].strip()


@pytest.mark.parametrize("heading", ["Explicit file-tool boundary policy", "Finish", "Orient and isolate"])
def test_shared_role_sections_remain_identical(heading):
    leaf = (PLUGIN / "loops" / "leaf.md").read_text(encoding="utf-8")
    parent = (PLUGIN / "loops" / "parent.md").read_text(encoding="utf-8")
    assert section(leaf, heading) == section(parent, heading)
    installed = PLUGIN.parent / ".github" / "agent-org" / "loops"
    assert (installed / "leaf.md").read_bytes() == (PLUGIN / "loops" / "leaf.md").read_bytes()
    assert (installed / "parent.md").read_bytes() == (PLUGIN / "loops" / "parent.md").read_bytes()


def test_only_self_contained_role_files_are_shipped():
    assert {file.name for file in (PLUGIN / "loops").iterdir()} == {"leaf.md", "parent.md"}
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["skills"] == ["skills/bootstrap"]
    for role in ("leaf", "parent"):
        content = (PLUGIN / "loops" / f"{role}.md").read_text(encoding="utf-8")
        assert "common.md" not in content
        assert "invoke `agent-org-design` through the native skill" in content.lower()
        assert "invoke `agent-org-wiki-curate`" in content.lower()
        assert "/skills reload" in content
        assert "read SKILL.md as a substitute" in content
        assert "Only the root session creates a worktree" in content
        assert "descendants never create, integrate," in content
        assert "only the root integrates and cleans up" in content


def test_role_specific_behavior_stays_separate():
    leaf = (PLUGIN / "loops" / "leaf.md").read_text(encoding="utf-8")
    parent = (PLUGIN / "loops" / "parent.md").read_text(encoding="utf-8")
    assert "Execute the planned work in your effective domain" in section(leaf, "Execute")
    assert "An unsplit root also performs the root-only lifecycle" in leaf
    assert "owning **direct\n   child**" in section(parent, "Plan, delegate, and reconcile")
    assert "Never repair a descendant's files yourself" in parent
    assert "## Plan, delegate, and reconcile" not in leaf


def test_canonical_template_points_directly_to_substituted_loop():
    text = (PLUGIN / "templates" / "_node.template.md").read_text(encoding="utf-8")
    _, header, body = text.split("---", 2)
    assert "loop:" not in header
    assert re.findall(r"^loop: (.+)$", body, re.MULTILINE) == ["{{loop}}"]
    assert "{{id}}" in body


def test_roles_and_splitter_agree_on_single_live_path_and_distinct_transitions():
    for role in ("leaf", "parent"):
        text = (PLUGIN / "loops" / f"{role}.md").read_text(encoding="utf-8")
        assert "Read `.github/agent-org/org.json`" in text
        assert "repository root, not the config directory" in text
        assert "ConfigRelocation" in text
    splitter = (PLUGIN / "agents" / "splitter.md").read_text(encoding="utf-8")
    assert "--split-baseline .github/agent-org/org.json" in splitter
    assert "--proposal $proposal --acting splitter" in splitter
    assert "## ConfigRelocation (not add-children)" in splitter
    assert "main domain entry **in place**" in splitter
    assert "increment `version` once" in splitter


@pytest.mark.parametrize("step,target", [(3, "<absolute-shared-worktree>"), (6, "<absolute-source>")])
def test_activation_docs_install_resolve_bind_before_refresh(step, target):
    readme = PLUGIN / "tools" / "README.md"
    installed = PLUGIN.parent / ".github" / "agent-org" / "tools" / "README.md"
    assert installed.read_bytes() == readme.read_bytes()
    activation = section(readme.read_text(encoding="utf-8"), "Bounded v4 -> v5 activation (Host / splitter / root)")
    assert "/plugin install <absolute-" not in activation
    assert "one Host-controlled tool invocation" in activation
    phase = re.search(rf"(?ms)^{step}\. .*?(?=^\d+\. |\Z)", activation).group()
    ordered = [
        f'client.rpc.plugins.install({{source: "{target}/plugin", workingDirectory: "{target}"}})',
        "client.rpc.plugins.list()",
        "client.rpc.extensions.discover()",
        "python $tool bind-plugin",
        f'session.rpc.metadata.setWorkingDirectory({{workingDirectory: "{target}"}})',
        "session.rpc.plugins.reload()",
    ]
    for operation in ordered:
        assert operation in phase
    positions = [phase.index(operation) for operation in ordered]
    assert positions == sorted(positions)


def test_activation_docs_require_validation_before_integration_and_finish():
    text = (PLUGIN / "tools" / "README.md").read_text(encoding="utf-8")
    activation = section(text, "Bounded v4 -> v5 activation (Host / splitter / root)")
    integration = re.search(r"(?ms)^5\. .*?(?=^6\. )", activation).group()
    final = re.search(r"(?ms)^6\. .*", activation).group()
    assert "owner_validator.py" in integration
    assert "owner_validator.py" in final
    assert integration.index("owner_validator.py") < integration.index("integrate --repo")
    assert final.index("owner_validator.py") < final.index("finish --repo") < final.index("cleanup --repo")
