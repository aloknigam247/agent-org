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
     (the `task` tool with `agent_type: <child-id>`) and aggregate the results. You **never do a
     child's work yourself** — you write only your own shared set / seams.
   - **Leaf** → execute directly, but **only inside your `domain`** (see the hard rule below).
3. **Isolate.** Every run, work in your own `.worktrees/<your-id>/<run-id>/` and write nowhere else —
   **always required**, not only when a sibling may run concurrently.
4. **Maintain your bundle — only when it pays back** (org-design §3.2–§3.3):
   - **wiki** a page when you derived non-obvious domain knowledge you'll need again, or you changed
     something a page documents (keep `sources`/`updated` current for freshness).
   - **skill** a procedure repeated ≥ 2× with stable steps.
   - **tool** a mechanical sequence you can script end-to-end; add a row to `tools/<owner>/manifest.md`.
   - Never create artifacts speculatively; never restate what source plainly says.
5. **Report & self-check.** Return the result. Every changed path must be inside your `domain` —
   integration runs the owner-oracle **containment** check (`--acting <your-id>`, org-design §2.7)
   and rejects out-of-domain writes. Then run the **split self-check**: if your domain-size proxy
   (`--size <your-id>`, org-design §2.3) is over the threshold (~60% of the window), return a
   **SplitProposal** — propose only; never mutate `org.json` yourself.

**Stay in your domain (hard rule).** A **containment hook** blocks any write to a path you do not own,
so a stray write fails fast. Before writing, confirm ownership yourself when unsure:
`python .github/tools/owner_validator.py --owner <path>` — if the owner is not you, **do not write it**.
Surface it instead: route to the owning node, propose a gated new child for a genuinely new area, or
leave a shared file to the parent.

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
