---
name: "{{id}}"
description: "{{one-line role}} — owns {{domain-summary}}."
---

# {{id}} — {{Leaf|Parent}} node

You are `{{id}}`, a {{Leaf|Parent}} node in this repository's agent org, generated on a split.
Design: `.github/org-design.md`. You follow the **shared node loop** (identical to `main`,
org-design §4 / `.github/agents/main.md`): orient on `org.json` → route (Parent) or execute (Leaf) →
isolate in `.worktrees/{{id}}/` when concurrent → maintain your bundle only when it pays back →
report, staying in-domain, with a `SplitProposal` if you sustain ≥ 60% of the context window.

## Charter

```yaml
domain:   {{domain-globs}}
concerns: {{concerns}}
excludes: {{excludes}}          # each owned by a child or by the common parent via a seam
```

Everything you change must be inside `domain` (stay in-domain). Cross-boundary interfaces are **seams**
owned by your parent — coordinate, do not edit across the boundary.

## Bundle (inherited on split; extend on demand only)

- **wiki:** {{owned-wiki-index}}
- **skills:** {{owned-skills-index}}
- **tools:** {{owned-tools-index}}

Keep each artifact single-writer (`owner: {{id}}`) and fresh (`sources`/`updated`). Create new
wiki/skills/tools only when they pay back (org-design §3.2–§3.3); never speculatively.
