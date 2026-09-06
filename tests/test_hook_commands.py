import json
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(tmp_path)], check=True)
    tool_dir = tmp_path / ".github" / "agent-org" / "tools"
    tool_dir.mkdir(parents=True)
    for name in ("hook.ps1", "owner_validator.py"):
        shutil.copy2(PLUGIN / "tools" / name, tool_dir / name)
    org = {
        "version": 3, "root": "coordinator", "scope": ["**"], "nodes": [
            {"id": "coordinator", "parent": None, "children": ["catalog", "orders"], "mode": "Parent",
             "charter": {"domain": [".github/**", "org.json"], "concerns": [], "excludes": []}},
            {"id": "catalog", "parent": "coordinator", "children": [], "mode": "Leaf",
             "charter": {"domain": ["catalog/**"], "concerns": [], "excludes": []}},
            {"id": "orders", "parent": "coordinator", "children": [], "mode": "Leaf",
             "charter": {"domain": ["orders/**"], "concerns": [], "excludes": []}},
        ],
    }
    (tmp_path / "org.json").write_text(json.dumps(org), encoding="utf-8")
    return tmp_path


def invoke(repo, event, payload, cwd):
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(repo / ".github" / "agent-org" / "tools" / "hook.ps1"), "-Event", event],
        cwd=cwd, input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("actor,decision", [("catalog", "allow"), ("coordinator", "deny")])
def test_powershell_entry_uses_payload_identity_and_workspace(repo, tmp_path_factory, actor, decision):
    cwd = tmp_path_factory.mktemp("different-process-cwd")
    session_id = f"{actor}-session"
    data = {"cwd": str(repo), "sessionId": session_id}
    invoke(repo, "userPromptSubmitted", {
        **data, "prompt": f"AgentOrgActingNode: {actor}\nAgentOrgRunId: run-one\nWork",
    }, cwd)
    call = {**data, "toolName": "create", "toolArgs": {"path": "orders/new.txt"}}
    assert invoke(repo, "preToolUse", call, cwd)["permissionDecision"] == decision
    if decision == "allow":
        warning = invoke(repo, "postToolUse", {**call, "toolResult": {"resultType": "success"}}, cwd)
        assert "orders/new.txt" in warning["additionalContext"]
    entries = (repo / ".git" / "agent-org" / "foreign" / "run-one" / f"{session_id}.jsonl").read_text()
    assert f'"acting": "{actor}"' in entries


def test_canonical_hook_configuration_is_windows_only():
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == {"postToolUse", "preToolUse", "userPromptSubmitted"}
    for name, entries in hooks.items():
        for entry in entries:
            assert "bash" not in entry
            assert rf".github\agent-org\tools\hook.ps1 -Event {name}" in entry["powershell"]
