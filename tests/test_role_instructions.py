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


def test_roles_and_splitter_agree_on_canonical_config_and_add_children():
    for role in ("leaf", "parent"):
        text = (PLUGIN / "loops" / f"{role}.md").read_text(encoding="utf-8")
        assert "Read `.github/agent-org/org.json`" in text
        assert "repository root, not the config directory" in text
    splitter = (PLUGIN / "agents" / "splitter.md").read_text(encoding="utf-8")
    assert "--split-baseline .github/agent-org/org.json" in splitter
    assert re.findall(r"^## (.+)$", splitter, re.MULTILINE) == ["SplitProposal", "Procedure"]
    assert "The supported primitive is `add-children`" in splitter
    assert "at least two new Leaf" in splitter
    assert "Existing nodes are not renamed or reparented" in splitter
    assert "Execute only a SplitProposal already approved through the Host's `ask_user` gate" in splitter
    assert "increment `version` once" in splitter


@pytest.mark.parametrize("role", ["leaf", "parent"])
def test_finish_requires_split_advice_and_an_actionable_proposal_even_for_trivial_tasks(role):
    text = (PLUGIN / "loops" / f"{role}.md").read_text(encoding="utf-8")
    finish = section(text, "Finish")
    assert "As the final step of every task, including trivial or read-only ones" in finish
    assert "`owner_validator.py --split-advice` with your node id" in finish
    assert "state its result" in finish
    assert "`recommend_split` result of true" in finish
    assert "is a verdict a human must triage, not optional advice" in finish
    assert "you MUST return a structured SplitProposal" in finish
    assert "shape in `.github\\agents\\splitter.md`" in finish
    assert "human's approve/edit/reject gate" in finish
    assert "A top node surfaces it to the Host directly" in finish
    assert "Never downgrade a verdict to advisory or take no action" in finish
    assert "Do not mutate the organization yourself" in finish


def test_parent_must_route_a_structured_proposal_for_every_child_verdict():
    text = (PLUGIN / "loops" / "parent.md").read_text(encoding="utf-8")
    reconcile = section(text, "Plan, delegate, and reconcile")
    assert "For EACH child's `recommend_split: true` verdict" in reconcile
    assert "you MUST return a structured SplitProposal" in reconcile
    assert "shape in `.github\\agents\\splitter.md`" in reconcile
    assert "Host's human approve/edit/reject gate" in reconcile
    assert "never treat it as optional advice, silently defer, or take no action" in reconcile
    assert "PROPOSE growth" not in reconcile


def test_host_must_triage_every_returned_proposal_or_verdict_before_proceeding():
    text = (PLUGIN / "instructions" / "agent-org.instructions.md").read_text(encoding="utf-8")
    host = section(text, "Host procedure")
    assert "For each returned SplitProposal or split verdict, the Host MUST use" in host
    assert "`ask_user` for approve / edit / reject before proceeding" in host
    assert "A split verdict requires human triage" in host
    assert "the Host MUST\nNOT treat it as advisory, silently defer, or take no action" in host
    assert "Require a structured SplitProposal" in host
    assert "Never mutate topology merely because a verdict is true" in host
    # Scope profiles reference the authoritative Host policy rather than copying a competing gate.
    for profile in (PLUGIN / "instructions").glob("agent-org.scope-*.instructions.md"):
        assert "agent-org.instructions.md" in profile.read_text(encoding="utf-8")


@pytest.mark.parametrize("role", ["leaf", "parent"])
def test_role_policy_lines_remain_wrapped(role):
    text = (PLUGIN / "loops" / f"{role}.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        assert len(line) <= 120, line


@pytest.mark.parametrize("source,target", [
    ("agents/splitter.md", "agents/splitter.md"),
    ("tools/README.md", "agent-org/tools/README.md"),
    ("instructions/agent-org.instructions.md", "instructions/agent-org.instructions.md"),
    ("skills/agent-org-design/SKILL.md", "skills/agent-org-design/SKILL.md"),
    ("org.schema.json", "agent-org/org.schema.json"),
])
def test_operational_document_mirrors_match(source, target):
    assert (PLUGIN / source).read_bytes() == (PLUGIN.parent / ".github" / target).read_bytes()


def test_runtime_docs_keep_readiness_and_native_completion_contracts():
    text = (PLUGIN / "tools" / "README.md").read_text(encoding="utf-8")
    assert re.findall(r"^## (.+)$", text, re.MULTILINE) == ["Bootstrap", "Owner validator", "Session worktree"]
    assert "--repo . --check-runtime" in section(text, "Bootstrap")
    assert "Existing integer versions have no minimum" in text
    assert "per-org evolution" in text
    assert "### Native child completion annotation" in text
    assert "native-event footer suffix" in text
    assert "human approval" in text
    assert "maximum observed `inputTokens`" in text
    assert "split verdict" in text
    assert "exactly one record" in text
    assert "Legacy cumulative evidence" in text
    assert "Source tests alone are not evidence of live delivery" in text
