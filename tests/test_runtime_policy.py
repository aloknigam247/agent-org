import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin" / "tools"))
import owner_validator as oracle


def node(name, parent, domain, children=()):
    return {
        "id": name, "parent": parent, "children": list(children),
        "mode": "Parent" if children else "Leaf",
        "charter": {"domain": domain, "concerns": [], "excludes": []},
    }


@pytest.fixture
def org():
    return {
        "version": 3, "root": "hub", "collaboration": "hybrid", "scope": ["**"], "storage": "local",
        "nodes": [
            node("hub", None, ["org.json", ".github/**", "shared/**"], ["east", "west"]),
            node("east", "hub", ["east/shared/**"], ["billing", "stock"]),
            node("billing", "east", ["billing/**"]),
            node("stock", "east", ["stock/**"]),
            node("west", "hub", ["west/shared/**"], ["returns", "shipping"]),
            node("returns", "west", ["returns/**"]),
            node("shipping", "west", ["shipping/**"]),
        ],
    }


@pytest.fixture
def repo(tmp_path, org):
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(tmp_path)], check=True)
    (tmp_path / "org.json").write_text(json.dumps(org), encoding="utf-8")
    for folder in ("billing", "shipping", "stock"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "data.txt").write_text("original", encoding="utf-8")
    return tmp_path


def payload(repo, path, sid="child-one", tool="create"):
    return {"cwd": str(repo), "sessionId": sid, "toolName": tool, "toolArgs": {"path": path}}


@pytest.mark.parametrize(
    "actor,path,expected",
    [
        ("billing", "billing/data.txt", "allow"),
        ("billing", "east/shared/policy.txt", "allow"),
        ("billing", "shipping/data.txt", "allow"),
        ("billing", "stock/data.txt", "allow"),
        ("billing", "unassigned.txt", "allow"),
        ("east", "billing/data.txt", "deny"),
        ("east", "shipping/data.txt", "allow"),
        ("hub", "billing/data.txt", "deny"),
        ("hub", "east/shared/policy.txt", "deny"),
        ("hub", "shared/policy.txt", "allow"),
    ],
)
def test_relationship_write_policy(org, repo, actor, path, expected):
    decision, records = oracle.classify_write(payload(repo, path), org, acting=actor)
    assert decision["permissionDecision"] == expected
    if expected == "deny":
        assert records[0]["disposition"] == "deny"
        assert "must delegate" in decision["permissionDecisionReason"]
    elif path != "billing/data.txt" and path != "shared/policy.txt":
        assert records[0]["disposition"] == "warn"


def test_multi_file_patch_cannot_hide_descendant_write(org, repo):
    call = {
        "cwd": str(repo), "sessionId": "parent", "toolName": "apply_patch",
        "toolArgs": {"input": "*** Begin Patch\n*** Update File: shared/policy.txt\n"
                              "*** Update File: stock/data.txt\n*** End Patch"},
    }
    decision, records = oracle.classify_write(call, org, acting="hub")
    assert decision["permissionDecision"] == "deny"
    assert [r["path"] for r in records] == ["stock/data.txt"]


def test_freeform_patch_is_classified_like_a_structured_patch(org, repo):
    call = {
        "cwd": str(repo), "sessionId": "parent", "toolName": "apply_patch",
        "toolArgs": "*** Begin Patch\n*** Add File: stock/new.txt\n+new\n*** End Patch",
    }
    decision, records = oracle.classify_write(call, org, acting="hub")
    assert decision["permissionDecision"] == "deny"
    assert records[0]["owner"] == "stock"


def test_partial_unmanaged_write_allowed_even_in_enforce_mode(org, repo):
    org["scope"] = ["billing/**"]
    decision, records = oracle.classify_write(payload(repo, "new-human-area.txt"), org, "enforce", "billing")
    assert decision["permissionDecision"] == "allow" and not records
    assert oracle.ownership(org, "new-human-area.txt")["status"] == "unmanaged"
    assert oracle.check_coverage(org, ["billing/data.txt", "new-human-area.txt"]) == []


def test_partial_scope_gap_is_not_mistaken_for_unmanaged(org):
    org["scope"] = ["billing/**"]
    org["nodes"][2]["charter"]["domain"] = ["billing/invoices/**"]
    result = oracle.check_coverage(org, ["billing/gap.txt", "human/readme.md"])
    assert [v["path"] for v in result] == ["billing/gap.txt"]
    assert oracle.ownership(org, "billing/gap.txt")["status"] == "unowned"


def test_split_cannot_redefine_scope(org):
    old = {"version": 3, "root": "keeper", "scope": ["billing/**"], "nodes": [
        node("keeper", None, ["billing/**"]),
    ]}
    new = {"version": 4, "root": "keeper", "scope": ["billing/**"], "nodes": [
        node("keeper", None, ["billing/shared/**"], ["invoices", "payments"]),
        node("invoices", "keeper", ["billing/invoices/**"]),
        node("payments", "keeper", ["billing/payments/**"]),
    ]}
    paths = ["billing/invoices/one", "billing/payments/two", "human.txt"]
    assert oracle.check_split(old, new, paths)["status"] == "ok"
    assert oracle.ownership(new, "billing/payments/two")["status"] == "owned"
    widened = copy.deepcopy(new)
    widened["scope"] = ["**"]
    assert oracle.check_split(old, widened, paths)["status"] == "violations"


