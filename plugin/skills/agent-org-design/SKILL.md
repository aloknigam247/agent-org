---
name: agent-org-design
description: The reference design (the law) for the agent-org kernel — ownership and coverage, growth via gated splits, seams, worktree isolation, self-ownership, and invariants.
---

# Agent-Org design

This is the reference design for the `agent-org` kernel: a self-organizing organization of GitHub
Copilot CLI custom agents that **completely owns** a git repository.

Read this when in doubt. `org.json` is the live state; this document is the law.

---

## 1. Thesis — ownership = coverage + self-sufficiency

A repository is **completely owned by agents** when two properties hold and *stay* true:

- **Coverage (spatial ownership).** Every tracked path is owned by **exactly one node**. Most paths
  are owned by a **leaf**; the shared/contract files common to a parent's children are owned by that
  **parent** (its *shared set*). No orphans, no double-owners.
- **Self-sufficiency (epistemic ownership).** Every node externalizes what it needs to maintain its
  domain without re-deriving it from source each time: a **wiki** (knowledge), **skills**
  (procedures), and **tools** (automation).

These properties are upheld **intrinsically**, with no runtime judge:

- the **owner-oracle** (`owner_validator.py` in the plugin, §2.7) computes `owner(path)` and is run by
  the `splitter` on every split, at every integration gate, and by the containment hook — coverage is
  never eyeballed;
- **nodes** uphold self-sufficiency by discipline — the payback rule, single-writer, and freshness
  (§3).

Out of scope: testing/benchmarking of the agents themselves — to be designed separately.

---

## 2. Substrate

The organizational mechanics. If you know the earlier attempt, skim to §2.2 and §2.5.

### 2.1 Nodes and roles

- **Host** — the Copilot CLI session itself. Domain-less. It hardcodes the entry point to `main`,
  gates splits to the human, and invokes `splitter` to execute them. It never writes feature code.
- **main** — the root node. Owns the whole repo (`**`) until the first split.
- **splitter** — a meta-agent that executes an approved split as one validated, git-committed
  transaction.
- **nodes** — the working agents. Each is a `Parent` or a `Leaf`:
  - **Leaf** implements a task directly within its domain.
  - **Parent** does **scatter-gather**: decompose the task → map subtasks to children by charter →
    invoke child sub-agents → aggregate. A parent writes no domain code itself; it owns only seams.

### 2.2 Charters and coverage

A node's **charter** = `{ domain, concerns, excludes }` (see `org.schema.json`):

- `domain` — glob patterns the node owns (gitignore semantics; §2.7).
- `concerns` — human-readable responsibilities inside the domain.
- `excludes` — globs a node's `domain` would match but that are owned elsewhere (a cross-cutting
  sibling). A node's **effective domain** = `domain` − `excludes`.

Ownership is **one computed function**, `owner(path)` (the oracle, §2.7):

> `owner(path)` = the single node whose **effective domain** matches `path` — a **leaf** for most
> paths, or a **parent** for the shared/contract files common to its children. **Exactly one** node
> must match.

This replaces the earlier "exactly one *leaf*" rule (which contradicted parents owning seams) and the
hand-authored include/exclude bookkeeping that produced the "disjoint charter" bug. **Coverage rules**
(checked by the oracle):

1. **Covered** — every tracked path matches exactly one node's effective domain. **0** matches is
   `UNOWNED`; **> 1** is `overlap`.
2. **Parent shared-set** — a Parent's `domain` is an *explicit shared set* (root/org/contract files
   common to its children), **never** `**`; it does not blanket-own its children's code. A Parent
   therefore needs no big `excludes` list — it simply does not claim what its children own.
3. **Excludes re-covered** — a path one node excludes is owned by exactly one *other* node. (A
   materialized exclude that nothing else covers simply surfaces as `UNOWNED`.)
4. **Tree shape** — acyclic, single-root; `parent`/`children` back-references agree; a `Leaf` has no
   children; a `Parent` has ≥ 2 children.

`UNOWNED` is a **first-class, actionable** state, not a silent gap: a new path matching no node is
resolved by assigning it to an existing child, proposing a gated new child (a new domain), or — only
if it is genuinely shared — the parent. The integration gate (§2.7) runs the oracle on **every**
change, so drift is caught when a file appears, not only at split time.

