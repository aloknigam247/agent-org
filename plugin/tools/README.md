# agent-org runtime tools

The deterministic engine of the agent-org kernel. These ship inside the plugin and are copied into a
bootstrapped repo's `.github/tools/` (a git-excluded overlay), where the agents, the containment hook,
and CI all invoke them. They need only `pathspec` (`pip install -r requirements.txt`). Node-owned
bundles live separately under `tools/<node>/`.

## owner-oracle — `owner_validator.py`

Computes `owner(path)` for the repo's files against `org.json` and reports violations
(`uncovered`/`UNOWNED`, `overlap`, `tree`). One source of truth, used by the `splitter`, the integration
gate, and the containment hook. Exit `0` when `status: ok`, non-zero on any violation. Below, `OV` is
`python .github/tools/owner_validator.py`.

```pwsh
OV --org org.json --root .                       # validate the whole repo (tracked + untracked)
OV --org org.json --paths src/foo.py src/bar.ts  # validate an explicit path set
OV --org org.json --owner src/foo.py             # who owns a path?
OV --org org.json --acting <node-id>             # integration gate: are a node's changes in its domain?
OV --org org.json --size <node-id>               # split self-check: domain size vs the context window
OV --org new.json --split-baseline old.json      # validate a proposed split transition
OV --hook --org org.json                         # pre-tool-use hook: read a payload on stdin, print allow/deny
```

## bundle-integrity — `bundle_validator.py`

Deterministic self-ownership checks (design §3.7 SO2–SO6): every live node has an agent-def; each
wiki/skill/tool sits in a live node's namespace with a matching `owner`; sources resolve. Also
`check_freshness` (a changed source must re-touch its artifact).

```pwsh
python .github/tools/bundle_validator.py --org org.json --root .
```

## worktree — `worktree.py`

The isolation substrate (design §2.5): create a per-run `.worktrees/<node>/<run-id>` worktree, gate its
changes with the containment check, serialize-merge into `main`, and clean up.