def test_task_context_binds_each_child_to_same_run(org, repo):
    oracle.record_acting({
        "cwd": str(repo), "sessionId": "parent-session",
        "prompt": f"AgentOrgActingNode: east\nAgentOrgRunId: root-run\nAgentOrgWorktree: {repo}\nPlan",
    })
    for child in ("billing", "stock"):
        call = {"cwd": str(repo), "sessionId": "parent-session", "toolName": "task",
                "toolArgs": {"agent_type": child, "prompt": "Implement your part"}}
        changed = oracle.process_hook(call)["modifiedArgs"]["prompt"]
        assert changed.startswith(f"AgentOrgActingNode: {child}\n")
        assert "AgentOrgRunId: root-run" in changed
        assert f"AgentOrgWorktree: {repo}" in changed
        assert "ParentAgentSessionId: parent-session" in changed


def test_prompt_examples_cannot_bind_or_rebind_a_session(repo):
    oracle.record_acting({"cwd": str(repo), "sessionId": "sample",
                          "prompt": "Explain this example: AgentOrgActingNode: billing"})
    assert oracle._acting_from_map({"cwd": str(repo), "sessionId": "sample"}) is None
    oracle.record_acting({"cwd": str(repo), "sessionId": "sample", "prompt": "AgentOrgActingNode: billing\nWork"})
    with pytest.raises(ValueError, match="cannot change"):
        oracle.record_acting({"cwd": str(repo), "sessionId": "sample", "prompt": "AgentOrgActingNode: hub\nWork"})


@pytest.mark.parametrize(
    "field,first,second",
    [
        ("AgentOrgRunId", "first-root", "second-root"),
        ("AgentOrgWorktree", r"D:\first", r"D:\second"),
        ("ParentAgentSessionId", "first-parent", "second-parent"),
    ],
)
def test_session_context_cannot_move_between_root_runs(repo, field, first, second):
    common = {"cwd": str(repo), "sessionId": "child"}
    oracle.record_acting({**common, "prompt": f"AgentOrgActingNode: billing\n{field}: {first}\nWork"})
    with pytest.raises(ValueError, match="cannot change"):
        oracle.record_acting({**common, "prompt": f"AgentOrgActingNode: billing\n{field}: {second}\nWork"})


@pytest.mark.parametrize("sid", ["../elsewhere", r"..\elsewhere", "", "/absolute", "NUL", "con"])
def test_runtime_state_identifiers_cannot_escape(repo, sid):
    with pytest.raises(ValueError):
        oracle.record_acting({"cwd": str(repo), "sessionId": sid or "/empty",
                              "prompt": "AgentOrgActingNode: billing\nWork"})


def test_parallel_instances_logs_do_not_mix_roots(repo):
    for sid, run_id in (("first-billing", "first-root"), ("second-billing", "second-root")):
        oracle.record_acting({"cwd": str(repo), "sessionId": sid,
                              "prompt": f"AgentOrgActingNode: billing\nAgentOrgRunId: {run_id}\nWork"})
        call = payload(repo, "stock/data.txt", sid)
        before = oracle.process_hook(call)
        assert before["permissionDecision"] == "allow"
        assert "additionalContext" in before
        after = oracle.process_hook({**call, "toolResult": {"resultType": "success"}}, after=True)
        assert "stock/data.txt" in after["additionalContext"]
    first = oracle.foreign_records(repo, "first-root")
    second = oracle.foreign_records(repo, "second-root")
    assert {r["sessionId"] for r in first} == {"first-billing"}
    assert {r["sessionId"] for r in second} == {"second-billing"}
    assert {r["phase"] for r in first} == {"attempted", "completed"}


def test_payload_cwd_not_process_cwd_resolves_shared_repo(org, repo, monkeypatch, tmp_path_factory):
    oracle.record_acting({"cwd": str(repo), "sessionId": "billing-session",
                          "prompt": "AgentOrgActingNode: billing\nWork"})
    monkeypatch.chdir(tmp_path_factory.mktemp("unrelated"))
    call = payload(repo / "billing", str(repo / "stock" / "data.txt"), "billing-session")
    assert oracle.process_hook(call)["permissionDecision"] == "allow"
    assert oracle.foreign_records(repo)[0]["owner"] == "stock"


def test_hybrid_drift_reports_human_changes_across_runs(org, repo):
    org["scope"] = ["billing/**"]
    oracle.checkpoint(org, repo)
    (repo / "billing" / "data.txt").write_text("human edit", encoding="utf-8")
    (repo / "billing" / "new.txt").write_text("human addition", encoding="utf-8")
    (repo / "shipping" / "data.txt").write_text("outside scope", encoding="utf-8")
    result = oracle.drift(org, repo)
    assert {p["path"] for p in result["changes"]} == {"billing/data.txt", "billing/new.txt"}
    assert {p["owner"] for p in result["changes"]} == {"billing"}
    oracle.checkpoint(org, repo)
    assert oracle.drift(org, repo)["status"] == "ok"
    (repo / "billing" / "data.txt").unlink()
    assert oracle.drift(org, repo)["changes"][0]["change"] == "removed"


def test_hybrid_drift_includes_already_committed_human_edits(org, repo):
    oracle.git_output(repo, "config", "core.hooksPath", str(repo / ".git" / "hooks"))
    oracle.git_output(repo, "add", "-A")
    oracle.git_output(repo, "-c", "user.name=eval", "-c", "user.email=eval@local",
                      "commit", "-qm", "chore: baseline")
    oracle.checkpoint(org, repo)
    (repo / "billing" / "data.txt").write_text("committed human edit", encoding="utf-8")
    oracle.git_output(repo, "add", "-A")
    oracle.git_output(repo, "-c", "user.name=eval", "-c", "user.email=eval@local",
                      "commit", "-qm", "feat: change billing data")
    assert oracle.git_changed(repo) == []
    result = oracle.drift(org, repo)
    assert [change["path"] for change in result["changes"]] == ["billing/data.txt"]
