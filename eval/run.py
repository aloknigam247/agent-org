"""agentOrg eval harness.

Runs one fixture case against the kernel's agents, offline and headless: build a disposable sandbox
from a fixture, invoke a Copilot agent with `copilot -p --agent`, capture what it did (response,
changed paths, commits, usage), grade it against the manifest, and tear down. Repeating a case N times
turns a stochastic agent into an estimated pass@1 rate with per-check rates, failure modes, cost, a
runaway count, and calibration pairs for the split threshold.

A fixture is a directory `eval/fixtures/<case>/` with:
  - `manifest.yml` — the case definition (see eval/README.md).
  - `seed/`        — the repo state under test (org.json + domain files); the kernel's `.github/`
                     and `org.schema.json` are overlaid by the runner so fixtures stay small.

Auth: the harness's Copilot subprocess cannot use the parent session's Entra auth, so it sets
`COPILOT_GITHUB_TOKEN` from `gh auth token` (the local GitHub login).
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graders  # noqa: E402
import owner_validator as ov  # noqa: E402  (path added by graders import)

KERNEL = Path(__file__).resolve().parent.parent


def sh(args, cwd=None, env=None, timeout=None):
    # decode as utf-8 (copilot's JSON stream is utf-8); replace stray bytes so a reader thread never
    # crashes on Windows' default cp1252 codec.
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def build_sandbox(fixture: Path, dest: Path):
    """Overlay the kernel skeleton, then the fixture seed, and make a baseline commit."""
    shutil.copytree(KERNEL / ".github", dest / ".github")
    shutil.copy2(KERNEL / "org.schema.json", dest / "org.schema.json")
    if (KERNEL / ".gitignore").exists():
        shutil.copy2(KERNEL / ".gitignore", dest / ".gitignore")
    seed = fixture / "seed"
    for item in seed.iterdir():
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    sh(["git", "init", "-q", "-b", "main"], cwd=dest)
    sh(["git", "config", "core.autocrlf", "false"], cwd=dest)
    sh(["git", "config", "core.eol", "lf"], cwd=dest)
    # Hermetic: ignore the user's global core.hooksPath (e.g. commitlint) so sandbox commits succeed.
    sh(["git", "config", "core.hooksPath", str(dest / ".git" / "hooks")], cwd=dest)
    sh(["git", "add", "-A"], cwd=dest)
    sh(["git", "-c", "user.email=eval@local", "-c", "user.name=eval", "commit", "-q", "--no-verify", "-m", "chore: seed"], cwd=dest)
    return sh(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip()


def copilot_env():
    env = dict(os.environ)
    if not env.get("COPILOT_GITHUB_TOKEN"):
        token = sh(["gh", "auth", "token"]).stdout.strip()
        if token:
            env["COPILOT_GITHUB_TOKEN"] = token
    return env


def _parse_jsonl(stream):
    events = []
    for line in (stream or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            pass
    return events


def _extract_response(events):
    """The agent's final text: the result event if present, else the concatenated assistant messages."""
    for e in reversed(events):
        if e.get("type") == "result":
            data = e.get("data")
            if isinstance(data, str):
                return data.strip()
            if isinstance(data, dict):
                for k in ("response", "message", "content", "text"):
                    if isinstance(data.get(k), str):
                        return data[k].strip()
    texts = []
    for e in events:
        if e.get("type") == "assistant.message":
            data = e.get("data", {})
            c = (data.get("content") or data.get("text")) if isinstance(data, dict) else None
            if isinstance(c, str):
                texts.append(c)
    return "\n".join(texts).strip()


def _extract_trajectory(events):
    """Ordered tool calls [{tool, args, success}], joining start/complete by toolCallId."""
    by_id, order = {}, []
    for e in events:
        t = e.get("type")
        d = e.get("data", {}) if isinstance(e.get("data"), dict) else {}
        if t == "tool.execution_start":
            item = {"tool": d.get("toolName"), "args": d.get("arguments"), "success": None}
            by_id[d.get("toolCallId")] = item
            order.append(item)
        elif t == "tool.execution_complete":
            item = by_id.get(d.get("toolCallId"))
            if item is not None:
                item["success"] = d.get("success")
    return order


