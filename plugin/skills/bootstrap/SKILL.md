---
name: bootstrap
description: Configure agent-org in an existing Windows Git repository without overwriting files or changing the index.
user-invocable: true
---

# Bootstrap agent-org

Use Windows and PowerShell. Configuration fields and legacy defaults are defined in
[`org.schema.json`](..\..\org.schema.json). The canonical seed is `seed\org.json` inside the plugin;
each target gets its own mutable `org.json`.

## Ask before installing

Use `ask_user` to collect these **independent** choices. Do not conflate human collaboration with partial ownership.

| Question | Choices / input |
| --- | --- |
| Which area should the org manage? | Full repository, or partial with a nonempty list of managed globs |
| Who changes the managed area? | Agents only, or hybrid human + agent collaboration |
| What is the root agent's name? | Ask for an id; default `main` |
| How should the setup be stored? | Local-hidden, or tracked and ready for version control |

For partial scope, obtain the actual globs before continuing. Explain that outside-scope paths stay unmanaged,
whereas a missing owner inside scope remains a coverage problem. For hybrid collaboration, explain that drift
reports changes and owners; it does not automatically reassign ownership.

If `org.json` exists, read its configuration and present those choices instead of resetting the tree. The
bootstrap command rejects conflicting selections. Local mode cannot hide files that Git already tracks.

## Install

1. Resolve this **loaded SKILL.md's location** from its skill metadata. Set `$skillFile` to that absolute path;
   do not guess an installation directory or recursively search the user's home.

   ```pwsh
   $bootstrap = Join-Path (Split-Path -Parent $skillFile) "..\..\tools\bootstrap.py"
   ```

2. Translate the answers to the command's options; `python $bootstrap --help` is the CLI reference.
   Set `$rootName`, `$managedGlobs`, `$collaboration`, and `$storage` from the answers, then run:

   ```pwsh
   python $bootstrap --repo (Get-Location).Path --root-name $rootName --scope $managedGlobs `
     --collaboration $collaboration --storage $storage
   ```

   This preflights all destination collisions, preserves matching files, and reports `created` versus `existing`.
   Generated definitions and installed operational skills follow the invocation policy in `agent-org-design`.
   Stop on a conflict; never overwrite a file, hand-edit the live tree to force a pass, or stage unrelated work.
   No Git initialization, commit, package install, or global Git/Copilot configuration change is performed.

3. Validate the installed tree:

   ```pwsh
   python .github\agent-org\tools\owner_validator.py --root . --org org.json
   ```

   Only if this fails because a Python dependency is missing, install the runtime requirements and retry:

   ```pwsh
   python -m pip install -r .github\agent-org\tools\requirements.txt
   ```

4. Report the chosen configuration, created/existing files, exclude-file location, and actual validation result.
   Surface coverage failures without silently reassigning paths. Request `/skills reload` to discover skills added
   during this session, and do not claim they are registered before that refresh. Start a new session to load the
   installed agents, hooks, and extension; verify their behavior rather than claiming they intercept every kind of
   write. Before the first task, follow the [worktree prerequisites](..\..\tools\README.md#session-worktree).

## Installed layout

| Target path | Contents |
| --- | --- |
| `org.json` | Mutable target configuration and live tree |
| `.github\agent-org\` | Runtime tools, self-contained Parent/Leaf loops, schema, seed, and template |
| `.github\agents\` | Definitions for live nodes and `splitter`; unrelated agents are preserved |
| `.github\extensions\agent-org\` | Extension and its runtime helper modules |
| `.github\hooks\agent-org.json` | Copy of the canonical plugin hooks |
| `.github\instructions\` | Base policy plus only the selected scope and collaboration profiles |
| `.github\skills\` | Operational `agent-org-design` and `agent-org-wiki-curate` skills; unrelated skills are preserved |

The renderer always copies `agent-org.instructions.md`, then selects `agent-org.scope-full.instructions.md`
for scope `["**"]` or `agent-org.scope-partial.instructions.md` otherwise. The `collaboration` choice selects
`agent-org.collaboration-agents.instructions.md` or `agent-org.collaboration-hybrid.instructions.md`.

Local mode adds exact owned files to the real Git common directory's `info\exclude`, including when `.git`
is a linked-worktree file. It does not hide whole shared agent or instruction directories. Tracked mode adds
no overlay exclusions. Both modes exclude runtime worktrees and caches; neither changes the index.

The installed bootstrap tooling can reuse its copied seed, template, tools, operational skills, and **selected**
profiles, including rendering new nodes after a split. It does not depend on a copied bootstrap SKILL.md. To
bootstrap with a different profile selection, use the original plugin's bootstrap skill. Unselected profiles and
evaluation code are not copied into targets.
