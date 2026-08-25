# Agent-Org design

This is the reference design for the `agentOrg` kernel: a self-organizing organization of GitHub
Copilot CLI custom agents that **completely owns** a git repository.

Read this when in doubt. `org.json` is the live state; this document is the law.

---

## 1. Thesis — ownership = coverage + self-sufficiency

A repository is **completely owned by agents** when two properties hold and *stay* true:

- **Coverage (spatial ownership).** Every tracked path is owned by exactly one **leaf** node. No
  orphan files, no overlaps. A parent owns only the **seams** — the contracts *between* its children.
- **Self-sufficiency (epistemic ownership).** Every node externalizes what it needs to maintain its
  domain without re-deriving it from source each time: a **wiki** (knowledge), **skills**
  (procedures), and **tools** (automation).

These properties are upheld **intrinsically**, with no separate machinery:

- the **splitter** preserves coverage as part of every atomic split (§2.2, §3.6), backed by
  `org.schema.json`;
- **nodes** uphold self-sufficiency by discipline — the payback rule, single-writer, and freshness
  (§3).

Out of scope: testing/benchmarking of the agents themselves — to be designed separately.

---

## 2. Substrate

The organizational mechanics. If you know the earlier attempt, skim to §2.2 and §2.5.

### 2.1 Nodes and roles

- **Host** — the Copilot CLI session itself. Domain-less. It hardcodes the entry point to `main`,
  gates splits to the human, and is the **only** writer of `org.json`. It never writes feature code.
- **main** — the root node. Owns the whole repo (`**/*`) until the first split.
- **splitter** — a meta-agent that executes an approved split as one validated, git-committed
  transaction.
- **nodes** — the working agents. Each is a `Parent` or a `Leaf`:
  - **Leaf** implements a task directly within its domain.
  - **Parent** does **scatter-gather**: decompose the task → map subtasks to children by charter →
    invoke child sub-agents → aggregate. A parent writes no domain code itself; it owns only seams.

### 2.2 Charters and coverage

A node's **charter** = `{ domain, concerns, excludes }` (see `org.schema.json`):

- `domain` — glob patterns the node owns.
- `concerns` — human-readable responsibilities inside the domain.
- `excludes` — globs inside `domain` that are owned elsewhere (a child, or a sibling via the common
  parent). **Every exclude must be covered by another node's domain.**

The schema validates structure. The following **coverage rules** — which the `splitter` enforces on
every split (§3.6) — complete the definition of coverage and are the direct fix for the earlier
"disjoint include/exclude" bug:

1. Every tracked path matches exactly one **leaf** domain (coverage, no gaps).
2. No two leaves overlap.
3. Every `exclude` glob is covered by some other node's `domain` (no disjoint excludes).
4. For every parent: `⋃ children.domain ⊆ parent.domain`, and
   `(⋃ children.domain) ∪ parent.retained == parent.domain` (no gaps, no leaks).
5. Tree is acyclic, single-root; `parent`/`children` back-references agree; a `Leaf` has no children;
   a `Parent` has ≥ 2 children.

Canonical example (the Spec Review Platform): `main` (Parent) retains root/org/contract files and
excludes `src/Api/**`, `tests/**`, `src/web/**`, `infra/**`, `**/Dockerfile`, … which are the
domains of `backend`, `frontend`, and `infra`. Coverage holds because every excluded glob is some
child's domain, and the children plus `main`'s retained set reconstruct `**/*`.

### 2.3 Growth — gated, one-way splits

A node splits **only after** a task, out-of-band, when it is overloaded. Overload signal (fixed, from
the prior research): **≥ 60% of the 200K context window** (~120K input tokens) sustained on its own
domain. A node only **proposes**; it never mutates the org. Three kinds:

- **Vertical** — the node becomes a Parent that holds the map/contracts; ≥ 2 children own the detail.
  Use when one domain has grown too deep.
- **Horizontal** — partition one domain into sibling leaves. Use when a domain has independent parts.
- **Grouping-node** — insert an intermediate Parent to reduce a parent's fan-out. Use when a Parent
  has too many children to route well.

Every split is **gated** (human approves/edits/rejects via the Host) and **one-way** (no merge-back).
Executed by the `splitter` as one atomic git commit; `org.json` `version` bumps by one. Git history
**is** the org log.