SHELL_TOOLS = {"bash", "pwsh", "powershell", "shell", "run_in_terminal", "native_tools-pwsh"}
AGENT_TOOLS = {"task", "invoke_agent", "run_agent", "run_factory"}


def _shell_command(item):
    a = item.get("args") or {}
    return (a.get("command") or a.get("cmd") or a.get("script") or "") if isinstance(a, dict) else ""


def analyze_trajectory(trajectory):
    """Advisory-only trajectory signals (never affects the pass/fail verdict, per E5 fully-advisory)."""
    cmds = [_shell_command(t) for t in trajectory if t.get("tool") in SHELL_TOOLS]
    oracle_idx = next((i for i, c in enumerate(cmds) if "owner_validator" in c), None)
    commit_idx = next((i for i, c in enumerate(cmds) if "git" in c and "commit" in c), None)
    delegated = []
    for t in trajectory:
        if t.get("tool") in AGENT_TOOLS:
            a = t.get("args") or {}
            ag = (a.get("agent_type") or a.get("agent") or a.get("name")) if isinstance(a, dict) else None
            if ag:
                delegated.append(ag)
    repeats = collections.Counter((t.get("tool"), json.dumps(t.get("args"), sort_keys=True, default=str))
                                  for t in trajectory)
    return {
        "tool_calls": len(trajectory),
        "tools": dict(collections.Counter(t.get("tool") for t in trajectory)),
        "delegated_to": delegated,
        "ran_oracle": oracle_idx is not None,
        "committed": commit_idx is not None,
        "oracle_before_commit": commit_idx is None or (oracle_idx is not None and oracle_idx < commit_idx),
        "looped_tools": sorted({tool for (tool, _), n in repeats.items() if n >= 3}),
    }


INFRA_STATUS = {401, 402, 403, 429}


def _infra_error(events):
    """Detect an infrastructure failure (quota/auth/rate-limit) — the agent never really ran, so the
    run must not count as an agent pass/fail. Returns a compact dict or None."""
    for e in events:
        if e.get("type") == "session.error":
            d = e.get("data") or {}
            return {"type": d.get("errorType") or d.get("errorCode") or "error",
                    "status": d.get("statusCode"), "message": (d.get("message") or "")[:200]}
    for e in events:
        if e.get("type") in ("model.call_failure", "model.model_call_failure"):
            d = e.get("data") or {}
            status = d.get("statusCode") or (d.get("modelCall") or {}).get("status")
            if status in INFRA_STATUS:
                return {"type": "model_call_failure", "status": status,
                        "message": (d.get("errorMessage") or "")[:200]}
    return None


def invoke(manifest, sandbox: Path, env, model, effort, timeout):
    usage = sandbox / ".eval-usage.json"
    cmd = [
        "copilot", "-p", manifest["intent"],
        "--agent", manifest.get("agent", "main"),
        "-C", str(sandbox), "--add-dir", str(sandbox),
        "--allow-all-tools", "--no-ask-user", "--no-color", "--output-format", "json",
        "--log-level", "none", "--usage-output-file", str(usage),
    ]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    started = time.time()
    timed_out = False
    try:
        proc = sh(cmd, cwd=sandbox, env=env, timeout=timeout)
        stream, code = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stream, code, timed_out = (exc.stdout or ""), None, True
    events = _parse_jsonl(stream)
    metrics = {}
    if usage.exists():
        try:
            metrics = json.loads(usage.read_text(encoding="utf-8"))
        except Exception:
            pass
        usage.unlink(missing_ok=True)
    return {
        "response": _extract_response(events),
        "exit": code,
        "timed_out": timed_out,
        "duration_s": round(time.time() - started, 1),
        "usage": metrics,
        "trajectory": _extract_trajectory(events),
        "infra_error": _infra_error(events),
    }


