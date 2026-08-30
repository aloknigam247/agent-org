---
name: wiki-curate
description: The disciplined path for recording durable knowledge into a node's wiki — filter by payback, type it, route it to the right page, append a compact entry. Keeps the wiki few and high-value.
---

# Wiki-curate

How a node records knowledge into its wiki without bloating it. Follow this at the node loop's
"maintain your bundle" step whenever you have a candidate note. The rule is **filter first**: a wiki is
a trusted cache of what is *expensive to re-derive*, not a transcript.

## Per note

1. **Payback gate.** Keep the note only if it was *actually needed* this task **and** is *likely needed
   again* **or** *documents something you just changed*. Drop anything cheap to re-read from source, or
   speculative. When in doubt, drop it.

2. **Type it** — one of:
   - `decision` — a choice made, the *why*, and the rejected alternative, when reversing it is costly.
   - `gotcha` — a non-obvious trap that cost you time and would cost a weaker model again.
   - `arch` — how the domain is structured, or a load-bearing invariant.
   - `how-it-works` — a non-obvious mechanism you had to derive from source.

3. **Route it** to a page under your namespace `wiki/<you>/`:
   - `decision` → `DECISIONS.md` · `gotcha` → `GOTCHAS.md` · `arch`/`how-it-works` → `OVERVIEW.md` or a
     topic page. These names are **conventions, not obligations** — create a page only when it pays back.

4. **Append a compact entry** — one claim, the *why*, and the `sources` it depends on:
   ```
   - <claim in one line>. Why: <the non-obvious reason>. (sources: src/x, src/y)
   ```
   Merge into the page's relevant section; do not restate what an existing entry already says. Keep the
   page front-matter fresh so the freshness check protects it:
   ```yaml
   ---
   owner: <you>            # single-writer (SO3)
   sources: [src/x, ...]   # freshness (SO5): the union of the entries' sources
   ---
   ```

## Consolidation (occasional, not per-note)

When a page has grown noisy, or you added a large braindump at once, run one consolidation pass over
*that page*: split it into atomic claims, drop duplicates and anything that no longer pays back (prune
by **judgement** — there is no usage counter), re-type, and merge related claims into tight sections.
This is the only place to "digest" in bulk; the per-note path above stays lightweight.

## Never

- Preserve losslessly, or rank every fact — that manufactures the noise §3.3 exists to prevent.
- Duplicate another node's page (single-writer); write across a boundary — surface a seam instead.
- Leave `sources` stale — a changed source must re-touch its page in the same task (freshness, SO5).
