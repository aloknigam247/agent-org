"""agentOrg eval graders (E2) — deterministic, property-based, reusing the owner-oracle.

Given a captured run (the manifest, the sandbox's `org.json`, the changed paths, the sandbox path,
the response), produce a verdict: a list of checks each `pass`/`fail` with evidence, and an overall
`passed` = all applicable checks green. No LLM judges here (those are advisory, deferred to E5).

Ground truth for routing is the manifest's human `expected_owner`, independent of `org.json` (s7):
the oracle only attributes what the agent *did* (which node owns each changed path), never the
expected side.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".github" / "tools"))
import owner_validator as ov  # noqa: E402


def _match_any(globs, path):
    return bool(globs) and ov._match(ov._spec(globs), ov.normalize(path))


def grade(manifest, org, changed_paths, sandbox, response="", exit_code=0, timed_out=False):
    checks = []
    compiled = ov.compile_nodes(org.get("nodes", []))

    # invocation health — a crashed or timed-out run never passes, however green the rest looks vacuously
    live = exit_code == 0 and not timed_out
    checks.append({"check": "invocation", "result": "pass" if live else "fail",
                   "evidence": f"exit={exit_code} timed_out={timed_out}"})

    # routing — the node that owns each changed path vs the manifest's human label
    expected = set(manifest.get("expected_owner") or [])
    acted, unowned = set(), []
    for path in changed_paths:
        hits = ov.owners_of(compiled, path)
        if len(hits) == 1:
            acted.add(hits[0])
        elif not hits:
            unowned.append(ov.normalize(path))
    if expected:
        ok = acted.issubset(expected) and not unowned
        checks.append({"check": "routing", "result": "pass" if ok else "fail",
                       "evidence": f"acted={sorted(acted)} expected={sorted(expected)} unowned={unowned}"})

    # paths — changes stay inside allowed regions and out of forbidden ones (regions, not exact diff)
    allowed = manifest.get("allowed_paths") or []
    forbidden = manifest.get("forbidden_paths") or []
    if allowed or forbidden:
        outside = [ov.normalize(p) for p in changed_paths if allowed and not _match_any(allowed, p)]
        inside_forbidden = [ov.normalize(p) for p in changed_paths if _match_any(forbidden, p)]
        ok = not outside and not inside_forbidden
        checks.append({"check": "paths", "result": "pass" if ok else "fail",
                       "evidence": f"outside_allowed={outside} in_forbidden={inside_forbidden}"})

    # coverage — the repo is still fully and singly owned after the change
    cov = ov.validate(org, ov.git_tracked(sandbox))
    checks.append({"check": "coverage", "result": "pass" if cov["status"] == "ok" else "fail",
                   "evidence": cov["violations"][:5]})

    # containment — every change is owned by the acting node (the agent under test)
    acting = manifest.get("agent", "main")
    if acting in {n["id"] for n in org.get("nodes", [])} and changed_paths:
        con = ov.check_containment(org, acting, changed_paths)
        checks.append({"check": "containment", "result": "pass" if con["status"] == "ok" else "fail",
                       "evidence": con["violations"][:5]})

    # build/tests — optional objective outcome
    build_cmd = manifest.get("build_cmd")
    if build_cmd:
        proc = subprocess.run(build_cmd, cwd=sandbox, shell=True, capture_output=True, text=True)
        checks.append({"check": "build", "result": "pass" if proc.returncode == 0 else "fail",
                       "evidence": f"exit={proc.returncode}"})

    return {"passed": all(c["result"] == "pass" for c in checks), "checks": checks}


if __name__ == "__main__":
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(grade(payload["manifest"], payload["org"], payload["changed_paths"],
                           payload["sandbox"], payload.get("response", ""),
                           payload.get("exit", 0), payload.get("timed_out", False)), indent=2))