def _parse_name_status_z(text):
    """Parse `git diff --name-status -z` into [(status, path)], expanding rename/copy to both sides."""
    toks = text.split("\0")
    out, i = [], 0
    while i < len(toks):
        s = toks[i]
        if not s:
            i += 1
            continue
        code = s[0]
        if code in ("R", "C") and i + 2 < len(toks):
            out.append((code, ov.normalize(toks[i + 1])))  # source
            out.append((code, ov.normalize(toks[i + 2])))  # destination
            i += 3
        elif i + 1 < len(toks):
            out.append((code, ov.normalize(toks[i + 1])))
            i += 2
        else:
            i += 1
    return out


def capture(sandbox: Path, baseline_sha):
    """The complete baseline→final change set: committed + staged + unstaged + untracked, rename/NUL-safe.
    The sandbox is disposable and the agent has already run, so staging everything to fold untracked files
    into a single diff against the baseline is safe."""
    sh(["git", "add", "-A"], cwd=sandbox)
    diff = sh(["git", "diff", "--cached", "--name-status", "-z", "--find-renames", baseline_sha],
              cwd=sandbox).stdout
    changed = _parse_name_status_z(diff)
    commits = sh(["git", "rev-list", "--count", "HEAD"], cwd=sandbox).stdout.strip()
    new_commits = (int(commits) - 1) if commits.isdigit() else 0
    return {"changed_paths": sorted({p for _, p in changed}),
            "new_commits": new_commits,
            "made_worktree": (sandbox / ".worktrees").exists()}


def mark_runaway(result, manifest):
    """Runaway gate (s10): flag non-termination, not inefficiency — timeout / cost / model-call caps."""
    reasons = []
    if result.get("timed_out"):
        reasons.append("timeout")
    usage = result.get("usage", {})
    max_cost = manifest.get("max_cost")
    cost = usage.get("totalPremiumRequestCost")
    if max_cost is not None and cost is not None and cost > max_cost:
        reasons.append(f"cost>{max_cost}")
    max_calls = manifest.get("max_model_calls")
    calls = usage.get("totalUserRequests")
    if max_calls is not None and calls is not None and calls > max_calls:
        reasons.append(f"calls>{max_calls}")
    result["runaway"] = bool(reasons)
    result["runaway_reasons"] = reasons


def add_calibration(result, org, sandbox, acting):
    """Record the pair (static domain-size proxy, actual task tokens) to recalibrate the 60% threshold."""
    try:
        result["domain_est_tokens"] = ov.domain_size(org, acting, sandbox)["est_tokens"]
    except Exception:
        result["domain_est_tokens"] = None
    model_metrics = result.get("usage", {}).get("modelMetrics", {})
    result["task_input_tokens"] = sum(v.get("usage", {}).get("inputTokens", 0) for v in model_metrics.values())
    result["task_output_tokens"] = sum(v.get("usage", {}).get("outputTokens", 0) for v in model_metrics.values())


