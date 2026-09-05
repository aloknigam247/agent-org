---
name: agent-org-design
description: On-demand reference for organization growth, managed ownership, seams, and durable node knowledge.
---

# Agent-org design reference

Read this when a design question requires it, not before every task. `org.json` is live per-target state.
The operating rules live in the small files selected by each generated definition's `loop` reference.

## 1. Ownership

An agent org maintains two properties over its managed code area:

- **Coverage:** paths have an unambiguous owner, with shared interfaces retained by the appropriate parent.
- **Self-sufficiency:** owners retain the knowledge, procedures, and automation needed to maintain their domains.

The owner validator computes coverage. Self-sufficiency also depends on node discipline; a written instruction
or helper is not proof that an agent follows it.

## 2. Organization

### 2.1 Nodes and roles

The Host reads the entry id from `org.root`, routes managed work, and gates growth. A Leaf executes within its
charter; a Parent partitions work among its direct children and owns its retained shared set. `splitter` is a
meta-agent for approved organization changes, not a domain owner.

See the installed Host instructions and `.github\agent-org\loops` for the operating rules. The agent must
explicitly read its `loop` file; a custom frontmatter field does not cause Copilot to load it.

### 2.2 Charters and managed scope

[`org.schema.json`](..\..\org.schema.json) defines configuration and charter fields, including legacy defaults.
The stable managed scope is independent of a node's effective domain: `domain` minus `excludes`.
Splitting the root does not reduce the managed area to its retained files.

The owner validator reports `UNOWNED` for a gap inside that area and `overlap` for multiple owners. An
outside-scope path is unmanaged rather than a gap. Resolve managed gaps with an explicit approved organization
change, not by treating an uncovered file as permission to write or by silently widening a charter.

Tree validation checks a single acyclic root, matching parent/child references, and the role constraints in
the schema. A Parent retains a shared set rather than blanket-owning descendant implementation.

### 2.3 Growth

Nodes request growth after a task; the human gates it through the Host. The supported proposal and execution
procedure live in `.github\agents\splitter.md`. Splits are one-way; reparenting and merge-back are not supported.

Use the owner validator's `--split-advice` result for overload evidence. Its `--help` documents the configurable
window and threshold; do not duplicate those numerical defaults in instructions. Domain size is a proxy, not
a measurement of all knowledge an agent needs. Usage records provide additional evidence when available.

### 2.4 Seams and contracts

A cross-child interface is a parent-owned **artifact**: a schema, shared type, protocol definition, or other
machine-readable contract. A neighboring `*.contract.md` is an overview, not a second source of truth.

The parent changes its artifact and delegates conforming implementation to each owning child in the shared
session worktree. Every affected side's checks must pass before root integration. Prefer the existing typed
build or generated-client check; add a dedicated contract test only when the stack needs one.

If a seam change cannot be completed, coordinate reversal of the affected owners' work. Do not unilaterally
reset a shared worktree that contains siblings' changes.

### 2.5 Session worktrees

The root lifecycle and descendant context protocol are defined once in the common operating core under
`.github\agent-org\loops`. The [tool reference](..\..\tools\README.md) gives runnable lifecycle commands.

This provides isolation between root sessions. It is not proof of which child wrote a change, serialization
among siblings, or enforcement of arbitrary shell writes.

### 2.6 Bootstrap and storage

The [bootstrap skill](..\bootstrap\SKILL.md) owns the adoption procedure and installed layout. The canonical
plugin seed is used only to initialize a target; it is not a shared mutable organization.

Agent discovery and instruction discovery remain in their normal repository directories. Runtime machinery,
role files, and templates are separate from node-owned bundles. Local storage must not force excluded state
into commits; tracked storage makes the overlay available for ordinary version control.

### 2.7 Owner validation

The runtime owner validator is the single ownership implementation used by hooks, split checks, and integration.
Its pathspec globs use gitignore semantics, including exclusions. Matching is case-sensitive and includes
dotfiles. Git paths are normalized to repository-relative slash form as data; commands use Windows paths.

Validate repository coverage and the actual changed-path set, including new files, rather than trusting only a
child's reported file list. See the [tool reference](..\..\tools\README.md) for commands and exit behavior.

### 2.8 Boundaries and reconciliation

The relationship-based allow/deny/warn policy is defined in the common operating core. Hook and extension
records aid reconciliation; they do not replace review of actual changes. The Parent loop specifies owner
routing, and the selected collaboration profile supplies any between-run drift and checkpoint steps.

## 3. Durable node bundles

### 3.1 Contents and ownership

Every live node has an agent definition and can maintain artifacts under its own namespaces:

- `wiki\<owner>\` for durable knowledge.
- `skills\<owner>\` for reusable procedures.
- `tools\<owner>\` for mechanical automation and its manifest.

Create artifacts on demand, not empty directories or speculative indexes at bootstrap. Definitions point to
the live charter rather than duplicating mutable ownership data.

### 3.2 Payback and artifact choice

Create or update an artifact only when its knowledge or procedure was needed and is likely to be needed again,
or when it documents something just changed. Drop facts cheap to re-read from source.

| Artifact | Trigger | Avoid |
| --- | --- | --- |
| Skill | A recurring procedure has stable steps | One-off instructions or reimplementing a tool in prose |
| Tool | A recurring mechanical sequence can be scripted end-to-end | Automating judgment or wrapping one trivial command |
| Wiki | Non-obvious knowledge was expensive to derive and will be useful again | Source paraphrases, transcripts, or duplicate pages |

### 3.3 Wiki maintenance

Follow [wiki-curate](..\wiki-curate\SKILL.md) when recording a candidate note. Prefer few useful pages; filter,
type, and route an entry before appending it. Consolidate a noisy page when needed rather than preserving a
lossless transcript. Remove dead artifacts instead of keeping an unused cache.

### 3.4 Single-writer and freshness

An artifact belongs to one live node and declares `owner` and `sources`. When a referenced source changes,
its owner revisits the artifact in the same task. Keep references resolvable and route cross-child knowledge
to a common-parent seam rather than sharing write ownership.

A freshness check can show that a file was revisited; it cannot prove its meaning is complete or correct.

### 3.5 Formats

A wiki page uses frontmatter such as:

```yaml
---
owner: catalog
documents: [src/catalog/**]
sources: [src/catalog/schema.json]
---
```

A skill lives at `skills\<owner>\<name>\SKILL.md`:

```yaml
---
name: add-catalog-item
description: Use when adding a catalog item.
owner: catalog
sources: [src/catalog/schema.json]
---
```

A tool belongs under `tools\<owner>\` with a row in that owner's `manifest.md`:

| tool | owner | sources | purpose | usage |
| --- | --- | --- | --- | --- |
| scaffold.ps1 | catalog | src/catalog/schema.json | scaffold a catalog item | pwsh tools\catalog\scaffold.ps1 -Name Example |

### 3.6 Bundle partition on split

The splitter procedure owns repartitioning and role-reference updates. Move an artifact according to the
code it documents; update ownership and source references together. Shared contracts stay with their common
parent. Keep the split's metadata and bundle changes consistent with the selected storage mode.

### 3.7 Validation limits

Runtime validation covers organizational structure and managed ownership. Nodes must still maintain bundle
ownership, source freshness, and useful content. Bundle-integrity checks belong to the repository's evaluation
infrastructure, not the installed runtime; do not claim they are running on every task.
