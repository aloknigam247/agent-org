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

Omitted optional fields retain the CLI defaults instead of being passed as null options.

The copied CLI supports same-configuration reruns and role rendering without the original plugin. The plugin's
`bootstrap` skill defines installed paths, profile selection, and conflict handling.

Python APIs in `bootstrap.py`:

- `bootstrap_repo(repo, *, root_name=None, scope=None, collaboration=None, storage=None, source=None)`
  preflights every target and reports created/existing files without overwriting files or changing the index.
- `runtime_files(org, source=None)` returns repository-relative paths mapped to bytes, excluding the live
  `.github/agent-org/org.json`. It renders every live node and can render Parent promotion plus new Leaf
  definitions after a split. Fresh defaults come from `default_org()`, never from an evolved target.
- `runtime_paths(org, source=None)` inventories names without reading their contents. Integration combines
  this current inventory with the descriptor's original catalog, preserving known local deletion endpoints.

These Python APIs accept `source` as an explicit plugin root or installed runtime root. By default, source is
resolved from the script's location, not the working directory or a search of the user's plugin configuration.

The sole live configuration is `.github/agent-org/org.json`. Its integer `version` is a per-org evolution
counter, not a file-layout version: fresh defaults start at 3, and every applied split increments it once.
Existing integer versions have no minimum. Read-only runtime readiness uses the canonical configuration:

```pwsh
python .github\agent-org\tools\bootstrap.py --repo . --check-runtime
```

This compares only runtime assets; it does not bootstrap or change live state. An explicit `--org <candidate>`
selects a proposal for this read-only check, not an alternate discovery location.

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
peak context is the session's maximum observed `inputTokens`, including across reused child turns.
Cumulative CLI `totalTokens`, output tokens, and separate cache counters never contribute. Missing input
samples make observations partial; no observed input is unavailable, not zero. Peak occupancy is
**not billed AI credits**. A true `recommend_split` is a verdict requiring human triage, not optional advice:
return a structured SplitProposal through the Host's `ask_user` approve/edit/reject gate for human approval.
This annotation changes neither topology nor charters.

Usage recording requires `--usage-record <node> --session <native-session-id> --tokens <peak-input-tokens>`;
use `--partial` for incomplete input observations. Both providers resolve the same Git common directory.
An exclusive per-session usage lock and atomic replacement maintain exactly one record at
`agent-org/usage/<node>/<session>.json`; duplicate reports do not rewrite it, while later higher peaks update
that same record. Recording failures are surfaced, not silently discarded. The existing `<node>.jsonl` logs
are never rewritten: split advice still includes their unverified legacy evidence and flags its presence.
`peak_session_tokens` is the maximum recorded session peak (or null if unavailable); `domain_est_tokens`
OR that peak reaching `--threshold * --window` returns a split verdict. Legacy cumulative evidence may
inflate the signal and must be discussed at human triage.

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