def summarize(runs):
    """Aggregate N repeats into an estimated pass@1 rate, per-check rates, failure modes, and cost.
    Runs that failed to invoke for infrastructure reasons (quota/auth/rate-limit) tell us nothing about
    the agent, so they are excluded from the quality numbers and reported separately."""
    n = len(runs)
    if not n:
        return {}
    infra = [r for r in runs if r.get("infra_error")]
    valid = [r for r in runs if not r.get("infra_error")]
    if not valid:
        return {"repeats": n, "infra_error_count": len(infra), "pass_rate": None,
                "note": "all runs failed to invoke (infrastructure/quota); not a measure of the agent",
                "infra_error_sample": infra[0]["infra_error"] if infra else None}
    m = len(valid)
    accepted = sum(1 for r in valid if r["grade"]["passed"] and not r.get("runaway"))
    names = sorted({c["check"] for r in valid for c in r["grade"]["checks"]})
    per_check, fail_modes = {}, {}
    for name in names:
        fails, sample = 0, None
        for r in valid:
            for c in r["grade"]["checks"]:
                if c["check"] == name and c["result"] == "fail":
                    fails += 1
                    sample = sample or c["evidence"]
        per_check[name] = round((m - fails) / m, 3)
        if fails:
            fail_modes[name] = {"failed": fails, "sample": sample}
    runaway = sum(1 for r in valid if r.get("runaway"))
    if runaway:
        fail_modes["runaway"] = {"failed": runaway,
                                 "sample": next((r["runaway_reasons"] for r in valid if r.get("runaway")), None)}
    costs = [r.get("usage", {}).get("totalPremiumRequestCost", 0) or 0 for r in valid]
    durations = [r.get("duration_s", 0) for r in valid]
    out = {
        "repeats": n,
        "valid_runs": m,
        "pass_rate": round(accepted / m, 3),  # estimated pass@1 over runs that actually invoked
        "per_check_pass_rate": per_check,
        "failure_modes": fail_modes,
        "cost": {"mean": round(sum(costs) / m, 3), "max": max(costs)},
        "duration_s": {"mean": round(sum(durations) / m, 1), "max": max(durations)},
        "runaway_count": runaway,
        "calibration": [{"domain_est_tokens": r.get("domain_est_tokens"),
                         "task_input_tokens": r.get("task_input_tokens")} for r in valid],
        "trajectory": _summarize_trajectory(valid),  # advisory-only (never folded into pass_rate)
        "judge": _summarize_judge(valid),             # advisory-only LLM verdict tally (empty if no rubric)
    }
    if infra:
        out["infra_error_count"] = len(infra)
    return out


def _summarize_judge(runs):
    verdicts = [r["judge"]["verdict"] for r in runs if r.get("judge")]
    return dict(collections.Counter(verdicts)) if verdicts else {}


def _summarize_trajectory(runs):
    """Aggregate the advisory trajectory signals across repeats. Reported, never scored."""
    a = [r["trajectory_analysis"] for r in runs if r.get("trajectory_analysis")]
    if not a:
        return {}
    n = len(a)
    return {
        "mean_tool_calls": round(sum(x["tool_calls"] for x in a) / n, 1),
        "delegation_rate": round(sum(1 for x in a if x["delegated_to"]) / n, 3),
        "ran_oracle_rate": round(sum(1 for x in a if x["ran_oracle"]) / n, 3),
        "committed_rate": round(sum(1 for x in a if x["committed"]) / n, 3),
        "oracle_before_commit_rate": round(sum(1 for x in a if x["oracle_before_commit"]) / n, 3),
        "looped_any_rate": round(sum(1 for x in a if x["looped_tools"]) / n, 3),
    }


JUDGE_RUBRIC_PREFIX = (
    "You are a strict, impartial evaluator of an AI agent's work. Judge ONLY the question in `rubric` "
    "using the evidence in the payload; do not run tools. Reply with exactly one JSON object and "
    "nothing else: {\"verdict\": \"pass\" | \"fail\" | \"unsure\", \"rationale\": \"<= 1 sentence\"}."
)


def _parse_judge(text):
    start, end = text.find("{"), text.rfind("}")
    candidate = text[start:end + 1] if (start != -1 and end > start) else text
    # strict JSON first, then a whitespace-normalized retry (LLMs often put raw newlines in strings)
    for attempt in (candidate, re.sub(r"\s+", " ", candidate)):
        try:
            o = json.loads(attempt)
            v = str(o.get("verdict", "unsure")).lower()
            return {"verdict": v if v in ("pass", "fail", "unsure") else "unsure",
                    "rationale": str(o.get("rationale", ""))[:200]}
        except Exception:
            pass
    vm = re.search(r"verdict\W+(pass|fail|unsure)", text, re.I)  # last-ditch loose extraction
    if vm:
        rm = re.search(r"rationale\W+\"?([^\"}\n]{0,200})", text, re.I)
        return {"verdict": vm.group(1).lower(), "rationale": (rm.group(1).strip() if rm else "")[:200]}
    return {"verdict": "unsure", "rationale": (text[:120] or "no judge output")}


