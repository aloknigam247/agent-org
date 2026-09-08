# Agent-org runtime tools

Run these commands in PowerShell from a bootstrapped repository root. Runtime tools live in
`.github\agent-org\tools`; node-owned automation remains separate under `tools\<node>`.
The plugin's `bootstrap` skill documents adoption and configuration.

## Bootstrap

```pwsh
python .github\agent-org\tools\bootstrap.py --help
```

Rerun an initialized target with its existing configuration:

```pwsh
$org = Get-Content -LiteralPath .\.github\agent-org\org.json -Raw | ConvertFrom-Json
$bootstrapArgs = @("--repo", ".", "--root-name", $org.root)
foreach ($field in @("collaboration", "scope", "storage")) {
    if ($null -ne $org.$field) {
        $bootstrapArgs += "--$field"
        $bootstrapArgs += @($org.$field)
    }
}
python .github\agent-org\tools\bootstrap.py @bootstrapArgs
```

Omitted legacy fields retain the CLI defaults instead of being passed as null options.

The copied CLI supports same-configuration reruns and role rendering without the original plugin. The plugin's
`bootstrap` skill defines installed paths, profile selection, and conflict handling.

Python APIs in `bootstrap.py`:

- `bootstrap_repo(repo, *, root_name=None, scope=None, collaboration=None, storage=None, source=None, migrate_legacy=False)`
  preflights every target and reports created/existing files without overwriting files or changing the index.
- `runtime_files(org, source=None)` returns repository-relative paths mapped to bytes, excluding the live
  `.github/agent-org/org.json` and all org seeds. It renders every live node and can render Parent promotion plus
  new Leaf definitions after a split. Fresh defaults come from `default_org()`, never from an evolved target.
- `runtime_paths(org, source=None)` inventories names without reading their contents. Integration combines
  this current inventory with the descriptor's original catalog, preserving known local deletion endpoints.

Both Python APIs accept `source` as an explicit plugin root or installed runtime root. By default, source is
resolved from the script's location, not the working directory or a search of the user's plugin configuration.

An unambiguous legacy target requires explicit `--migrate-legacy`. Migration moves root `org.json` without
changing its bytes or organization version and preserves tracked/local storage. Both live paths are always a
conflict, including equal or malformed files. Differing runtime files are not overwritten. The default legacy
installed seed is removed; a non-default seed remains a collision. Normal runtime has **no** root-path fallback.
For read-only readiness on a still-legacy target, use:

```pwsh
python .github\agent-org\tools\bootstrap.py --repo . --check-runtime --org org.json
```

This compares only runtime assets. It neither bootstraps nor migrates live state. After migration, omit `--org`.

Bootstrap itself needs only Python's standard library and Git. Runtime Python dependencies are in
`requirements.txt`; if an owner-validator command reports a missing dependency:

```pwsh
python -m pip install -r .github\agent-org\tools\requirements.txt
```

## Owner validator

```pwsh
$org = Get-Content -LiteralPath .\.github\agent-org\org.json -Raw | ConvertFrom-Json
python .github\agent-org\tools\owner_validator.py --help
python .github\agent-org\tools\owner_validator.py --root .
python .github\agent-org\tools\owner_validator.py --owner README.md
python .github\agent-org\tools\owner_validator.py --paths README.md src\app.py
python .github\agent-org\tools\owner_validator.py --acting $org.root --root .
python .github\agent-org\tools\owner_validator.py --size $org.root --root .
python .github\agent-org\tools\owner_validator.py --split-advice $org.root --root .
```

Validation reports `ok` or ownership/tree violations and exits nonzero on a failed check. The pure Python APIs
are `managed(org, path)` for scope membership and `ownership(org, path)` for the `overlap`, `owned`, `unmanaged`,
or `unowned` status also returned by `--owner`. `--acting` checks containment of a supplied or changed path set;
a shared worktree diff alone does not identify its author. Do not substitute the root's id for a child's.
`--org <candidate>` is an explicit proposal/testing override, not discovery. All ownership paths and globs
remain repository-root-relative, including when `--org` points into Git metadata.

The selected hybrid instruction profile defines when to run these commands. Their Python counterparts are
`checkpoint(org, root)` and `drift(org, root)`:

```pwsh
python .github\agent-org\tools\owner_validator.py --drift --root .
python .github\agent-org\tools\owner_validator.py --checkpoint --root .
```

Hook configuration comes from `hooks.json`; each role file defines boundary policy and its limitations.

### Native child completion annotation

The existing extension appends completion information to the parent's tool result, not to a SessionEnd
summary. Sync `task` results correlate native `toolTelemetry.restrictedProperties.agent_id` with the
completion event's top-level `agentId`; hook input need not contain `toolCallId`. Background launches are
not final responses; completed `read_agent` results correlate by `toolArgs.agent_id`. Missing identity
leaves the original result untouched and adds an explicit context warning. Child body text and display
names never select a report.

The event handler publishes its report/readiness promise before asynchronous usage recording. A matching
post hook awaits it before asking the existing `split_advice` oracle, using that oracle's defaults. Native
completion totals are authoritative; accumulated usage can be partial, and absent usage is unavailable,
not zero. Tokens are input/output usage, **not billed AI credits**. A split remains a proposal requiring
human approval; this annotation changes neither topology nor charters.

Duplicate project/plugin hooks are idempotent **per chained output**: the next provider preserves the
preceding provider's modified result using its native-event footer suffix. There is no delivery
acknowledgement, so no persistent completion claim or permanent "reported" flag is consumed ahead of
delivery. A fresh raw reread can receive the same current completion once in that output; rereading an
already annotated result does not append again. A new native start, child prompt, or permitted explicit
`write_agent` target invalidates the prior report; async work for that superseded report cannot annotate
the new turn. Session bindings remain immutable until SessionEnd. Old claim files are unused, not migrated.

Worktree tests do not activate these changes in a running provider. After root integration, the Host must
install, perform supported reload, inspect both providers, and verify native sync/background responses
through their actual hook pipeline. Source tests alone are not evidence of live delivery.

## Session worktree

Creation requires a clean source worktree on a named branch with an existing commit. After tracked bootstrap,
review and commit the generated overlay through the repository's normal workflow before starting a session.
Bootstrap does not do this for you. Do not automatically stage, commit, or stash unrelated source changes.

Follow your `.github\agent-org\loops\leaf.md` or `parent.md` for root-only lifecycle and per-call workspace context.

```pwsh
python .github\agent-org\tools\worktree.py --help
```

## Bounded v4 -> v5 activation (Host / splitter / root)

This is the single approved ConfigRelocation described in `.github\agents\splitter.md`, not a general upgrade,
new split, permanent legacy alias, or alternate CLI subprocess. Do not activate partial dependencies.
The Host performs CLI directory/plugin changes; kernel readiness does **not** modify source or plugin caches.

Use the install/resolve/bind/activate order below for **both** readiness and final source replacement.
Await each operation and stop on any error; installation must complete before entry discovery and binding.
Do not use the interactive `/plugin install` during the handover: it refreshes active plugin subsystems,
including extensions, before a subsequent `bind-plugin` tool call can run. There is no deferred-activation
flag in the public `PluginsInstallRequest`; installation and session activation are separate SDK operations.

The public SDK contracts below were checked against CLI 1.0.83's shipped `copilot-sdk/generated/rpc.d.ts`
(`PluginsInstallRequest`, `DiscoveredExtension`, `MetadataSetWorkingDirectoryRequest`,
`SessionPluginsReloadRequest`). `client` means an already-connected SDK client for the **existing Host CLI**
(server RPCs), and `session` means its unbound Host session, not a new CLI subprocess or a named-node session.
`client.rpc.plugins.install` changes installed state; `client.rpc.extensions.discover` reports persisted
extension metadata without launching it. `session.rpc.plugins.reload()` is the activation boundary:
its defaults reload hooks, MCP servers, agents, skills and subprocess extensions (`reloadExtensions: true`).
`session.rpc.extensions.reload()` / `extensions_reload` also activate entries and must never precede binding.
Directory metadata changes leave discovery side effects to the Host; keep normal folder-trust checks intact.
These SDK APIs are experimental: if unavailable, stop for the Host rather than guessing an alternative.