### 2.4 Seams / contracts

When a domain splits into parts that must agree on an interface (e.g. `backend` ↔ `frontend` over a
REST/OpenAPI + SignalR contract), that interface is a **seam**. Seams are owned by the **common
parent**, never by a child, and are documented as `wiki/**/<name>.contract.md`. A change on one side
of a seam is not "done" until the seam doc and the other side agree — a rule the seam-owning parent
upholds.

### 2.5 Concurrency — worktree isolation

Multiple nodes may execute in parallel. Even with disjoint domains they can collide on the working
tree, the index, or build outputs. **Invariant:** each concurrently-executing node works in its own
git worktree under `.worktrees/<node-id>/`, and results are integrated on the main tree only after
the node completes its work. This is a hard fix for the "concurrent agents collided" failure.

### 2.6 Where things live

| Path | What | Written by |
| ---- | ---- | ---------- |
| `org.json` | Live org state (whole tree, charters inlined). | Host only, on split. |
| `org.schema.json` | Structural schema. | Humans/kernel. |
| `.github/org-design.md` | This design. | Humans/kernel. |
| `.github/copilot-instructions.md` | Host manual. | Humans/kernel. |
| `.github/agents/<id>.md` | Live agent def per node. `main` is seed; children generated on split. | splitter. |
| `wiki/**` | Partitioned knowledge + `*.contract.md` seams. | Owning node, on demand. |
| `skills/<name>/SKILL.md` | Procedures. | Owning node, on demand. |
| `tools/**` | Reusable scripts + `tools/manifest.md`. | Owning node, on demand. |

---

## 3. Self-Ownership

Coverage makes every file owned. Self-sufficiency makes every *owner* able to maintain its domain
cheaply. This section defines what each node must externalize, when, and in what shape.

### 3.1 The bundle

Every live node owns a **bundle**:

- **agent-def** — `.github/agents/<id>.md`: identity, charter pointer, operating loop.
- **wiki** — the knowledge it would otherwise re-derive from source.
- **skills** — the procedures it repeats.
- **tools** — the scripts that collapse multi-step operations into one command.

"Completely owned" = coverage (every file has an owner) **and** every owner has a maintained bundle.

### 3.2 Artifact taxonomy — three kinds, three triggers

| Kind | Is | Create when | Never |
| ---- | -- | ----------- | ----- |
| **wiki** page | Durable *knowledge*: architecture, invariants, decisions, gotchas, seam meaning. | You had to *derive* non-obvious domain knowledge from source that you (or a weaker model) will need again. | Restate what source plainly says; duplicate another node's page. |
| **skill** | A documented *procedure* an agent follows (numbered steps; may call tools). | A multi-step task has ≥ 2 expected recurrences and its steps are stable (e.g. "add an API endpoint", "add a migration"). | Encode a one-off; encode something a tool already does deterministically. |
| **tool** | A deterministic *script* (PowerShell/Python/Node) that runs a mechanical sequence. | A mechanical sequence recurs and can be scripted end-to-end (build, targeted test, scaffold, lint, data fix). | Script a judgement call; wrap a single trivial command. |

Rule of thumb: **knowledge → wiki, know-how → skill, mechanics → tool.** A skill *may invoke* tools; a
tool never contains judgement.

### 3.3 The payback rule (avoid both skipping and bloat)

The two observed failure modes are opposite: agents **skip** the wiki (losing context) or **bloat** it
(noise). Both are solved by one demand-driven rule:

> Create or update an artifact **iff** it pays back: the knowledge/procedure was **actually needed**
> (not speculated) and is **likely to be needed again** or **documents something you just changed**.
> Otherwise do nothing.

Corollaries: no speculative artifacts (SO7); prefer *few, high-value* artifacts; capture what is
*expensive to re-derive*, not what is cheap to re-read. An artifact that is never referenced again is
a liability — avoid creating it, and remove it if it becomes dead weight.

### 3.4 Freshness & single-writer

- **Single-writer (SO3).** Each wiki page / skill / tool is owned by exactly one live node, declared
  in front-matter `owner`. No shared writes. Cross-child concerns are seams owned by the parent
  (SO4).
- **Freshness (SO5).** An artifact declares the source paths it depends on (`sources`). When those
  sources change, the owning node must re-touch the artifact in the same task. A dangling reference
  (to a path/symbol that no longer exists) is a violation. Freshness is what turns the wiki from a
  stale liability into a trusted cache.

