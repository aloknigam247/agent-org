# Parent operation

Read and follow [the common core](common.md) first.

1. **Plan before changes.** Resolve intended paths to owners, then partition the plan by the owning **direct
   child**, even when the final owner is deeper in that child's subtree. Keep only your retained shared set
   and seams for yourself. A parent must never perform its descendants' implementation work.
2. **Delegate.** Invoke each owning child as a custom subagent. Pass its scoped plan, checks, and the exact
   root session worktree. Use the common core's prompt context with the direct child's acting id; set
   `ParentAgentSessionId` to your own CLI session id. Require the child to work at that path and repeat the
   context when it delegates. Coordinate potentially conflicting sibling operations; the shared worktree
   does not provide sibling serialization.
3. **Reconcile.** After children return, read the session audit with
   `python .github\agent-org\tools\owner_validator.py --foreign --root <shared-worktree> --session <run-id>`.
   Inspect the actual session diff as well as child reports; neither a shared diff nor markers alone proves
   authorship. Send foreign changes to the owner for acceptance, repair, or reversal. Route through your
   direct child if that owner is in its subtree; otherwise return unresolved work to your own parent.
   Never repair a descendant's files yourself.
4. **Validate and report.** Require the owning children to pass their checks and resolve in-scope coverage
   gaps. Check each child's `owner_validator.py --split-advice <child-id>` result and propose growth on its
   behalf when needed. Return aggregate results and unresolved work upward. Only the root performs the
   common core's integration and selected collaboration profile's checkpoint, after reconciliation.
