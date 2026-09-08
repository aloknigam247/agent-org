"""agent-org eval graders (E2) — deterministic, property-based, reusing the owner-oracle.

Given a captured run (the manifest, the sandbox's `.github/agent-org/org.json`, the repo-root-relative
changed paths, the sandbox root, the response), produce a verdict: a list of checks each `pass`/`fail`
with evidence, and an overall `passed` = all applicable checks green. No LLM judges here (those are
advisory, deferred to E5).

Ground truth for routing is the manifest's human `expected_owner`, independent of the baseline org (s7):
the oracle only attributes what the agent *did* (which node owns each changed path), never the
expected side.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import owner_validator as ov  # noqa: E402
import bundle_validator as bv  # noqa: E402


def _match_any(globs, path):
    return bool(globs) and ov._match(ov._spec(globs), ov.normalize(path))


_UNCHANGED = object()


def grade(manifest, org, changed_paths, sandbox, response="", exit_code=0, timed_out=False,
          final_org=_UNCHANGED, run_id=None, trajectory=None):
    """Grade a run. Ownership is attributed with the immutable baseline `org` the agent was given
    (routing/containment), while coverage validates the run's `final_org` (the post-run tree). A
    missing/unparseable/conflicting final org (final_org is None) fails coverage rather than crashing."""
    checks = []
    compiled = ov.compile_nodes(org.get("nodes", []))
    final = org if final_org is _UNCHANGED else final_org

    # invocation health — a crashed or timed-out run never passes, however green the rest looks vacuously
    live = exit_code == 0 and not timed_out
    checks.append({"check": "invocation", "result": "pass" if live else "fail",
                   "evidence": f"exit={exit_code} timed_out={timed_out}"})

    # routing — the node that owns each changed path vs the manifest's human label
    expected = set(manifest.get("expected_owner") or [])
    acted, unowned, overlapped = set(), [], []
    for path in changed_paths:
        if not ov.managed(org, path):
            continue
        hits = ov.owners_of(compiled, path)
        if len(hits) == 1:
            acted.add(hits[0])
        elif not hits:
            unowned.append(ov.normalize(path))
        else:
            overlapped.append(ov.normalize(path))  # >1 owner is a coverage break, never a routing pass
    if expected:
        ok = acted.issubset(expected) and not unowned and not overlapped
        checks.append({"check": "routing", "result": "pass" if ok else "fail",
                       "evidence": f"acted={sorted(acted)} expected={sorted(expected)} "
                                   f"unowned={unowned} overlap={overlapped}"})

    # effect — fail closed on a no-op: a run that was supposed to change something but didn't (or the
    # reverse, a refuse/reject case that must change nothing) never passes vacuously.
    required_paths = manifest.get("required_paths") or []
    required_owners = set(manifest.get("required_touched_owners") or [])
    no_changes = manifest.get("expected_no_changes")
    if required_paths or required_owners or no_changes is not None:
        problems = []
        if no_changes is True and changed_paths:
            problems.append(f"expected no changes but {len(changed_paths)} occurred")
        if no_changes is False and not changed_paths:
            problems.append("expected a change but none occurred")
        unmet = [g for g in required_paths if not any(_match_any([g], p) for p in changed_paths)]
        if unmet:
            problems.append(f"required_paths unmet={unmet}")
        missing_owners = sorted(required_owners - acted)
        if missing_owners:
            problems.append(f"required_touched_owners missing={missing_owners}")
        checks.append({"check": "effect", "result": "pass" if not problems else "fail",
                       "evidence": "; ".join(problems) or "ok"})

    # paths — changes stay inside allowed regions and out of forbidden ones (regions, not exact diff)
    allowed = manifest.get("allowed_paths") or []
    forbidden = manifest.get("forbidden_paths") or []
    if allowed or forbidden:
        outside = [ov.normalize(p) for p in changed_paths if allowed and not _match_any(allowed, p)]
        inside_forbidden = [ov.normalize(p) for p in changed_paths if _match_any(forbidden, p)]
        ok = not outside and not inside_forbidden
        checks.append({"check": "paths", "result": "pass" if ok else "fail",
                       "evidence": f"outside_allowed={outside} in_forbidden={inside_forbidden}"})

    # coverage — the repo is still fully and singly owned after the change (validated on the FINAL org)
    if final is None:
        checks.append({"check": "coverage", "result": "fail",
                       "evidence": f"final {bv.ORG_PATH.as_posix()} missing, unparseable, or conflicting"})
    else:
        cov = ov.validate(final, ov.git_tracked(sandbox))
        checks.append({"check": "coverage", "result": "pass" if cov["status"] == "ok" else "fail",
                       "evidence": cov["violations"][:5]})

    if isinstance(final, dict):
        config_changes = [
            field for field, default in (
                ("collaboration", "agents"), ("root", None), ("scope", ["**"]), ("storage", "local")
            ) if org.get(field, default) != final.get(field, default)
        ]
        checks.append({"check": "configuration", "result": "fail" if config_changes else "pass",
                       "evidence": config_changes})
        if manifest.get("unit") == "splitter" and final != org:
            split = ov.check_split(org, final, ov.git_tracked(sandbox))
            checks.append({"check": "split_transition",
                           "result": "pass" if split["status"] == "ok" else "fail",
                           "evidence": split["violations"]})

    acting = manifest.get("agent", org.get("root"))
    acting_node = {n["id"]: n for n in org.get("nodes", [])}.get(acting)
    if acting_node and not acting_node.get("children") and changed_paths:
        con = ov.check_containment(org, acting, changed_paths)
        unexpected = [
            violation for violation in con["violations"]
            if not any(
                manifest.get("hook_mode", "warn") == "warn"
                and expected.get("acting", acting) == acting
                and expected.get("owner") == violation.get("owner")
                and _match_any([expected["path"]], violation.get("path", ""))
                for expected in manifest.get("expected_foreign", [])
            )
        ]
        checks.append({"check": "containment", "result": "fail" if unexpected else "pass",
                       "evidence": unexpected[:5]})

    # freshness (SO5) — if the run changed a file a bundle artifact cites as a source, it must re-touch
    # the artifact. Only appended when such an artifact exists (applicable), so it stays out of the way.
    fresh = bv.check_freshness(sandbox, changed_paths)
    if fresh.get("checked"):
        checks.append({"check": "freshness", "result": "pass" if fresh["status"] == "ok" else "fail",
                       "evidence": fresh["violations"][:5]})

    for field, check, disposition, phase in (
        ("expected_denied", "denied_writes", "deny", "attempted"),
        ("expected_foreign", "foreign_log", "warn", "completed"),
    ):
        expected_records = manifest.get(field)
        if expected_records is None:
            continue
        try:
            logged = ov.foreign_records(sandbox, run_id)
        except (OSError, ValueError) as error:
            checks.append({"check": check, "result": "fail", "evidence": f"invalid audit log: {error}"})
            continue
        relevant = [r for r in logged if r.get("disposition") == disposition and r.get("phase") == phase]
        missing = [
            expected for expected in expected_records
            if not any(
                _match_any([expected["path"]], record.get("path", ""))
                and record.get("owner") == expected.get("owner")
                and record.get("acting") == expected.get("acting", acting)
                and record.get("sessionId")
                for record in relevant
            )
        ]
        unexpected_empty = not expected_records and relevant
        checks.append({"check": check, "result": "fail" if missing or unexpected_empty else "pass",
                       "evidence": {"missing": missing, "records": relevant}})

    if manifest.get("check_role_refs"):
        roles = bv.check_agent_roles(final, sandbox) if isinstance(final, dict) else ["final org missing"]
        checks.append({"check": "role_references", "result": "fail" if roles else "pass", "evidence": roles})

    # build/tests — optional objective outcome (timeout-bounded so a hung build fails, not stalls)
    build_cmd = manifest.get("build_cmd")
    if build_cmd:
        try:
            proc = subprocess.run(build_cmd, cwd=sandbox, shell=True, capture_output=True, text=True,
                                  timeout=manifest.get("build_timeout", 60))
            result, evidence = ("pass" if proc.returncode == 0 else "fail"), f"exit={proc.returncode}"
        except subprocess.TimeoutExpired:
            result, evidence = "fail", "build timed out"
        checks.append({"check": "build", "result": result, "evidence": evidence})

    return {"passed": all(c["result"] == "pass" for c in checks), "checks": checks}


if __name__ == "__main__":
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(grade(payload["manifest"], payload["org"], payload["changed_paths"],
                           payload["sandbox"], payload.get("response", ""),
                           payload.get("exit", 0), payload.get("timed_out", False)), indent=2))
