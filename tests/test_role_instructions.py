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


def test_only_self_contained_role_files_are_shipped():
    assert {file.name for file in (PLUGIN / "loops").iterdir()} == {"leaf.md", "parent.md"}
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert "skills/agent-org-loop" not in manifest["skills"]
    for role in ("leaf", "parent"):
        content = (PLUGIN / "loops" / f"{role}.md").read_text(encoding="utf-8")
        assert "common.md" not in content
        assert r".github\agent-org\skills\agent-org-design\SKILL.md" in content
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


def test_default_root_definition_points_directly_to_leaf_body():
    text = (PLUGIN / "agents" / "main.md").read_text(encoding="utf-8")
    _, header, body = text.split("---", 2)
    assert "loop:" not in header
    assert re.findall(r"^loop: (.+)$", body, re.MULTILINE) == [r".github\agent-org\loops\leaf.md"]
    assert r".github\agents\main.md" not in body
