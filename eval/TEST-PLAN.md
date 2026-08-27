# agentOrg eval — test plan

How the agent org is verified across its two pillars — **self-ownership** and **agent reliability** —
and its **substrate** (ownership oracle, routing/delegation, splitting, worktree isolation). The
executable self-tests and fixtures are the living source of truth for *what* is covered; this file is
the strategy and the standing gaps.

## Layers

- **L0 — harness correctness** (deterministic). The runner must not manufacture false verdicts. Grades
  are fail-closed; see the harness tests in `eval/test_graders.py`.
- **L1 — ownership oracle** (deterministic). Glob dialect, coverage, tree invariants, containment,
  domain-size, split-transition, CLI contracts — `.github/tools/test_owner_validator.py`.
- **L2 — deterministic validators.** Split transitions (`owner_validator.check_split`) and bundle
  integrity (`.github/tools/bundle_validator.py`, tests `test_bundle_validator.py`); worktree lifecycle
  (`.github/tools/worktree.py`, tests `test_worktree.py`).
- **L3 — agent fixtures** (stochastic, needs model quota). Real `copilot -p` runs graded over N
  repeats; fixtures under `eval/fixtures/`, run via `eval/run.py` (see `eval/README.md`).

Run all deterministic suites (no quota):

```pwsh
python .github/tools/test_owner_validator.py
python .github/tools/test_bundle_validator.py
python .github/tools/test_worktree.py
python eval/test_graders.py
```

## Principles

- **Fail closed.** A crashed, timed-out, or no-op run never passes. Every mutation fixture declares an
  expected effect (`required_paths` / `required_touched_owners` / `expected_no_changes`); a refuse/reject
  case declares `expected_no_changes: true`.
- **Baseline vs final.** Ownership is attributed with the immutable seed `org.json` the agent was given;
  coverage is validated on the run's final `org.json`. An agent cannot grade itself by rewriting ownership.
- **Capture everything.** The change set is the full diff from the baseline commit — committed, staged,
  unstaged, and untracked — NUL- and rename-safe.
- **Fixtures valid and unbiased.** Every seed passes `org.schema.json` and preflight (clean baseline
  coverage, agent exists with a def, bundle integrity, and — for a mutation fixture — `build_cmd` fails
  on the untouched seed). Scenarios are fresh, independent of the design's own examples.
- **Gradeability is explicit.** Each check is a *deterministic gate*, an advisory *trajectory signal*, or
  an advisory *LLM-judge rubric*. Advisory signals never change pass/fail.
- **Deterministic first.** L0–L2 need no quota and gate the meaning of every L3 number; quota is never a
  reason to defer them.

## Invariant traceability (design §5 / §3.7)

| Invariant | Where verified |
| --- | --- |
| Coverage — exactly one owner; parent shared-set never `**` | oracle coverage + tree tests |
| Delegation (scatter-gather) — parent invokes the owning child | trajectory `delegated_to` (L3, quota) |
| Hardcoded entry — Host → `main` | `host-entry` fixture via host-mode (L3, quota) |
| Single-writer / no orphan / freshness (SO3/SO6/SO5) | bundle-integrity tests |
| Worktree isolation + serialized integration (§2.5) | worktree lifecycle tests |
| Growth — valid split; invalid split rejected | split-transition tests + `invalid-split-rejection` |

## Standing gaps

**Quota-gated (logic locked deterministically; only the agent runs remain):**

- Delegation actually happens (`routing-to-child`): parent fires a `task` call to the owning child.
- Leaf stays in domain (`foreign-path-containment`): the leaf refuses a foreign write and surfaces it.
- Splitter rejects an invalid split cleanly (`invalid-split-rejection`).
- Host → `main` entry (`host-entry`, host-mode).
- Re-baseline the behavioral corpus on the hardened harness for trustworthy pass-rates.

**Deferred (no current need):**

- Seam / contract atomicity (SO4) — no seams exist until a coupled split.
- Deep/wide and adversarial routing fixtures — add when a multi-level tree exists.
- Worktree isolation *by the agent* — the substrate is enforced by the runner; agent compliance is an
  L3 concern.
