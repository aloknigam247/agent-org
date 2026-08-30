# agent-org — a self-organizing agent-org kernel

`agent-org` turns an ordinary git repository into one that is **completely owned by agents**: a small
tree of GitHub Copilot CLI custom agents that partition the repo, do the work, and maintain their own
knowledge and automation.

This repository packages the kernel as an installable **GitHub Copilot CLI plugin** (under `plugin/`),
plus the eval harness that tests it. Install the plugin, then bootstrap any git repo to adopt it.

## The two properties that define "ownership"

1. **Coverage (spatial)** — every path in the repo is owned by exactly one *leaf* node. No orphans,
   no overlaps. A parent owns only the **seams** (contracts) between its children.
2. **Self-sufficiency (epistemic)** — every node externalizes what it needs to maintain its domain
   without re-deriving it from source each time: a **wiki** (knowledge), **skills** (procedures),
   and **tools** (automation).

These properties are upheld **intrinsically**: the `splitter` preserves coverage on every atomic
split, and nodes uphold self-sufficiency by discipline (the payback rule, single-writer, freshness).
See the `agent-org-design` skill under `plugin/skills/`.

The agents themselves are tested by an offline eval harness under `eval/` (see `eval/TEST-PLAN.md`).

## Layout

| Path | Role |
| ---- | ---- |
| `plugin/` | The installable Copilot CLI plugin (see **Install** below). |
| `plugin/agents/*.md` | Seed agent defs: `main`, `splitter`, and `_node.template` for generated children. |
| `plugin/skills/` | Law-as-skills (`agent-org-loop`, `agent-org-design`) and the `bootstrap` skill. |
| `plugin/tools/` | The owner-oracle (`owner_validator.py`) plus the bundle and worktree validators. |
| `plugin/hooks.json` | The pre-tool-use containment hook. |
| `plugin/org.schema.json` | Structural schema for `org.json`. |
| `eval/`, `tests/` | The kernel's own test bed — never shipped in the plugin. |

A **bootstrapped** target repo gets `org.json` (live org state) plus a git-excluded `.github/` overlay
(tools, seed agents, Host manual). At runtime, nodes also create — on demand, never speculatively —
`wiki/` pages, `skills/` playbooks, and `tools/` scripts, each owned by exactly one node.

## Roles

- **Host** — the Copilot CLI session. Domain-less. Hardcodes the entry to `main` and gates splits to
  the human. The `splitter` is the only writer of `org.json`.
- **main** — the root node; owns the whole repo until the first split.
- **splitter** — executes an approved split as one validated, git-committed transaction.

## Install

`agent-org` is a GitHub Copilot CLI plugin.

**1 — Install the plugin**, from the repo's `plugin/` subdirectory or a local clone:

```pwsh
copilot plugin install aloknigam247/agent-org:plugin      # from GitHub
# or, from a local clone:
copilot plugin install ./agent-org/plugin
```

Start a new session (or `/restart`) so it loads; confirm with `copilot plugin list`. The oracle needs
**Python 3 with `pathspec`** on PATH (`pip install pathspec`; the bootstrap does this too).

**2 — Bootstrap your repo.** In the target git repo, ask Copilot to **run the agent-org bootstrap**
(the `bootstrap` skill). It is **non-invasive** — everything it writes is added to `.git/info/exclude`,
so it never appears in `git status` or gets committed. It:

- creates `org.json` (root `main` owns the whole repo),
- copies the tools into `.github/tools/` and installs the containment hook,
- verifies coverage with the oracle.

**3 — Use it.** Work through the org: `main` routes or executes, the containment hook keeps every write
inside the acting node's domain, and a node proposes a **gated** split when its domain grows too large.
Growth is **one-way and human-approved** — nothing splits without your say-so, and nothing merges back.
