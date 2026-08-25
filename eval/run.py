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
import json
import os
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
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


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


def copilot_env():
    env = dict(os.environ)
    if not env.get("COPILOT_GITHUB_TOKEN"):
        token = sh(["gh", "auth", "token"]).stdout.strip()
        if token:
            env["COPILOT_GITHUB_TOKEN"] = token
    return env


def invoke(manifest, sandbox: Path, env, model, effort, timeout):
    usage = sandbox / ".eval-usage.json"
    cmd = [
        "copilot", "-p", manifest["intent"],
        "--agent", manifest.get("agent", "main"),
        "-C", str(sandbox), "--add-dir", str(sandbox),
        "--allow-all-tools", "--no-ask-user", "--no-color", "-s",
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
        response, code = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired as exc:
        response, code, timed_out = (exc.stdout or ""), None, True
    metrics = {}
    if usage.exists():
        try:
            metrics = json.loads(usage.read_text(encoding="utf-8"))
        except Exception:
            pass
        usage.unlink(missing_ok=True)
    return {
        "response": (response or "").strip(),
        "exit": code,
        "timed_out": timed_out,
        "duration_s": round(time.time() - started, 1),
        "usage": metrics,
    }


def capture(sandbox: Path):
    status = sh(["git", "status", "--porcelain", "--untracked-files=all"], cwd=sandbox).stdout
    changed = [line[3:] for line in status.splitlines() if line.strip()]
    commits = sh(["git", "rev-list", "--count", "HEAD"], cwd=sandbox).stdout.strip()
    new_commits = (int(commits) - 1) if commits.isdigit() else 0
    worktrees = (sandbox / ".worktrees").exists()
    return {"changed_paths": changed, "new_commits": new_commits, "made_worktree": worktrees}


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
    """Aggregate N repeats into an estimated pass@1 rate, per-check rates, failure modes, and cost."""
    n = len(runs)
    if not n:
        return {}
    accepted = sum(1 for r in runs if r["grade"]["passed"] and not r.get("runaway"))
    names = sorted({c["check"] for r in runs for c in r["grade"]["checks"]})
    per_check, fail_modes = {}, {}
    for name in names:
        fails, sample = 0, None
        for r in runs:
            for c in r["grade"]["checks"]:
                if c["check"] == name and c["result"] == "fail":
                    fails += 1
                    sample = sample or c["evidence"]
        per_check[name] = round((n - fails) / n, 3)
        if fails:
            fail_modes[name] = {"failed": fails, "sample": sample}
    runaway = sum(1 for r in runs if r.get("runaway"))
    if runaway:
        fail_modes["runaway"] = {"failed": runaway,
                                 "sample": next((r["runaway_reasons"] for r in runs if r.get("runaway")), None)}
    costs = [r.get("usage", {}).get("totalPremiumRequestCost", 0) or 0 for r in runs]
    durations = [r.get("duration_s", 0) for r in runs]
    return {
        "repeats": n,
        "pass_rate": round(accepted / n, 3),  # estimated pass@1 (production gets one attempt)
        "per_check_pass_rate": per_check,
        "failure_modes": fail_modes,
        "cost": {"mean": round(sum(costs) / n, 3), "max": max(costs)},
        "duration_s": {"mean": round(sum(durations) / n, 1), "max": max(durations)},
        "runaway_count": runaway,
        "calibration": [{"domain_est_tokens": r.get("domain_est_tokens"),
                         "task_input_tokens": r.get("task_input_tokens")} for r in runs],
    }


def run_case(fixture: Path, repeats: int, model, effort, keep: bool):
    manifest = yaml.safe_load((fixture / "manifest.yml").read_text(encoding="utf-8"))
    env = copilot_env()
    runs = []
    for _ in range(repeats):
        sandbox = Path(tempfile.mkdtemp(prefix=f"eval-{manifest['id']}-"))
        try:
            build_sandbox(fixture, sandbox)
            result = invoke(manifest, sandbox, env, model, effort, manifest.get("timeout", 300))
            result.update(capture(sandbox))
            org = json.loads((sandbox / "org.json").read_text(encoding="utf-8"))
            result["grade"] = graders.grade(manifest, org, result["changed_paths"], sandbox, result["response"])
            mark_runaway(result, manifest)
            add_calibration(result, org, sandbox, manifest.get("agent", "main"))
            if keep:
                result["sandbox"] = str(sandbox)
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
    args = parser.parse_args(argv)
    result = run_case(Path(args.fixture), args.repeats, args.model, args.effort, args.keep)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
