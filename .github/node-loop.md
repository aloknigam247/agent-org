# Shared node loop

The operating loop every node follows — `main` and every generated child. **Read and follow this
before acting.** Read on demand by nodes; this file is deliberately not one of the auto-loaded
instruction files, so it never reaches the Host.

1. **Orient.** Read `org.json`. Confirm your charter (`domain`, `concerns`, `excludes`) and, if you
   are a Parent, your children.
2. **Route or execute.**
   - **Parent** → scatter-gather: decompose the task, map each subtask to the child whose charter
     owns it, invoke those child agents, aggregate. Write no domain code yourself; you own only seams.
   - **Leaf** → execute directly within your domain.
3. **Isolate.** Work in your own `.worktrees/<your-id>/<run-id>/` (always safe; required when a
   sibling may run concurrently).
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

If your task needs a path outside your `domain` — one another node owns, or an **UNOWNED** path
(matching no node) — do not write it: surface it for resolution (route to the owner, propose a gated
new child for a genuinely new area, or leave a shared file to the parent).

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
seams: [ "<name>.contract.md" ]                # contracts you keep; empty if children are independent
```

Coupling decides the shape, not a different procedure: coupled children → keep their contract as a
seam in `retained`; independent children → `retained` is just shared files and `seams` is empty.
After the split every path you own must go to exactly one of {a child, you-as-Parent's retained}; the
`splitter` re-validates with the owner-oracle (§2.7) before committing.
