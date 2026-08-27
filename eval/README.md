# agentOrg eval harness

Offline, headless, fixture-based testing of the agent org. Each case gives an agent an **intent**,
runs it in a disposable sandbox, and checks its **output** — response, actions, and resulting repo
state — against a **human-curated manifest** that is independent of `org.json` (so routing checks are
not circular).

Status: **E1–E3** — build/invoke/capture/teardown (E1), deterministic grading (E2), and stochastic
aggregation over N repeats: estimated pass@1, per-check rates, failure modes, cost, a runaway gate, and
split-threshold calibration (E3).

## Layout

```
eval/
  run.py                 # runner; reuses .github/tools/owner_validator as the grader backbone
  requirements.txt       # pyyaml (the oracle needs pathspec, see .github/tools/requirements.txt)
  fixtures/<case>/
    manifest.yml         # the case definition (below)
    seed/                # repo state under test: org.json + domain files (kept small)
```

The runner overlays the kernel (`.github/`, `org.schema.json`) onto the fixture `seed/`, so a fixture
only carries its unique state. The sandbox is a fresh git repo with a single `seed` commit as the
baseline for diffs.

## Manifest

```yaml
id: <case-id>
unit: node | splitter          # what is under test
agent: main                    # agent to invoke (main, splitter, or a node id)
intent: "<the prompt given to the agent>"
expected_owner: [main]         # human-labeled owning node(s) — routing ground truth (not from org.json)
allowed_paths: ["**"]          # regions the change may touch
forbidden_paths: []            # regions it must not touch
required_paths: []             # globs at least one changed path must match (fail closed on a no-op)
required_touched_owners: []    # owners that must appear among the acting node(s) (fail closed on a no-op)
expected_no_changes: null      # true for a refuse/reject case that must change nothing at all
required_behavior: []          # human-readable outcome checks (judge only where a command can't decide)
build_cmd: null                # optional outcome assertion (exit 0 = pass); verifies required_behavior
build_timeout: 60              # seconds; a build that overruns fails (never stalls the run)
judge: null                    # optional advisory LLM-judge question (never folded into pass/fail)
timeout: 240                   # seconds (case wall clock; exceeding it flags a runaway)
max_cost: null                 # optional USD cap; premium-request cost above it flags a runaway
max_model_calls: null          # optional cap on model calls; above it flags a runaway
threshold_override: null       # optional split-threshold fraction (scale down so a small fixture trips a split)
```

Every seed `org.json` must satisfy `org.schema.json` (e.g. `version >= 3`). Before spending an agent
run, the harness **preflights** each fixture: schema-valid seed, clean baseline coverage, the invoked
agent exists with a def, and — for a mutation fixture — `build_cmd` **fails** on the untouched seed (so
a later green build proves the agent did the work, not that the fixture was pre-satisfied). A fixture
that fails preflight is reported as a `fixture_error` and consumes no agent runs (`--no-preflight`
skips this).

Fixtures are **versioned and immutable** — new reality means a new fixture version, never a silent
edit of an existing one.

## Run

```pwsh
pip install -r eval/requirements.txt
python eval/run.py eval/fixtures/smoke-create            # one run
python eval/run.py eval/fixtures/smoke-create --repeats 5 --keep
```

Auth: the harness sets `COPILOT_GITHUB_TOKEN` from `gh auth token`, because a spawned `copilot`
subprocess does not inherit an agent session's managed auth.

## Repeats & summary

`--repeats N` runs the case N times and adds a `summary`: `pass_rate` (estimated pass@1 — a runaway
run never counts as a pass), `per_check_pass_rate`, `failure_modes` (a sample per failing check),
`cost`, `duration_s`, `runaway_count`, and `calibration` pairs (static domain-size proxy vs. actual
input tokens) for recalibrating the split threshold. Pin `--model`/`--effort` for reproducible numbers.

## Trajectory (advisory)

The runner captures the agent's tool-call trajectory from the `--output-format json` stream
(`tool.execution_start`/`tool.execution_complete`) and reports advisory signals that **never change
pass/fail**: per-run `trajectory_analysis` (`tool_calls`, `tools`, `delegated_to`, `ran_oracle`,
`committed`, `oracle_before_commit`, `looped_tools`) and a summary `trajectory` block with the
delegation / oracle-before-commit / loop rates. These reveal *how* an outcome was reached — e.g. a
parent that produces the right file without invoking the owning child. Use `--keep` to retain the raw
per-call trajectory for inspection.

## Judge (advisory, opt-in)

Add a `judge: "<question>"` field to a manifest and the runner asks a strict headless LLM judge that
one question about each run (given the intent, required behavior, response, and changed paths),
recording `{verdict: pass|fail|unsure, rationale}` per run and a verdict tally in the summary. It is
**advisory** — never folded into pass/fail — and used only where a command can't decide a qualitative
point. No `judge` field means no judge call (no cost); `--no-judge` skips it globally.

## Self-tests

Deterministic, offline, no agent calls — fresh scenarios independent of the design examples that pin
the grading backbone:

```pwsh
python .github/tools/test_owner_validator.py   # oracle: glob, coverage, tree, containment, --size, split
python .github/tools/test_bundle_validator.py  # bundle integrity: SO2 presence, single-writer, orphans
python .github/tools/test_worktree.py          # worktree lifecycle: isolation, serialized integration
python eval/test_graders.py                    # grade() + harness: capture, fail-closed, preflight
```

Each exits non-zero if any case fails.
