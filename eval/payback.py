#!/usr/bin/env python
"""Paired-task payback experiment: does a node's bundle (wiki/skills/tools) pay back?

Runs the same fixture task twice — **warm** (bundle present) and **cold** (bundle stripped) — N times
each, then reports the deltas in pass_rate, cost, task input tokens, and mean tool-calls. A bundle
"pays back" when the warm arm is at least as reliable and no more expensive than the cold arm. Reuses
the run.py machinery; the cold arm strips the manifest's `bundle_paths` from each sandbox before the
agent is invoked (the agent-def still references the now-missing bundle, isolating content presence).

Run: python eval/payback.py eval/fixtures/payback-report --repeats 3
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402


def strip_bundle(bundle_paths):
    def _prepare(sandbox: Path):
        for pattern in bundle_paths:
            for p in sorted(Path(sandbox).glob(pattern), reverse=True):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.exists():
                    p.unlink()
    return _prepare


def arm_metrics(res):
    summary, runs = res["summary"], res["runs"]
    toks = [r.get("task_input_tokens") or 0 for r in runs]
    return {
        "pass_rate": summary.get("pass_rate"),
        "cost_mean": summary.get("cost", {}).get("mean"),
        "mean_input_tokens": round(sum(toks) / len(toks)) if toks else 0,
        "mean_tool_calls": summary.get("trajectory", {}).get("mean_tool_calls"),
        "judge": summary.get("judge", {}),
    }


def _delta(a, b):
    return None if (a is None or b is None) else round(a - b, 3)


def main(argv=None):
    ap = argparse.ArgumentParser(description="agentOrg payback experiment (warm vs cold bundle)")
    ap.add_argument("fixture", help="path to a fixture directory with a manifest bundle_paths field")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default=None)
    ap.add_argument("--no-judge", action="store_true", help="skip the advisory LLM judge")
    args = ap.parse_args(argv)

    fixture = Path(args.fixture)
    manifest = yaml.safe_load((fixture / "manifest.yml").read_text(encoding="utf-8"))
    bundle = manifest.get("bundle_paths") or []
    if not bundle:
        print(json.dumps({"error": "manifest has no bundle_paths; nothing to strip for the cold arm"}))
        return 2

    judge = not args.no_judge
    warm = run.run_case(fixture, args.repeats, args.model, args.effort, False, judge)
    cold = run.run_case(fixture, args.repeats, args.model, args.effort, False, judge, prepare=strip_bundle(bundle))
    w, c = arm_metrics(warm), arm_metrics(cold)

    pays_back = bool(
        w["pass_rate"] is not None and c["pass_rate"] is not None
        and w["pass_rate"] >= c["pass_rate"]
        and (w["pass_rate"] > c["pass_rate"] or w["mean_input_tokens"] <= c["mean_input_tokens"])
    )
    report = {
        "case": manifest["id"],
        "repeats": args.repeats,
        "bundle_paths": bundle,
        "warm": w,
        "cold": c,
        "delta_warm_minus_cold": {
            "pass_rate": _delta(w["pass_rate"], c["pass_rate"]),
            "cost_mean": _delta(w["cost_mean"], c["cost_mean"]),
            "mean_input_tokens": w["mean_input_tokens"] - c["mean_input_tokens"],
            "mean_tool_calls": _delta(w["mean_tool_calls"], c["mean_tool_calls"]),
        },
        "pays_back": pays_back,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
