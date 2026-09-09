# agent-org evaluations

The runtime lives in `plugin/`. This directory and `tests/` are development-only; neither is installed in target repos.
`bundle_validator.py` checks wiki/tool namespaces and charter-owned node skills under `.github\skills` for bundle
metadata and freshness during preflight and grading, not at runtime. Unrelated repository skills are ignored.
The installed organization is **only** `.github/agent-org/org.json`. Its location does not rebase charter
globs, managed scope, manifest paths, or bundle `sources`: all remain relative to the repository root.

## Deterministic tests

From the repository root:

```pwsh
python -m pip install -r plugin\tools\requirements.txt -r eval\requirements.txt
python -m pytest -q
```

For just the eval-owned deterministic checks:

```pwsh
python -m pytest -q -p no:cacheprovider eval/test_graders.py tests/test_bundle_validator.py
python eval/bundle_validator.py --root <sandbox-repo>
```

The bundle CLI defaults to `<sandbox-repo>/.github/agent-org/org.json`, independently of the caller's
working directory. An explicit `--org` selects an organization file by absolute path or relative to `--root`,
for example `--org candidates/organization.json`. Other files do not participate in default discovery.

See [TEST-PLAN.md](TEST-PLAN.md) for the scenario matrix and evidence boundaries.

## Agent fixtures

Each `fixtures/<case>/` contains a manifest and an independent `seed/` repository state, including
`seed/.github/agent-org/org.json` and domain files at their repository-root-relative paths. The `seed/` directory
is independent fixture setup.
The runner uses bootstrap's runtime assembly with that explicit fixture org, then overlays the fixture. It does
not run bootstrap on the development checkout or derive defaults from the checkout's evolved live organization.
Agent definitions are generated from the fixture org unless the fixture deliberately overrides one to exercise
a runtime guardrail. For example, `shared-session-routing` retains its independent `director` root with `billing`
and `receiving` children; `partial-unmanaged-write` retains its limited managed scope.

Preflight checks the seed schema, managed coverage, agent definitions, bundle integrity, and outcome preconditions.
Fixtures require a readable `seed/.github/agent-org/org.json`; the runner does not repair missing or malformed
fixture configurations.
Each invocation gets an isolated Copilot home, the canonical Windows command hooks and skills, and one shared
worktree. All descendants receive the same run/worktree context. The harness owns integration and cleanup.

```pwsh
python eval\run.py eval\fixtures\shared-session-routing --repeats 3 --no-judge --keep
```

An explicitly supplied `COPILOT_GITHUB_TOKEN` takes precedence; otherwise the runner uses the active `gh` account.
Pin an available `--model` and `--effort` when comparing runs. Live agent runs consume the selected account's quota.
`--keep` returns the worktree, source repository, and isolated home paths for inspection.

## Manifest fields

| Field | Meaning |
| --- | --- |
| `agent` | Org node to invoke; defaults to the seed root. `host` omits `--agent`. |
| `allowed_paths`, `forbidden_paths` | Permitted and prohibited changed-path patterns. |
| `build_cmd`, `build_timeout` | Objective result assertion and its time limit. |
| `check_role_refs` | Check the single Markdown-body `loop:` reference against the node's final Leaf/Parent role. |
| `expected_delegations` | Expected child calls, reported as advisory observations rather than inferred from files. |
| `expected_denied` | Expected attempted denials: entries with `path`, `owner`, and `acting`. |
| `expected_foreign` | Expected completed warnings with `path`, `owner`, and `acting`; `[]` requires none. |
| `expected_no_changes` | A rejection/refusal must leave no diff; `false` requires an effect. |
| `expected_owner` | Human-labeled allowed owners of changed managed paths; this does not establish authorship. |
| `hook_mode` | Defaults to the runtime's relationship-aware `warn`; `enforce` is an explicit stricter test mode. |
| `id`, `intent`, `unit` | Case identity, prompt, and component under test. |
| `judge` | Optional qualitative rubric; advisory only. Skip it with `--no-judge`. |
| `max_cost`, `max_model_calls`, `timeout` | Optional usage bounds and invocation timeout. |
| `required_behavior` | Human-readable expectations; each needs a corresponding assertion or advisory rubric. |
| `required_paths`, `required_touched_owners` | Required effects, preventing vacuous no-op passes. |

Scope, collaboration, and storage are defined in `seed/.github/agent-org/org.json`, using `plugin/org.schema.json`.
An out-of-scope path is unmanaged, not a coverage error. An unowned path inside scope remains a coverage error.

## Reading results

The changed-path set includes committed and working changes relative to the baseline. Ownership attribution uses
the baseline org; coverage uses the final canonical org. A missing or corrupt `.github/agent-org/org.json` fails
coverage. Runtime configuration must not be rewritten by an agent to hide a gap. Split evaluations also compare
the old and new orgs. Bundle sources that cite the organization must name `.github/agent-org/org.json` explicitly;
all source paths are resolved from the repository root.

Audit assertions use the common Git directory and the current run ID, including the recorded actor and phase.
An attempted warning is not proof that a write completed. Declaring one expected foreign write does not waive
containment for every other path.

`summary.pass_rate` is an observed rate over non-infrastructure runs. Timeouts remain failures. Per-check rates
exclude runs where that check was absent. Trajectory and judge results remain advisory; a correct final file alone
does not prove delegation, successful reconciliation, or interactive extension attachment.

The command-hook runner and deterministic extension-adapter tests cover different boundaries. Neither substitutes
for a live interactive extension test.
