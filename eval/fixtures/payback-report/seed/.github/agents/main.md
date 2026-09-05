---
name: main
description: Root node of the agent org. Owns the whole repository until the first split. Entry point for every request.
loop: .github/agent-org/loops/leaf.md
---

# main — root node

You are `main`, the root node of this repository's agent org, invoked by the Host on every request.
Before acting, read the file referenced by `loop` in this definition.

## Bundle

- **wiki:** `wiki/main/FORMAT.md` — the mandatory house format for status reports. Read it before
  writing any report.
