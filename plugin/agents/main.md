---
name: main
description: Default root definition before bootstrap renders the configured entry node.
loop: .github/agent-org/loops/leaf.md
---

# main

Read `.github\agents\main.md` to resolve its `loop` reference, then read and follow that file. Copilot does not
automatically load this custom field. Bootstrap renders the live entry definition using `org.json`; the Host
routes to its configured `root`, not this default name.

The full design is an on-demand reference, not a per-task read.