Keep the old project/plugin/command guards healthy until the verified replacement is bound. The existing
Host-owned session activation tool must perform binding and the following directory/refresh operations in
one Host-controlled tool invocation: no guarded tool dispatch may intervene after binding, when the old
loaded entry can cease to match, and before activation of the bound new entry. The PowerShell bind commands
below are that sequence's subprocess steps, not separate agent tool calls followed later by reload.
Kernel readiness does not implement or modify that temporary Host tool. Do not disable/uninstall guards,
auto-trust a provider, edit plugin caches, or add a second migration mechanism to make the sequence work.

1. Quiesce managed work. Preserve the existing ready run descriptor (including base, integrated fields,
   `runtime_files` and `local_files`); do not recreate it. Both source and shared live files must still be the
   exact approved v4 bytes. Finish readiness assets and tests in the shared worktree without bootstrap.
   Check the runtime there with `--check-runtime --org org.json`. Inventory actual running entries with
   `extensions_manage` operation `list`/`inspect` and the CLI `/env`; keep project and plugin providers enabled.
2. Set absolute `$source`, `$tree`, `$run`, `$pluginEntry` (the listed plugin extension entry) and the human-approved
   `$baselineHash`. From PowerShell prepare only the bounded GUID metadata, not a live config or durable plan:

   ```pwsh
   $tool = Join-Path $tree '.github\agent-org\tools\config_relocation.py'
   $prepared = python $tool prepare --repo $source --session $run `
     --baseline-sha256 $baselineHash --plugin-entry $pluginEntry | ConvertFrom-Json
   if ($LASTEXITCODE -ne 0) { throw 'Relocation preparation failed' }
   $proposal = $prepared.proposal
   Get-Content -LiteralPath $proposal -Raw
   ```

   The executable proposal embeds the raw baseline hash, complete v4/v5 objects, exact ordered edit list,
   original descriptor and expected provider fingerprints. It is bound to this Git common directory, source,
   worktree and run, and expires in 24 hours without automatic renewal. Review it against the approved request.
3. **Install and bind readiness before activation.** With the old guards still healthy, use
   `client.rpc.plugins.install({source: "<absolute-shared-worktree>/plugin", workingDirectory: "<absolute-shared-worktree>"})`
   (`server.plugins.install`) to install the complete local readiness package without a session refresh.
   Inspect the returned `plugin` identity and `client.rpc.plugins.list()` (including `directSourceId` when
   present), then `client.rpc.extensions.discover()` (`server.extensions.discover`). Resolve the replacement's
   actual absolute `path` from its enabled plugin extension record to `$pluginEntry`; do not derive a cache
   location from the package name or use the old running session's entry as the new location.
   Require an unambiguous intended plugin identity and the same enabled provider count. A same-name install
   is not proof of replacement: if the manager refuses it, leaves duplicates, or cannot resolve the new entry,
   stop before activation rather than inventing `--force` or uninstalling/disabling providers.
   Verify the complete on-disk entry against the proposal's ready fingerprint, then bind it while old guards
   remain healthy:

   ```pwsh
   python $tool bind-plugin --repo $tree --plugin-entry $pluginEntry
   if ($LASTEXITCODE -ne 0) { throw 'Plugin binding failed; do not activate' }
   ```

   `bind-plugin` independently checks that same ready fingerprint; it does not refresh fingerprints or change
   the approved org edits. Only after it succeeds, in the same Host-controlled sequence, call
   `session.rpc.metadata.setWorkingDirectory({workingDirectory: "<absolute-shared-worktree>"})`,
   then `session.rpc.plugins.reload()` with normal enabled hooks and extensions. This switches project
   discovery away from the still-legacy source and activates the already-bound plugin entry.
   Now use `extensions_manage list` and `inspect` for **both** entries and `/env`: the project and command
   hooks must be rooted at `$tree`, and the plugin must be the verified bound entry. A healthy process alone
   is not sufficient: trigger a harmless native `view` in that worktree and check:

   ```pwsh
   python $tool status --repo $tree --require worktree
   ```

   It must return `ok` with observed **pre and post** evidence from project, plugin and command providers,
   selecting the worktree's leased legacy file. A missing, stale, wrong-root or failed provider blocks apply.
   Re-establish each named node's explicit context after reload; keep input-session precedence unchanged.
   Request `/skills reload` for updated operational skills. Do not switch back to source before integration.
4. Only now invoke splitter with the approved GUID proposal and the existing run/worktree. Splitter runs the
   documented `apply --acting splitter`; no other node moves the live file or edits its charters. Both candidates,
   a changed baseline, different settings/order/topology/roles, wrong exclusion or version jump are rejected.
   Trigger another read-only tool call; require `status --require worktree` again for **applied** evidence.
5. **Validate the applied-but-unstaged projection, then let root stage.** Splitter/root first run this read-only
   check with the existing absolute `$tree` and approved `$proposal`. It uses existing APIs, not a new runtime
   helper or alternate live candidate. `git_tracked()` intentionally retains cached deletions: default owner
   validation can still report legacy `org.json` as unowned before staging.

   ```pwsh
   $env:PYTHONDONTWRITEBYTECODE = '1'
   $env:GIT_OPTIONAL_LOCKS = '0'
   @'
   import json, sys
   from pathlib import Path
   tree, proposal_file = (Path(p).resolve() for p in sys.argv[1:])
   sys.path.insert(0, str(tree / ".github/agent-org/tools"))
   import config_relocation as relocation, org_config as config, owner_validator as ov, worktree as wt
   ready = relocation.status(tree, require="worktree")
   if (ready["status"], ready["phase"]) != ("ok", "applied") or Path(ready["proposal"]).resolve() != proposal_file:
       raise SystemExit("Require the exact active, applied proposal and complete worktree evidence.")
   proposal = json.loads(proposal_file.read_text(encoding="utf-8"))
   expected = config.validate_proposal(proposal)
   old, lease = proposal["baseline"]["org"], proposal["handover"]
   descriptor = ov.git_common_dir(tree) / f"agent-org/runs/{lease['session_id']}.json"
   run = json.loads(descriptor.read_text(encoding="utf-8"))
   original = config.safe_path(Path(lease["repo"]), proposal["source"]).read_bytes()
   candidate = config.config_path(tree)
   new = json.loads(candidate.read_text(encoding="utf-8-sig"))
   if (Path(lease["worktree"]) != tree or not config.same_object(run, lease["descriptor"])
           or config.digest(original) != proposal["baseline"]["sha256"]
           or not config.same_object(json.loads(original), old)
           or candidate != tree / proposal["destination"] or not config.same_object(new, expected)):
       raise SystemExit("Baseline, destination or preserved descriptor differs from the approved proposal.")
   paths = sorted(set(wt._paths(tree, run["base_sha"])) | {proposal["source"], proposal["destination"]})
   checks = {
       "removed_endpoint": ov.check_containment(old, "main", [proposal["source"]]),
       "transition": ov.check_config_relocation(old, new, paths),
   }
   if any(result["status"] != "ok" for result in checks.values()):
       raise SystemExit(json.dumps(checks, indent=2))
   projected = sorted({proposal["destination"] if p == proposal["source"] else p for p in paths})
   checks["candidate"] = ov.validate(new, projected)
   print(json.dumps(checks, indent=2))
   raise SystemExit(0 if checks["candidate"]["status"] == "ok" else 1)
   '@ | python -B - $tree $proposal
   if ($LASTEXITCODE -ne 0) { throw 'Projected relocation validation failed; do not stage' }
   ```

   The unprojected transition inventory includes cached, untracked, changed and staged endpoints, including
   both config paths. The only candidate substitution is the declared `org.json` -> `.github/agent-org/org.json`;
   never filter by file existence or drop other deleted/unowned paths. Baseline containment validates the
   original deletion endpoint; projection is not a waiver of ordinary containment or per-owner reconciliation.
   These read-only checks leave the index, source, descriptor and proposal unchanged. Splitter never stages.

   After projection and reconciliation pass, **root only** sets `$taskFiles` to the explicit reviewed
   repository-relative task paths (including both relocation endpoints in tracked mode) and stages normally.
   Do not blanket-stage unrelated work or force-add local/excluded files. Require **default** owner validation,
   targeted tests and runtime/mirror checks **after staging and before any commit or integration**:

   ```pwsh
   Set-Location -LiteralPath $tree
   if (-not $taskFiles) { throw 'Set the explicit reviewed task file list before staging' }
   git -C $tree add -- @taskFiles
   if ($LASTEXITCODE -ne 0) { throw 'Task staging failed; stop before commit/integration' }
   python -B (Join-Path $tree '.github\agent-org\tools\owner_validator.py') --root $tree
   if ($LASTEXITCODE -ne 0) { throw 'Default worktree validation failed; stop before commit/integration' }
   python -B -m pytest -q -p no:cacheprovider tests/test_config_relocation.py tests/test_role_instructions.py `
     tests/test_bootstrap.py tests/test_worktree.py tests/test_owner_validator.py `
     tests/test_runtime_policy.py tests/test_hook_commands.py tests/test_extension.py
   if ($LASTEXITCODE -ne 0) { throw 'Targeted tests failed; stop before commit/integration' }
   python -B (Join-Path $tree '.github\agent-org\tools\bootstrap.py') --repo $tree --check-runtime
   if ($LASTEXITCODE -ne 0) { throw 'Runtime readiness failed; stop before commit/integration' }
   python (Join-Path $tree '.github\agent-org\tools\worktree.py') integrate --repo $source --session $run
   ```

   An active handover pins runtime fingerprints: a failing runtime check is a Host coordination blocker,
   never a reason to integrate/finish and fix later. Keep the **unchanged** run descriptor.
   During integration the exact source legacy file is permitted only by the lease; the shared worktree is
   canonical. Local integration uses the old descriptor inventory for deletion endpoints and the new runtime
   inventory for additions, without force-adding excluded files. Tracked integration moves config and assets
   together. No temporary source runtime edits are necessary, so the source-clean gate remains intact.
6. **Replace the temporary plugin before final source activation.** After successful integration, keep the
   worktree guards healthy while installing the integrated source package without activation via
   `client.rpc.plugins.install({source: "<absolute-source>/plugin", workingDirectory: "<absolute-source>"})`.
   Inspect its returned identity and `client.rpc.plugins.list()`, then `client.rpc.extensions.discover()`.
   Resolve the actual new absolute entry to `$pluginEntry` and verify the same ready fingerprint and
   unambiguous replacement, just as in step 3. Keep the same enabled provider count; stop on refusal,
   duplicate/stale registrations or unresolved entries. Bind before any source switch or refresh:

   ```pwsh
   python $tool bind-plugin --repo $tree --plugin-entry $pluginEntry
   if ($LASTEXITCODE -ne 0) { throw 'Final plugin binding failed; do not activate' }
   ```

   Only after binding succeeds, within the same Host-controlled sequence, call
   `session.rpc.metadata.setWorkingDirectory({workingDirectory: "<absolute-source>"})`,
   then `session.rpc.plugins.reload()` with normal enabled hooks and extensions. Inspect both providers and
   `/env`, and trigger a read-only source tool call. Require source evidence and owner validation **before**
   retiring the handover:

   ```pwsh
   python $tool status --repo $source --require source
   if ($LASTEXITCODE -ne 0) { throw 'Final source evidence is incomplete' }
   python (Join-Path $source '.github\agent-org\tools\owner_validator.py') --root $source
   if ($LASTEXITCODE -ne 0) { throw 'Source validation failed; preserve the handover' }
   python $tool finish --repo $source
   if ($LASTEXITCODE -ne 0) { throw 'Relocation finish failed; preserve the worktree' }
   python (Join-Path $source '.github\agent-org\tools\worktree.py') cleanup --repo $source --session $run
   ```

   `finish` requires exact canonical state and complete runtime in both roots, source integration recorded on
   the existing descriptor, and final source provider evidence. It removes only its GUID proposal/evidence and
   activation marker. Cleanup is blocked while the handover exists; never use `--discard`. Verify no root
   `org.json`, installed seed, temporary plugin registration, or relocation marker remains.
   Use the unbound Host for the source probe: an existing node's worktree context must not be rebound or erased
   just to produce source evidence.

On any failure or expiry, stop and preserve the descriptor/worktree/proposal for the Host. Never silently
reseed, extend a lease, disable enforcement, edit source/cache by hand, or report an unobserved activation.
