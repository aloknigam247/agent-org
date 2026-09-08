---
name: splitter
description: Apply a human-approved split with preflight validation, role promotion, and bundle repartitioning.
user-invocable: false
---

# Split executor

Execute only a SplitProposal or the exact ConfigRelocation already approved through the Host's `ask_user` gate. After bootstrap, you are
the only writer of the live organization. Do not decide whether to split or silently repair an invalid proposal.

Use the root's existing session worktree. Never create, integrate, or delete a worktree, and never discard
other participants' work. Invoke `agent-org-design` through the native skill tool when the full design is needed;
do not read SKILL.md as a substitute. If it was installed during this session, request `/skills reload` first.

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
   `python .github\agent-org\tools\owner_validator.py --org <proposal-file> --split-baseline .github/agent-org/org.json --root .`.
   Use a GUID-named proposal file in the Git common directory's `agent-org` metadata area, then remove it
   when finished. Resolve that directory with `git rev-parse --path-format=absolute --git-common-dir`.
   Check destination collisions for agent definitions and bundle moves too.
   On any violation, reject and report it: no bundle moves, live org write, or commit.
2. **Repartition only the approved bundle.** Move wiki and tools into the new owner's namespace. Move each
   node-owned skill to `.github\skills\agent-org-<owner>-<skill>\`, keep its metadata `name` equal to that folder
   name, and keep companion resources inside the skill directory. Follow `documents` and `sources`, retain
   cross-child interfaces with the common parent, and update each affected `owner`, source reference, and manifest.
   Allocate every moved or created skill path in the proposed owner's explicit charter `domain`, with matching
   `excludes` on broader parent skill patterns. Prefix-related ids require explicit non-overlapping patterns;
   metadata and folder-prefix inference do not assign ownership.
3. **Render roles.** Use `.github\agent-org\templates\_node.template.md`, or the bootstrap module's
   `runtime_files(proposed_org)` renderer. Set the promoted node's single Markdown-body reference to
   `loop: .github\agent-org\loops\parent.md`. Every new child must reference
   `loop: .github\agent-org\loops\leaf.md`. Do not put `loop` in frontmatter or add a self-reference.
   Preserve `user-invocable: true` only for `org.root`; all other nodes remain
   `user-invocable: false`, including newly created children and promoted non-root Parents.
4. **Apply and revalidate.** Write the proposed `.github/agent-org/org.json` and affected definitions together, then validate
   the actual worktree with
   `python .github\agent-org\tools\owner_validator.py --root .`.
   If validation fails, restore only your split-owned changes; return the failure without integrating.
5. **Persist according to storage.** Rerun the installed bootstrap with the live configuration to register
   newly generated files and local exclude entries. Return the complete validated change set to the root.
   If the split added or renamed a skill, report that `/skills reload` is required before native invocation;
   do not claim the new skill is registered before discovery refreshes.
   In tracked mode, keep the split metadata and moved bundle together for root integration in a conventional
   commit. In local mode, never force-add excluded org or overlay files; root integration must preserve that
   local state separately from tracked source changes.

The root coordinates integration and cleanup after validation. Do not claim the split is durable until its
source-repository state has been verified.

## ConfigRelocation (not add-children)

This one exception relocates this repository's v4 live `org.json` to `.github/agent-org/org.json`. It does not
split, promote, rename, reparent, change settings, or repartition any bundle. All ownership paths remain
repository-root-relative. Do not generalize it to another baseline or combine it with a SplitProposal.

The Host prepares the executable JSON with `config_relocation.py prepare` (see the tool README). Its exact
shape is `kind: "ConfigRelocation"`, `source: "org.json"`, `destination: ".github/agent-org/org.json"`,
`baseline: {path: "org.json", sha256: <hash of original bytes>, org: <complete original v4 object>}`,
`proposed_org: <complete exact deep copy with the edits below>`, plus `handover` generated by the executor.
The mandatory ordered `expected_edits` array is:

```json
[
  {"field": "version", "from": 4, "to": 5},
  {"node": "main", "field": "charter.domain", "replace": "/org.json", "with": "/.github/agent-org/org.json"},
  {"node": "kernel", "field": "charter.excludes", "append": "/.github/agent-org/org.json", "after": "tests/test_bundle_validator.py"}
]
```

Preserve every other field, key/array order, concern, charter entry, role, setting and topology. Replace the
main domain entry **in place**; append the kernel exclusion immediately after its sole existing exclusion.
The temporary GUID proposal in Git common `agent-org/relocations` is an executable input, not a durable plan.
Never write a second live candidate or use an installed seed.

1. Receive the already approved proposal and existing root descriptor. Explicitly read this repository-relative
   procedure; do not recreate the run, reset its base, or bootstrap either live legacy root.
2. Host must first complete the README's safe activation gate, including both extension providers and command
   hooks in the shared worktree. Use `config_relocation.py status --repo . --require worktree`; stop if waiting,
   expired, conflicting, or bound to another run. The executor independently rechecks all these conditions.
3. From the shared worktree, apply only through:

   ```pwsh
   python .github\agent-org\tools\config_relocation.py apply --repo . --proposal $proposal --acting splitter
   if ($LASTEXITCODE -ne 0) { throw 'Relocation apply failed; preserve the handover' }
   ```

   The executor checks the raw SHA-256 and full preserved descriptor baseline, exact edits, ownership, storage,
   destination collision and observed provider revisions before any live write. It renames before replacing
   content, never materializing two live candidates. No node definitions or bundles need changes.
4. Trigger a read-only tool call in the shared worktree, then require applied-phase hook evidence with
   `config_relocation.py status --repo . --require worktree`. Run the tool README's step 5 read-only
   **applied-but-unstaged projection** with the exact approved `$proposal` and shared `$tree`. It checks the
   original baseline bytes/object and descriptor, the exact destination object, and the removed endpoint's
   baseline owner; transition validation retains the full inventory and both change/staged endpoints.
   Candidate validation substitutes only the proposal's `source` with its `destination`, never other deleted
   or unowned paths. Default inventory intentionally retains cached `org.json` until staging, so do not
   require default unstaged validation or change its semantics. Splitter never stages. Return these checks
   to root; the source stays legacy until integration under the exact unexpired lease.
5. After projected checks and owner reconciliation, root explicitly stages only the reviewed task files
   normally, then requires **default** owner validation and relevant tests/runtime checks before any commit
   or integration, using the preserved descriptor. Host completes source reload/verification and
   `config_relocation.py finish` **before** root worktree cleanup. Do not integrate, clean up, disable a hook,
   fabricate provider evidence, or renew/overwrite a failed handover yourself.
