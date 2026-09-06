---
applyTo: "**"
---

# Human and agent collaboration

At root orientation, before planning, run in the source repository:

```pwsh
python "<source-repo>\.github\agent-org\tools\owner_validator.py" --drift --root "<source-repo>"
```

Inspect between-run changes and the returned owners. Route reconciliation and stale knowledge to those owners.
Drift does not reassign ownership; an in-scope gap needs an explicit, approved organization change.

After child reconciliation and successful integration, the root Parent checkpoints the source repository.
An unsplit root Leaf performs the same completion step. Do not checkpoint unfinished sibling work or a failed run.

```pwsh
python "<source-repo>\.github\agent-org\tools\owner_validator.py" --checkpoint --root "<source-repo>"
```

Replace `<source-repo>` with the source path from the session descriptor on every call; shell state is not persistent.