An illustration (abstract):

- `main` (Parent) — shared set `README.md`, `org.json`, `.github/**`, `wiki/**/*.contract.md`.
- `a` — domain `src/a/**`; excludes `**/*.lock`.
- `b` — domain `src/b/**`.
- `c` — domain `build/**`, `**/*.lock` (a cross-cutting aspect).

`src/a/deps.lock` → `a`'s domain matches but excludes it → **`c`** owns it (a cross-cutting aspect
resolved by one exclude). `src/a/x` → **`a`** only. `README.md` → **`main`**'s shared set only. A new
`src/shared/y` matching no node → **`UNOWNED`** → resolved to a child or a gated new child.

### 2.3 Growth — gated, one-way splits

A node splits **only after** a task, out-of-band, when overloaded. Overload is an **offline
domain-size proxy** measured at the end-of-task self-check — the size of the files the node owns
(owner-oracle `--size`, §2.7), not live context — against an interim **≥ 60% of the 200K-token
window**. *Work is the clock*: the repo changes only during runs and git replicates it, so any run on
any clone computes the same number — no telemetry service, no scheduler. A node only **proposes**; it
never mutates the org. The threshold is interim: a good wiki lets a node work from artifacts rather
than raw source, so raw size over-estimates true load, and the eval harness recalibrates it.

Growth uses a single primitive today — **`add-children`**: the node becomes a `Parent` and gains ≥ 2
new `Leaf` children carved from its domain, retaining a shared set. Whether the split reads as
**vertical** (the parent keeps a seam its coupled children share) or **horizontal** (independent
children, no seam) is a *property of the result decided by coupling*, not a separate procedure — one
code path covers both. Inserting an intermediate Parent to cut fan-out (**`interpose`**) is deferred
until fan-out is a real problem.

Every split is **gated** (human approves/edits/rejects via the Host) and **one-way** (no merge-back).
Executed by the `splitter` as one atomic git commit; `org.json` `version` bumps by one. Git history
**is** the org log.

### 2.4 Seams / contracts

When a domain splits into parts that must agree on an interface — a provider and a consumer sharing
an API/schema, shared types, a message format — that interface is a **seam**, owned by the **common
parent**, never by a child.

A seam is a machine-readable **artifact** (OpenAPI / JSON-schema / proto / shared types), not prose:
the artifact is the **source of truth**, and a `*.contract.md` beside it is only a human overview.
Both sides are checked **against the artifact** so drift surfaces mechanically — in a typed stack the
consumer generates its client from the provider's schema, so a breaking change fails the consumer's
build/type-check (the compiler is the contract test); add an explicit contract test only where the
stack is untyped.

A seam change is a **parent-orchestrated atomic transaction** — no child changes the interface
unilaterally:

1. the parent (seam owner) updates the **artifact**;
2. it delegates the conforming change to **each** affected child;
3. every side's checks must pass;
4. the children **integrate together** (their worktrees bundled under the parent, §2.5), or the whole
   seam change **rolls back** — the parent coordinates the rollback.

### 2.5 Concurrency — worktree isolation

Multiple nodes may execute in parallel; even with disjoint domains they can collide on the working
tree, the index, or build outputs. Each run is isolated in its own git worktree and reaches `main`
only through a single serialized step:

1. **Create** — `git worktree add .worktrees/<node-id>/<run-id>` on a branch off `main` (the `run-id`
   keeps concurrent runs of the same node apart).
2. **Execute** — the node does all its work there, and nowhere else.
3. **Validate** — run the gates in the worktree: the owner-oracle **containment** check over the diff
   (`--acting <node-id>`, §2.7) plus the task's build/tests.
4. **Integrate & clean up** — one role fast-forwards/merges the worktree branch into `main`, **one at
   a time**, then removes the worktree.

**Invariant:** every node run *executes* inside its own worktree and writes nowhere else — **always**,
whether or not a sibling is active; `main` changes only through the
serialized *integration* step, which is the single writer to `main`. Because coverage gives concurrent
nodes disjoint domains (hence disjoint files), serialized integration is near-conflict-free; the rare
seam/exclude overlap is caught by the containment check. This is the hard fix for the "concurrent
agents collided" failure.

