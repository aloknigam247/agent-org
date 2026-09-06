---
name: agent-org-loop
description: Locate the small operating file for an agent-org node's current role.
user-invocable: false
---

# Locate the operating file

Read the `loop` reference in your generated agent definition, then explicitly read that file. It selects
[Leaf](leaf.md) or [Parent](parent.md), each of which reads the single [common core](common.md).

These files are installed under `.github\agent-org\loops`. The full design is an on-demand reference, not
part of the per-task loop.
