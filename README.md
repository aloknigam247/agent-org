# agent-org — a self-organizing agent-org kernel

`agent-org` turns an ordinary git repository into one that is **completely owned by agents**: a small
tree of GitHub Copilot CLI custom agents that partition the repo, do the work, and maintain their own
knowledge and automation.

This repository is the **kernel**: the design plus the seed bootstrap you drop into a target repo.

## The two properties that define "ownership"

1. **Coverage (spatial)** — every path in the repo is owned by exactly one *leaf* node. No orphans,
   no overlaps. A parent owns only the **seams** (contracts) between its children.
2. **Self-sufficiency (epistemic)** — every node externalizes what it needs to maintain its domain
   without re-deriving it from source each time: a **wiki** (knowledge), **skills** (procedures),
   and **tools** (automation).

These properties are upheld **intrinsically**: the `splitter` preserves coverage on every atomic
split, and nodes uphold self-sufficiency by discipline (the payback rule, single-writer, freshness).
See `.github/org-design.md`.

Out of scope: testing/benchmarking of the agents themselves — to be designed separately.

## Layout

| Path | Role |
| ---- | ---- |
| `org.json` | Live org state: the whole node tree with charters inlined. |
| `org.schema.json` | Structural schema for `org.json`. |
| `.github/org-design.md` | The design reference (substrate + self-ownership). |
| `.github/copilot-instructions.md` | The Host manual (what the Copilot session itself does). |
| `.github/agents/*.md` | Live agent defs: `main` (seed), `splitter`, plus generated nodes. |

At runtime, nodes also create (on demand, never speculatively): `wiki/` pages, `skills/` playbooks,
and `tools/` scripts — each owned by exactly one node.

## Roles

- **Host** — the Copilot CLI session. Domain-less. Hardcodes the entry to `main`, gates splits to the
  human, and is the **only** writer of `org.json`.
- **main** — the root node; owns the whole repo until the first split.
- **splitter** — executes an approved split as one validated, git-committed transaction.

## Adopt it in a target repo

1. Copy `org.json`, `org.schema.json`, and `.github/` into the target repo.
2. Ensure it is a git repo (`git init` if needed) — git history **is** the org log; every split is a
   commit.
3. Start a Copilot CLI session; it invokes `main` for every request.

Growth is **one-way and gated**: nothing splits without your approval, and nothing merges back.
