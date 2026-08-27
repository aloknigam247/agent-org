"""worktree lifecycle: the substrate for strict per-run isolation and serialized integration (§2.5).

Every node run executes in its own ``.worktrees/<node>/<run-id>/`` (a linked git worktree on a branch
off ``main``) and reaches ``main`` only through a single serialized integration step gated by the
owner-oracle containment check. Because coverage gives nodes disjoint domains, serialized integration of
disjoint changes is conflict-free; a change that strays outside the acting node's domain is rejected at
the gate. This module is the deterministic mechanism — it does not run agents; a caller (or a test)
supplies the work.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import owner_validator as ov  # noqa: E402

_GIT_IDENT = ["-c", "user.email=wt@local", "-c", "user.name=wt"]


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def create(repo, node_id, run_id=None):
    """Add a linked worktree ``.worktrees/<node>/<run-id>`` on a fresh branch off HEAD."""
    run_id = run_id or uuid.uuid4().hex[:8]
    rel = f".worktrees/{node_id}/{run_id}"
    branch = f"wt/{node_id}/{run_id}"
    _git(repo, "worktree", "add", "-q", "-b", branch, rel, "HEAD")
    return {"path": str(Path(repo) / rel), "rel": rel, "branch": branch, "node": node_id, "run_id": run_id}


def _changed(wt_path):
    _git(wt_path, "add", "-A")
    out = _git(wt_path, "diff", "--cached", "--name-only", "-z", "HEAD").stdout
    return [ov.normalize(p) for p in out.split("\0") if p.strip()]


def integrate(repo, wt, acting, org=None):
    """Gate the worktree's changes with the containment check, then serialize-merge into main. Returns
    ``integrated`` plus the changed paths, or the reason it was rejected (containment / conflict)."""
    org = org or json.loads((Path(repo) / "org.json").read_text(encoding="utf-8"))
    wt_path = wt["path"]
    changed = _changed(wt_path)
    if not changed:
        return {"integrated": False, "reason": "no-op", "changed": []}
    gate = ov.check_containment(org, acting, changed)
    if gate["status"] != "ok":
        return {"integrated": False, "reason": "containment", "violations": gate["violations"], "changed": changed}
    _git(wt_path, *_GIT_IDENT, "commit", "-q", "--no-verify", "-m", f"work: {acting}")
    merge = _git(repo, *_GIT_IDENT, "merge", "--no-ff", "--no-edit", wt["branch"], check=False)
    if merge.returncode != 0:
        _git(repo, "merge", "--abort", check=False)
        return {"integrated": False, "reason": "conflict", "changed": changed}
    return {"integrated": True, "changed": changed}


def cleanup(repo, wt):
    """Remove the worktree and delete its branch."""
    _git(repo, "worktree", "remove", "--force", wt["rel"], check=False)
    _git(repo, "branch", "-D", wt["branch"], check=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description="agentOrg worktree lifecycle helper")
    parser.add_argument("repo", help="repo root (must be a git work tree on main)")
    parser.add_argument("node", help="acting node id")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)
    wt = create(args.repo, args.node, args.run_id)
    print(json.dumps(wt, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
