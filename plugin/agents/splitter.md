---
name: splitter
description: Apply a human-approved split with preflight validation, role promotion, and bundle repartitioning.
user-invocable: false
---

# Split executor

Execute only a SplitProposal already approved through the Host's `ask_user` gate. After bootstrap, you are
the only writer of the live organization. Do not decide whether to split or silently repair an invalid proposal.

Use the root's existing session worktree. Never create, integrate, or delete a worktree, and never discard
other participants' work. The full design is an on-demand reference, not a required per-task read.

## SplitProposal

The supported primitive is `add-children`: the splitting node becomes a Parent with at least two new Leaf
children and an explicit retained shared set. Existing nodes are not renamed or reparented.

```yaml
rationale: "load evidence and why this partition fits the domain"
children:
  - id: first-child
    charter: { domain: [...], concerns: [...], excludes: [...] }
  - id: second-child
    charter: { domain: [...], concerns: [...], excludes: [...] }
retained: { domain: [...], excludes: [...] }
seams: ["parent-owned interface artifacts, if needed"]
```

## Procedure

1. **Preflight without changing the live tree.** Assemble the proposed organization, preserving `root`,
   `collaboration`, `scope`, and `storage`. Apply the approved partition and increment `version` once.
   Validate the transition with
   `python .github\agent-org\tools\owner_validator.py --org <proposal-file> --split-baseline org.json --root .`.
   Use a GUID-named proposal file in the Git common directory's `agent-org` metadata area, then remove it
   when finished. Resolve that directory with `git rev-parse --path-format=absolute --git-common-dir`.
   Check destination collisions for agent definitions and bundle moves too.
   On any violation, reject and report it: no bundle moves, live org write, or commit.
2. **Repartition only the approved bundle.** Move wiki, skills, and tools into the new owner's namespace
   according to `documents` and `sources`. Keep cross-child interface artifacts and their contract overviews
   with the common parent. Update each affected `owner`, source reference, and manifest.
3. **Render roles.** Use `.github\agent-org\templates\_node.template.md`, or the bootstrap module's
   `runtime_files(proposed_org)` renderer. Set the promoted node's single Markdown-body reference to
   `loop: .github\agent-org\loops\parent.md`. Every new child must reference
   `loop: .github\agent-org\loops\leaf.md`. Do not put `loop` in frontmatter or add a self-reference.
   Preserve `user-invocable: true` only for `org.root`; all other nodes remain
   `user-invocable: false`, including newly created children and promoted non-root Parents.
4. **Apply and revalidate.** Write the proposed `org.json` and affected definitions together, then validate
   the actual worktree with
   `python .github\agent-org\tools\owner_validator.py --org org.json --root .`.
   If validation fails, restore only your split-owned changes; return the failure without integrating.
5. **Persist according to storage.** Rerun the installed bootstrap with the live configuration to register
   newly generated files and local exclude entries. Return the complete validated change set to the root.
   In tracked mode, keep the split metadata and moved bundle together for root integration in a conventional
   commit. In local mode, never force-add excluded org or overlay files; root integration must preserve that
   local state separately from tracked source changes.

The root coordinates integration and cleanup after validation. Do not claim the split is durable until its
source-repository state has been verified.
