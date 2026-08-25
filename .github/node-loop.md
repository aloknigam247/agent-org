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
3. **Isolate.** If you may run concurrently with a sibling, work in `.worktrees/<your-id>/`.
4. **Maintain your bundle — only when it pays back** (org-design §3.2–§3.3):
   - **wiki** a page when you derived non-obvious domain knowledge you'll need again, or you changed
     something a page documents (keep `sources`/`updated` current for freshness).
   - **skill** a procedure repeated ≥ 2× with stable steps.
   - **tool** a mechanical sequence you can script end-to-end; add a row to `tools/<owner>/manifest.md`.
   - Never create artifacts speculatively; never restate what source plainly says.
5. **Report.** Return the result. Every changed path must be inside your `domain` (stay in-domain). If
   you sustained **≥ 60% of the context window** on your own domain, also return a **SplitProposal**
   (org-design §2.3) — propose only; never mutate `org.json` yourself.

## SplitProposal shape

```yaml
kind: vertical | horizontal | grouping
rationale: "why you are overloaded and how this divides the domain"
children:
  - id: <new-node-id>
    mode: Leaf | Parent
    charter: { domain: [...], concerns: [...], excludes: [...] }
retained: { domain: [...], excludes: [...] }   # what the splitting node keeps (seams live here)
seams: [ "<name>.contract.md" ]                # cross-child contracts the parent will own
```

Coverage must reconstruct your current domain: `⋃ children.domain ∪ retained == your domain`, no gaps
or overlaps. The `splitter` will re-validate this before committing.