### 3.5 Formats

Wiki page front-matter:

```yaml
---
owner: backend                       # exactly one live node id
documents: [src/Api/**]              # what this page is about
sources: [src/Api/Approvals/**]      # change here ⇒ re-touch this page (freshness)
updated: 2026-08-03
---
```

Skill — `skills/<name>/SKILL.md` (Copilot CLI skill convention):

```yaml
---
name: add-api-endpoint
description: Use when adding a controller endpoint to the .NET API.
owner: backend
---
# steps: numbered, deterministic where possible; call tools by name (e.g. `tools/new-migration.ps1`).
```

Tool — a script plus a one-line row in `tools/manifest.md`:

```
| tool | owner | purpose | usage |
| ---- | ----- | ------- | ----- |
| new-migration.ps1 | backend | scaffold + apply an EF migration | pwsh tools/new-migration.ps1 -Name X |
```

### 3.6 Bootstrapping a node on split

When `splitter` splits node `N` into children `C1…Ck` (with `N` retaining seams):

1. **Repartition the bundle.** Move each wiki/skill/tool to the child that now owns its
   `documents`/`sources`. Anything spanning a seam stays with `N` (becomes/remains a `*.contract.md`).
   Re-stamp every `owner`. Single-writer must still hold.
2. **Generate each child's agent-def** from `.github/agents/_node.template.md`, filled with the
   child's charter and an index of its inherited bundle.
3. **Validate coverage** (§2.2) before the commit, as part of the atomic split transaction. A split
   that fails coverage is rejected, not committed.

### 3.7 Self-ownership invariants

- **SO1 Coverage** — every tracked path → exactly one leaf domain.
- **SO2 Bundle presence** — every live node has an agent-def.
- **SO3 Single-writer** — each wiki/skill/tool owned by exactly one live node.
- **SO4 Seam ownership** — every cross-child contract owned by the common parent.
- **SO5 Freshness** — no dangling refs; sources-changed ⇒ artifact re-touched.
- **SO6 No orphan** — every artifact is reachable from an index and owned by a *live* node.
- **SO7 Demand-driven** — no artifact created speculatively; each has a real, recurring use.

The `splitter` enforces SO1–SO4 on every split; nodes uphold SO3–SO7 by discipline as they work.

---

## 4. Meta-agents — responsibilities

- **Host** (`.github/copilot-instructions.md`) — hardcoded `main` entry; gates splits to the human;
  sole writer of `org.json`; commits.
- **main** — root node; owns the repo until the first split; otherwise a normal node.
- **splitter** — executes an approved split: repartition bundles, generate child defs, validate
  coverage, commit atomically, bump `version`.

Nodes (`main` and generated children) share one operating loop: work in an isolated worktree →
maintain the bundle per the payback rule → stay inside their domain → return the result and, if
overloaded, a `SplitProposal`.

---

## 5. Invariants (never violate)

- **Hardcoded entry** — every request starts by invoking `main`.
- **Host disposes, nodes propose** — only the Host mutates `org.json`; nodes only return proposals.
- **Coverage** — `⋃ children.domain ∪ parent.retained == parent.domain`; every path → one leaf.
- **Single-writer** — every wiki page / skill / tool owned by exactly one live node.
- **Seam ownership** — cross-child contracts owned by the common parent.
- **Worktree isolation** — concurrent nodes each work in their own `.worktrees/<id>/`.
- **Deferred, gated, one-way growth** — splits happen after a task, need human approval, never merge
  back.
- **Demand-driven bundle** — no wiki/skill/tool created speculatively; none left stale or orphaned.

---

## 6. What this fixes from the earlier attempt

| Earlier pain point | Fix |
| ------------------ | --- |
| Splitter produced disjoint include/exclude charters | `org.schema.json` + coverage rules the splitter enforces on every split (§2.2, §3.6) |
| Wiki skipped, or bloated | Demand-driven payback rule + freshness & single-writer discipline (§3.3, §3.4) |
| Tools not maintained | Tool manifest + payback rule (§3.2, §3.5) |
| Concurrent agents collided | Worktree-isolation invariant (§2.5) |
| Coverage gaps caught only in one agent | Coverage is a global rule the splitter validates on every split (§2.2) |