### 2.6 Where things live

| Path | What | Written by |
| ---- | ---- | ---------- |
| `org.json` | Live org state (whole tree, charters inlined). | `splitter`, on split. |
| `.github/agents/<id>.md` | Live agent def per node (seeds installed by bootstrap; children generated on split). | bootstrap / splitter. |
| `.github/instructions/agent-org.instructions.md` | Host manual (installed by bootstrap; additive). | bootstrap. |
| `wiki/<node>/**` | Partitioned knowledge; `*.contract.md` seams under the owning parent. | Owning node, on demand. |
| `skills/<node>/**` | Procedures (`<name>/SKILL.md`). | Owning node, on demand. |
| `tools/<node>/**` | Node-owned reusable scripts + `tools/<node>/manifest.md`. | Owning node, on demand. |
| plugin `agents/` `skills/` `tools/` `org.schema.json` | The kernel (source): seed agents, law (this design + the loop), the owner-oracle and validators, schema. Bootstrap copies the tools, seed agents, and Host manual into the repo as a **git-excluded overlay** (`.github/tools/`, `.github/agents/`, `.github/instructions/`). | Humans/kernel (the plugin). |

### 2.7 The owner-oracle and glob semantics

`owner(path)` is computed by a single deterministic tool — the **owner-oracle**
(`.github/tools/owner_validator.py`) — so coverage is never a matter of
judgement:

- **Glob dialect (pinned): gitignore semantics** (via `pathspec`). `dir/**` matches everything under
  `dir`; `**/Name` matches `Name` in any directory; a bare `**` matches everything. Matching is
  **case-sensitive**; **dotfiles are matched** like any other path.
- **Path domain:** the tracked set from `git ls-files` (files only), each normalized to
  forward-slash and repo-root-relative — so the oracle behaves identically on Windows and POSIX.
- **Effective domain** of a node = `domain` minus `excludes`.
- **Verdict:** `ok`, or a list of `{ rule, path|node, evidence }` for `uncovered` (0 owners — i.e.
  `UNOWNED`), `overlap` (> 1 owner), or `tree` (structural). Exit non-zero on any violation.