def judge_run(manifest, result, env, model):
    """Advisory LLM judge, opt-in via the manifest `judge` rubric. Never folded into pass/fail."""
    rubric = manifest.get("judge")
    if not rubric:
        return None
    payload = {
        "rubric": rubric,
        "intent": manifest.get("intent"),
        "required_behavior": manifest.get("required_behavior"),
        "agent_response": (result.get("response") or "")[:2000],
        "changed_paths": result.get("changed_paths", []),
    }
    prompt = JUDGE_RUBRIC_PREFIX + "\n\n" + json.dumps(payload, indent=2)
    scratch = Path(tempfile.mkdtemp(prefix="judge-"))
    try:
        cmd = ["copilot", "-p", prompt, "-C", str(scratch), "--allow-all-tools", "--no-ask-user",
               "--no-color", "-s", "--log-level", "none"]
        if model:
            cmd += ["--model", model]
        try:
            return _parse_judge((sh(cmd, cwd=scratch, env=env, timeout=120).stdout or "").strip())
        except subprocess.TimeoutExpired:
            return {"verdict": "unsure", "rationale": "judge timed out"}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def run_case(fixture: Path, repeats: int, model, effort, keep: bool, judge: bool = True, prepare=None):
    manifest = yaml.safe_load((fixture / "manifest.yml").read_text(encoding="utf-8"))
    env = copilot_env()
    runs = []
    for _ in range(repeats):
        sandbox = Path(tempfile.mkdtemp(prefix=f"eval-{manifest['id']}-"))
        try:
            baseline_sha = build_sandbox(fixture, sandbox)
            if prepare:
                prepare(sandbox)  # e.g. strip the node's bundle for a payback cold arm
                # fold the prepared state into the baseline so it is not counted as the agent's change
                sh(["git", "add", "-A"], cwd=sandbox)
                sh(["git", "-c", "user.email=eval@local", "-c", "user.name=eval",
                    "commit", "-q", "--no-verify", "--amend", "-m", "chore: seed"], cwd=sandbox)
                baseline_sha = sh(["git", "rev-parse", "HEAD"], cwd=sandbox).stdout.strip()
            result = invoke(manifest, sandbox, env, model, effort, manifest.get("timeout", 300))
            result.update(capture(sandbox, baseline_sha))
            # attribute the agent's edits with the immutable seed org (H3); validate the final org separately
            baseline_org = json.loads((fixture / "seed" / "org.json").read_text(encoding="utf-8"))
            try:
                final_org = json.loads((sandbox / "org.json").read_text(encoding="utf-8"))
            except Exception:
                final_org = None  # broken/deleted → coverage fails (H9), never crashes the run
            result["grade"] = graders.grade(manifest, baseline_org, result["changed_paths"], sandbox,
                                             result["response"], result["exit"], result["timed_out"],
                                             final_org=final_org)
            mark_runaway(result, manifest)
            add_calibration(result, baseline_org, sandbox, manifest.get("agent", "main"))
            result["trajectory_analysis"] = analyze_trajectory(result.get("trajectory", []))
            if judge:
                verdict = judge_run(manifest, result, env, model)
                if verdict:
                    result["judge"] = verdict
            if keep:
                result["sandbox"] = str(sandbox)
            else:
                result.pop("trajectory", None)  # keep the compact analysis; drop the raw call list
            runs.append(result)
        finally:
            if not keep:
                shutil.rmtree(sandbox, ignore_errors=True)
    return {"case": manifest["id"], "unit": manifest.get("unit"), "agent": manifest.get("agent", "main"),
            "summary": summarize(runs), "runs": runs}


def main(argv=None):
    parser = argparse.ArgumentParser(description="agentOrg eval harness")
    parser.add_argument("fixture", help="path to a fixture directory")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default=None)
    parser.add_argument("--keep", action="store_true", help="keep the sandbox for inspection")
    parser.add_argument("--no-judge", action="store_true", help="skip the advisory LLM judge")
    args = parser.parse_args(argv)
    result = run_case(Path(args.fixture), args.repeats, args.model, args.effort, args.keep, not args.no_judge)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
