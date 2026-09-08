#!/usr/bin/env python
"""Eval-only bundle metadata and freshness scenarios."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import bundle_validator as bv  # noqa: E402

ORG = {"version": 3, "root": "root", "nodes": [
    {"id": "root", "charter": {"domain": ["shared/**", "/.github/agent-org/org.json"]},
     "parent": None, "children": ["a", "b"], "mode": "Parent"},
    {"id": "a", "charter": {"domain": ["a/**", ".github/skills/agent-org-a-*/**"]},
     "parent": "root", "children": [], "mode": "Leaf"},
    {"id": "b", "charter": {"domain": ["b/**", ".github/skills/agent-org-b-*/**"]},
     "parent": "root", "children": [], "mode": "Leaf"}]}


@pytest.fixture
def repo(file_tree):
    agents = {f".github/agents/{node['id']}.md": f"# {node['id']}\n" for node in ORG["nodes"]}
    return lambda files: file_tree({bv.ORG_PATH.as_posix(): json.dumps(ORG), **agents, **files})


def evidences(result):
    return [v["evidence"] for v in result["violations"]]


def test_cli_canonical_default_resolves_paths_from_repo_root(repo, file_tree, monkeypatch, capsys):
    root = repo({
        ".github/skills/agent-org-a-refresh/SKILL.md": (
            "---\nname: agent-org-a-refresh\nowner: a\nsources: [a/x.py]\n"
            "user-invocable: false\n---\n"
        ),
        "a/x.py": "source",
        "wiki/a/notes.md": "---\nowner: a\nsources: [.github/agent-org/org.json, a/x.py]\n---\n",
    })
    elsewhere = file_tree({bv.ORG_PATH.as_posix(): "{not the requested repository"})
    monkeypatch.chdir(elsewhere)
    assert bv.main(["--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok", "violations": []}


@pytest.mark.parametrize("absolute", [False, True], ids=["repo-relative", "absolute"])
@pytest.mark.parametrize("canonical_state", ["present", "missing", "malformed"])
def test_cli_explicit_org_override(repo, file_tree, monkeypatch, capsys, absolute, canonical_state):
    candidate = "candidates/organization.json"
    root = repo({candidate: json.dumps(ORG)})
    canonical = root / bv.ORG_PATH
    if canonical_state == "missing":
        canonical.unlink()
    elif canonical_state == "malformed":
        canonical.write_text("{invalid", encoding="utf-8")
    monkeypatch.chdir(file_tree({}))
    selected = str(root / candidate) if absolute else candidate
    assert bv.main(["--root", str(root), "--org", selected]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_cli_requires_canonical_org(repo):
    root = repo({})
    (root / bv.ORG_PATH).unlink()
    with pytest.raises(FileNotFoundError, match=r"Missing canonical organization: .*\.github[/\\]agent-org[/\\]org\.json"):
        bv.main(["--root", str(root)])


def test_cli_rejects_malformed_canonical_org(repo):
    root = repo({})
    (root / bv.ORG_PATH).write_text("{invalid", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        bv.main(["--root", str(root)])


def test_cli_ignores_unrelated_root_file(repo, capsys):
    root = repo({"org.json": "{not an installed organization"})
    assert bv.main(["--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_front_matter_scalar_and_lists():
    assert bv._front_matter("---\nowner: a\nsources: [x, y]\n---\nbody")["owner"] == "a"
    assert bv._front_matter("---\nowner: a\nsources: [x, y]\n---\n")["sources"] == ["x", "y"]
    block = "---\nowner: b\nsources:\n  - one.py\n  - two.py\n---\n"
    assert bv._front_matter(block)["sources"] == ["one.py", "two.py"]
    assert bv._front_matter("no front matter") == {}


def test_valid_bundle_passes(repo):
    r = bv.check_bundle(ORG, repo({
        ".github/skills/agent-org-a-refresh/SKILL.md": (
            "---\nname: agent-org-a-refresh\nowner: a\nsources: [a/x.py]\n"
            "user-invocable: false\n---\nsteps"
        ),
        ".github/skills/agent-org-a-refresh/examples/sample.md": "example",
        "wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes",
        "a/x.py": "y",
        "tools/b/run.py": "print(1)"}))
    assert r["status"] == "ok", r


def test_so2_missing_agent_def_fails(repo):
    d = repo({})
    (d / ".github" / "agents" / "b.md").unlink()  # remove a live node's def
    assert any("missing agent-def" in e for e in evidences(bv.check_bundle(ORG, d)))


def test_so6_orphan_namespace_fails(repo):
    r = bv.check_bundle(ORG, repo({"wiki/ghost/page.md": "x"}))  # 'ghost' is not a live node
    assert any("orphan" in e for e in evidences(r)), r


def test_standard_skill_orphan_fails(repo):
    r = bv.check_bundle(ORG, repo({
        ".github/skills/agent-org-ghost-refresh/SKILL.md": (
            "---\nname: agent-org-ghost-refresh\nowner: ghost\nuser-invocable: false\n---\n"
        ),
    }))
    assert any("orphan" in e for e in evidences(r)), r


def test_unrelated_standard_skill_is_not_an_agent_org_bundle(repo):
    r = bv.check_bundle(ORG, repo({
        ".github/skills/human-release/SKILL.md": "---\nname: human-release\nowner: person\n---\n",
    }))
    assert r["status"] == "ok", r


def test_operational_skills_are_not_node_owned_bundles(repo):
    r = bv.check_bundle(ORG, repo({
        ".github/skills/agent-org-design/SKILL.md": "---\nname: agent-org-design\n---\n",
        ".github/skills/agent-org-wiki-curate/SKILL.md": "---\nname: agent-org-wiki-curate\n---\n",
    }))
    assert r["status"] == "ok", r


def test_skill_charters_disambiguate_prefix_node_ids(file_tree):
    prefix_org = {"version": 1, "root": "a", "nodes": [
        {"id": "a", "charter": {
            "domain": ["a/**", ".github/skills/agent-org-a-*/**"],
            "excludes": [".github/skills/agent-org-a-b-*/**"],
        }, "parent": None, "children": ["a-b", "other"], "mode": "Parent"},
        {"id": "a-b", "charter": {
            "domain": ["a-b/**", ".github/skills/agent-org-a-b-*/**"],
        }, "parent": "a", "children": [], "mode": "Leaf"},
        {"id": "other", "charter": {"domain": ["other/**"]}, "parent": "a", "children": [], "mode": "Leaf"},
    ]}
    root = file_tree({
        ".github/agents/a.md": "# a",
        ".github/agents/a-b.md": "# a-b",
        ".github/agents/other.md": "# other",
        ".github/skills/agent-org-a-b-refresh/SKILL.md": (
            "---\nname: agent-org-a-b-refresh\nowner: a-b\nuser-invocable: false\n---\n"
        ),
    })
    assert bv.check_bundle(prefix_org, root)["status"] == "ok"


def test_skill_owner_metadata_cannot_override_charter_owner(repo):
    r = bv.check_bundle(ORG, repo({
        ".github/skills/agent-org-a-refresh/SKILL.md": (
            "---\nname: agent-org-a-refresh\nowner: b\nuser-invocable: false\n---\n"
        ),
    }))
    assert any("owner 'b' != charter owner 'a'" in e for e in evidences(r)), r


def test_quoted_native_skill_metadata_is_parsed_as_yaml(repo):
    r = bv.check_bundle(ORG, repo({
        ".github/skills/agent-org-a-refresh/SKILL.md": (
            '---\nname: "agent-org-a-refresh"\nowner: "a"\nsources: ["a/x.py"]\n'
            'disable-model-invocation: false\nuser-invocable: false\n---\nsteps'
        ),
        "a/x.py": "y",
    }))
    assert r["status"] == "ok", r


def test_primary_skill_dangling_source_fails(repo):
    r = bv.check_bundle(ORG, repo({
        ".github/skills/agent-org-a-refresh/SKILL.md": (
            '---\nname: "agent-org-a-refresh"\nowner: "a"\nsources: ["a/missing.py"]\n'
            "user-invocable: false\n---\nsteps"
        ),
    }))
    assert any("dangling source 'a/missing.py'" in e for e in evidences(r)), r
    assert sum("owner" in e for e in evidences(r)) == 0


def test_skill_folder_and_metadata_name_must_match(repo):
    r = bv.check_bundle(ORG, repo({
        ".github/skills/agent-org-a-refresh/SKILL.md": (
            "---\nname: agent-org-a-other\nowner: a\nuser-invocable: false\n---\n"
        ),
    }))
    assert any("skill name" in e for e in evidences(r)), r


@pytest.mark.parametrize(
    "field",
    [
        'disable-model-invocation: "false"\nuser-invocable: false',
        "disable-model-invocation: 0\nuser-invocable: false",
        "disable-model-invocation: true\nuser-invocable: false",
        'user-invocable: "false"',
        "user-invocable: 0",
        "user-invocable: true",
    ],
)
def test_node_skills_are_internal_but_model_invocable(repo, field):
    r = bv.check_bundle(ORG, repo({
        ".github/skills/agent-org-a-refresh/SKILL.md": (
            f"---\nname: agent-org-a-refresh\nowner: a\n{field}\n---\n"
        ),
    }))
    assert any("invoc" in e for e in evidences(r)), r


@pytest.mark.parametrize("ownership", ["other", "overlap", "uncovered"])
def test_skill_companions_require_matching_charter_ownership(repo, ownership):
    companion = ".github/skills/agent-org-a-refresh/scripts/run.py"
    org = copy.deepcopy(ORG)
    if ownership != "overlap":
        org["nodes"][1]["charter"]["excludes"] = [companion]
    if ownership != "uncovered":
        org["nodes"][2]["charter"]["domain"].append(companion)
    r = bv.check_bundle(org, repo({
        ".github/skills/agent-org-a-refresh/SKILL.md": (
            "---\nname: agent-org-a-refresh\nowner: a\nuser-invocable: false\n---\n"
        ),
        companion: "print(1)",
    }))
    assert any("charter ownership" in e for e in evidences(r)), r


def test_partial_scope_node_skill_uses_explicit_charter_grant(file_tree):
    skill = ".github/skills/agent-org-a-refresh"
    companion = f"{skill}/scripts/run.py"
    partial_org = {
        "version": 1,
        "root": "root",
        "scope": ["src/**"],
        "nodes": [
            {
                "id": "root",
                "charter": {"domain": ["shared/**"]},
                "parent": None,
                "children": ["a"],
                "mode": "Parent",
            },
            {
                "id": "a",
                "charter": {"domain": ["src/a/**", f"{skill}/**"]},
                "parent": "root",
                "children": [],
                "mode": "Leaf",
            },
        ],
    }

    def tree(owner="a"):
        return file_tree({
            ".github/agents/a.md": "# a",
            ".github/agents/root.md": "# root",
            f"{skill}/SKILL.md": (
                f"---\nname: agent-org-a-refresh\nowner: {owner}\nsources: [src/a/x.py]\n"
                "user-invocable: false\n---\n"
            ),
            companion: "print(1)",
            "src/a/x.py": "x",
        })

    assert bv.check_bundle(partial_org, tree())["status"] == "ok"

    metadata_mismatch = bv.check_bundle(partial_org, tree(owner="root"))
    assert any("owner 'root' != charter owner 'a'" in e for e in evidences(metadata_mismatch))

    companion_org = copy.deepcopy(partial_org)
    companion_org["nodes"][1]["charter"]["excludes"] = [companion]
    companion_org["nodes"][0]["charter"]["domain"].append(companion)
    companion_mismatch = bv.check_bundle(companion_org, tree())
    assert any("charter ownership ['root'] != owning skill 'a'" in e for e in evidences(companion_mismatch))


def test_so3_owner_namespace_mismatch_fails(repo):
    r = bv.check_bundle(ORG, repo({"wiki/a/page.md": "---\nowner: b\n---\nx"}))  # under a/, claims owner b
    assert any("single-writer" in e for e in evidences(r)), r


def test_dangling_source_fails(repo):
    r = bv.check_bundle(ORG, repo({"wiki/a/page.md": "---\nowner: a\nsources: [a/missing.py]\n---\nx"}))
    assert any("dangling source" in e for e in evidences(r)), r


@pytest.mark.parametrize("artifact", [
    "wiki/a/layout.md",
    "tools/a/layout.md",
    ".github/skills/agent-org-a-refresh/SKILL.md",
    ".github/skills/agent-org-a-refresh/references/layout.md",
])
def test_canonical_org_sources_preserve_repo_relative_freshness(repo, artifact):
    header = "---\nname: agent-org-a-refresh\nowner: a\nuser-invocable: false\n"
    root = repo({
        ".github/skills/agent-org-a-refresh/SKILL.md": header + "---\n",
        artifact: header + "sources: [.github/agent-org/org.json, a/x.py]\n---\n",
        "a/x.py": "source",
    })
    assert bv.check_bundle(ORG, root)["status"] == "ok"
    stale = bv.check_freshness(root, [".github\\agent-org\\org.json"])
    assert [violation["path"] for violation in stale["violations"]] == [artifact]
    assert bv.check_freshness(root, [bv.ORG_PATH.as_posix(), artifact])["status"] == "ok"


@pytest.mark.parametrize("artifact", ["wiki/a/layout.md", ".github/skills/agent-org-a-refresh/SKILL.md"])
def test_sources_are_not_rebased_to_the_org_directory(repo, artifact):
    root = repo({
        ".github/agent-org/settings.json": "{}",
        artifact: (
            "---\nname: agent-org-a-refresh\nowner: a\nuser-invocable: false\n"
            "sources: [settings.json]\n---\n"
        ),
    })
    # A file beside the organization does not satisfy a repository-root source.
    assert not (root / "settings.json").exists()
    assert any("dangling source 'settings.json'" in evidence for evidence in evidences(bv.check_bundle(ORG, root)))


# --- SO5 freshness: a source changed but the artifact citing it was not re-touched ------------------

def test_freshness_stale_when_source_changed_not_retouched(repo):
    d = repo({"wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes", "a/x.py": "y"})
    r = bv.check_freshness(d, ["a/x.py"])  # source changed, notes.md not touched
    assert r["status"] == "violations"
    assert any("not re-touched" in e for e in evidences(r)), r


def test_freshness_ok_when_retouched_together(repo):
    d = repo({"wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes", "a/x.py": "y"})
    r = bv.check_freshness(d, ["a/x.py", "wiki/a/notes.md"])  # both changed in the same run
    assert r["status"] == "ok", r
    assert r["checked"] == 1


def test_freshness_ok_when_source_unchanged(repo):
    d = repo({"wiki/a/notes.md": "---\nowner: a\nsources: [a/x.py]\n---\nnotes", "a/x.py": "y"})
    r = bv.check_freshness(d, ["b/unrelated.py"])  # nothing the artifact depends on changed
    assert r["status"] == "ok", r


def test_standard_skill_freshness_uses_skill_metadata(repo):
    skill = ".github/skills/agent-org-a-refresh/SKILL.md"
    d = repo({
        skill: "---\nname: agent-org-a-refresh\nowner: a\nsources: [a/x.py]\n---\nsteps",
        "a/x.py": "y",
    })
    stale = bv.check_freshness(d, ["a/x.py"])
    assert any("not re-touched" in e for e in evidences(stale)), stale
    assert bv.check_freshness(d, ["a/x.py", skill])["status"] == "ok"


@pytest.mark.parametrize("sources", ["[null]", "[1]", "{path: a/x.py}"])
def test_node_skill_malformed_sources_are_violations(repo, sources):
    d = repo({
        ".github/skills/agent-org-a-refresh/SKILL.md": (
            f"---\nname: agent-org-a-refresh\nowner: a\nsources: {sources}\n"
            "user-invocable: false\n---\n"
        ),
    })
    bundle = bv.check_bundle(ORG, d)
    freshness = bv.check_freshness(d, ["a/x.py"])
    assert any("sources must be a list of non-empty path strings" in e for e in evidences(bundle)), bundle
    assert any("sources must be a list of non-empty path strings" in e for e in evidences(freshness)), freshness