Both consumers call this one oracle: the `splitter` (pre-commit, over the proposed tree) and the
**integration gate** (over each change's diff, including untracked files). One function, one source of
truth. Kernel tooling ships in the plugin and is copied to `.github/tools/` on bootstrap (git-excluded),
separate from node-owned `tools/<node>/` bundles.

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
  (to a path/symbol that no longer exists) is a violation. The sources-changed ⇒ re-touch check is a
  deterministic **heuristic** — necessary, not sufficient: it proves the artifact was updated, not
  that the update is semantically complete. Freshness is what turns the wiki from a stale liability
  into a trusted cache.

### 3.5 Formats

Every artifact carries `owner` (single-writer, SO3) and `sources` (freshness, SO5) front-matter, and
lives under its owner's namespace so single-writer is a path-prefix fact and orphans are detectable
from the filesystem (SO6).

Wiki page — `wiki/<owner>/<page>.md`:

```yaml
---
owner: a  # exactly one live node id
documents: [src/a/**]  # what this page is about
sources: [src/a/core/**]  # change here ⇒ re-touch this page (freshness)
updated: 2026-08-03
---
```

Skill — `skills/<owner>/<name>/SKILL.md` (Copilot CLI skill convention):

```yaml
---
name: add-widget
description: Use when adding a widget to module a.
owner: a
sources: [src/a/**]  # freshness: change here ⇒ revisit this skill
---
# steps: numbered, deterministic where possible; call tools by name (e.g. `tools/a/scaffold.ps1`).
```

Tool — a script under `tools/<owner>/` plus a row in that node's `tools/<owner>/manifest.md`:

```
| tool | owner | sources | purpose | usage |
| ---- | ----- | ------- | ------- | ----- |
| scaffold.ps1 | a | src/a/** | scaffold a new module under a | pwsh tools/a/scaffold.ps1 -Name X |
```

### 3.6 Bootstrapping a node on split

When `splitter` splits node `N` into children `C1…Ck` (with `N` retaining a shared set), it works in
a disposable worktree and **validates before it mutates**, so a rejected split leaves the repo
untouched:

1. **Pre-validate** the proposed tree with the owner-oracle (§2.7); bad charters are rejected before
   any files move.
2. **Repartition the bundle.** Move each wiki/skill/tool into the namespace of the child that now
   owns its `documents`/`sources` (`wiki/<child>/…`, etc.). A seam spanning children stays with `N`
   as its **artifact** (+ `*.contract.md` overview) under `N`'s namespace. Re-stamp every `owner`;
   single-writer must still hold.
3. **Generate each child's agent-def** from `.github/agents/_node.template.md`.
4. **Write `org.json`** (splitter only) and **re-validate** the result with the oracle; if it fails,
   discard the worktree and commit nothing.
5. **Commit atomically** — one commit for the `org.json` write, the moved bundle, and the new defs.

### 3.7 Self-ownership invariants

- **SO1 Coverage** — every tracked path → exactly one node (a leaf, or a parent for its shared set).
- **SO2 Bundle presence** — every live node has an agent-def.
- **SO3 Single-writer** — each wiki/skill/tool owned by exactly one live node.
- **SO4 Seam ownership** — every cross-child seam is an **artifact** owned by the common parent (the
  source of truth); seam changes are parent-orchestrated and atomic (§2.4).
- **SO5 Freshness** — no dangling refs; sources-changed ⇒ artifact re-touched.
- **SO6 No orphan** — every artifact sits under a live node's namespace and is reachable from the
  derived index (filesystem namespace + link-graph); none owned by a dead node.
- **SO7 Demand-driven** — no artifact created speculatively; each has a real, recurring use.

The `splitter` enforces SO1–SO4 on every split; nodes uphold SO3–SO7 by discipline as they work.

---

## 4. Meta-agents — responsibilities

- **Host** (the `agent-org.instructions.md` Host manual) — hardcoded `main` entry; gates splits to the
  human and invokes `splitter`; never writes `org.json` directly.
- **main** — root node; owns the repo until the first split; otherwise a normal node.
- **splitter** — executes an approved split: repartition bundles, generate child defs, validate
  coverage, commit atomically, bump `version`.

Nodes (`main` and generated children) share one operating loop, the `agent-org-loop` skill: work in an
isolated worktree → maintain the bundle per the payback rule → stay inside their domain → return the
result and, if overloaded, a `SplitProposal`.

---

## 5. Invariants (never violate)

- **Hardcoded entry** — the Host starts every request by invoking `main` (Host → `main`).
- **Delegation (scatter-gather)** — a Parent routes each subtask to the child whose charter owns it and
  invokes that child; it never does a child's work itself. Routing within the org is the parent's job,
  not the Host's. Distinct from the entry invariant above.
- **Host disposes, nodes propose** — nodes never mutate `org.json`; they only return proposals, and
  the `splitter` writes it on an approved split.
- **Coverage** — every tracked path → exactly one node's effective domain (`domain` − `excludes`); a
  Parent owns only its explicit shared set, never `**`.
- **Single-writer** — every wiki page / skill / tool owned by exactly one live node.
- **Seam ownership** — a cross-child seam is an artifact owned by the common parent; it is the source
  of truth, and seam changes are atomic (all sides together, or roll back).
- **Worktree isolation** — **every** node run executes in its own `.worktrees/<id>/<run-id>` and writes
  nowhere else (always, not only under concurrency); `main` changes only via the serialized integration
  step (the single writer to `main`).
- **Deferred, gated, one-way growth** — splits happen after a task, need human approval, never merge
  back.
- **Demand-driven bundle** — no wiki/skill/tool created speculatively; none left stale or orphaned.

---

## 6. What this fixes from the earlier attempt

| Earlier pain point | Fix |
| ------------------ | --- |
| Splitter produced disjoint include/exclude charters | `org.schema.json` + the owner-oracle (§2.7) run by the splitter pre-commit and at every gate (§2.2, §3.6) |
| Wiki skipped, or bloated | Demand-driven payback rule + freshness & single-writer discipline (§3.3, §3.4) |
| Tools not maintained | Tool manifest + payback rule (§3.2, §3.5) |
| Concurrent agents collided | Worktree-isolation invariant (§2.5) |
| Coverage gaps caught only in one agent | Coverage is a global rule the splitter validates on every split (§2.2) |
