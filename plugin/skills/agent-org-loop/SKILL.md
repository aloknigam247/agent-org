---
name: agent-org-loop
description: The operating loop every agent-org node follows — orient, route or execute, isolate in a worktree, maintain its bundle, and self-check. Read before acting.
---

# Shared node loop

The operating loop every node follows — `main` and every generated child. **Read and follow this
before acting.**

1. **Orient.** Read `org.json`. Confirm your charter (`domain`, `concerns`, `excludes`) and, if you
   are a Parent, your children.
2. **Route or execute.**
   - **Parent** → you **must delegate**: for each subtask, invoke the owning child **as a subagent**
     (the `task` tool with `agent_type: <child-id>`). **Prepend a first line** to the child's prompt,
     exactly `AgentOrgActingNode: <child-id>`, so enforcement can attribute the child's writes to it.
     Aggregate the results; you **never do a child's work yourself** — you write only your shared set /
     seams. Where you can, **plan first**: map each intended change to its owning child and dispatch each
     child only the paths it owns, so cross-domain writes are prevented rather than corrected.
   - **Leaf** → execute directly, but **only inside your `domain`** (see the hard rule below).
3. **Isolate.** Every run, work in your own `.worktrees/<your-id>/<run-id>/` and write nowhere else —
   **always required**, not only when a sibling may run concurrently.
4. **Maintain your bundle — only when it pays back** (org-design §3.2–§3.3):
   - **wiki** knowledge that pays back — follow the `wiki-curate` skill (filter → type → route →
     append); keep `sources` current for freshness.
   - **skill** a procedure repeated ≥ 2× with stable steps.
   - **tool** a mechanical sequence you can script end-to-end; add a row to `tools/<owner>/manifest.md`.
   - Never create artifacts speculatively; never restate what source plainly says.
5. **Report & self-check.** Return the result. Then run the **split self-check**: if your domain-size
   proxy (`--size <your-id>`, org-design §2.3) is over the threshold (~60% of the window), return a
   **SplitProposal** — propose only; never mutate `org.json` yourself.
6. **Reconcile (Parent).** After your children return, read each child's foreign-change log
   `.git/agent-org/foreign/<child-id>.jsonl` (writes it made outside its domain, warn-mode). For each,
   **propose the change to the owning node** — if the owner is in your subtree, dispatch it; if not,
   return the unresolved changes to *your* parent, so they route up to the common ancestor that owns
   both. The owner decides whether to apply it (single-writer holds). Never apply a sibling's file yourself.

**Stay in your domain.** A **containment hook** watches every write. In **warn** mode it lets the write
land (so its content is preserved for reroute) but **logs** it as foreign; in **enforce** mode it blocks
it. Either way, when unsure, confirm first: `python .github/tools/owner_validator.py --owner <path>` — if
the owner is not you, don't write it; surface it so it reaches the owner via your parent (step 6).

## SplitProposal shape

Propose only `add-children`: become a `Parent` by carving ≥ 2 new **leaf** children out of your
domain and keeping a **shared set** for yourself. You propose; the `splitter` executes an approved
proposal.

```yaml
rationale: "why you are overloaded and how this partitions the domain"
children:                          # ≥ 2 new leaves
  - { id: <id>, charter: { domain: [...], concerns: [...], excludes: [...] } }
  - { id: <id>, charter: { domain: [...], concerns: [...], excludes: [...] } }
retained: { domain: [...], excludes: [...] }   # your shared set as Parent (contracts/root/config)
seams: [ "<name>" ]                            # interface artifacts (+ .md overview) you keep; empty if independent
```

Coupling decides the shape, not a different procedure: coupled children → keep their contract as a
seam in `retained`; independent children → `retained` is just shared files and `seams` is empty.
After the split every path you own must go to exactly one of {a child, you-as-Parent's retained}; the
`splitter` re-validates with the owner-oracle (§2.7) before committing.
