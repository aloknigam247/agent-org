# Evaluation test plan

Run commands and result semantics are in [README.md](README.md). Fixtures use independent domains rather than the
design's examples. Deterministic failures must be resolved before interpreting agent-run results.

## Required scenarios

| Behavior | Deterministic coverage | Agent scenario |
| --- | --- | --- |
| Bootstrap on an existing repo without overwriting work | `tests/test_bootstrap.py` | Bootstrap skill adoption |
| Full/partial scope combined with agent-only/hybrid collaboration | Bootstrap combinations and `tests/test_runtime_policy.py` | `partial-unmanaged-write` |
| Renamed root and selected instruction profiles | Bootstrap output and command construction | `shared-session-routing`, `host-entry` |
| Parent denied direct or deeper descendant writes | Relationship matrix, multi-file patch checks, denied-audit grader | `parent-descendant-denied` |
| Leaf sibling/cousin/ancestor writes warned and recorded | Relationship matrix, completed-write audit checks | `foreign-write-warn` |
| Unmanaged paths allowed; managed gaps still rejected | Scope and coverage assertions | `partial-unmanaged-write` |
| Stable scope through a split; valid role promotion | Split-transition and role-reference checks | Splitter scenarios |
| One worktree reused by a root and all descendants | `tests/test_worktree.py`, hook context propagation | `shared-session-routing` |
| Parallel root sessions isolated; integration serialized | Worktree lifecycle, lock contention, conflict preservation | Concurrent root runs |
| Hybrid changes between runs detected without reassigning ownership | Source snapshots, added/modified/deleted path checks | Hybrid orientation |
| Eval-only bundle validation never installed | Bootstrap/package assertions; `tests/test_bundle_validator.py` | Fixture preflight |
| Session identity survives shared-worktree execution | Marker parsing, task injection, extension adapter tests | Child tool calls |
| Concurrent same-role sessions never share audit records accidentally | Run/session audit partition tests | Parallel child calls |
| Correct final files do not hide absent delegation | Grader and advisory-trajectory assertions | `routing-to-child`, `shared-session-routing` |

## Evidence boundaries

- **State:** Capture the baseline-to-final diff, including committed, staged, unstaged, untracked, and renamed paths.
  Reject no-op results when the fixture requires changes. Keep baseline ownership separate from final validation.
- **Policy:** Assert both a denied attempt and an unchanged target when testing prevention. Assert successful output
  and a completed audit record when testing warning mode. Foreign records must match the actor, owner, and run.
- **Roles:** Promoting a Leaf changes its agent definition's Markdown-body `loop:` reference to the Parent file;
  generated children reference the Leaf file. Reject frontmatter-only or duplicate pointers. Both role files
  contain the same shared sections, guarded by `tests\test_role_instructions.py`; neither needs a common-file read.
- **Isolation:** A shared worktree separates root sessions, not siblings within one session. The root must avoid
  overlapping sibling writes and wait for children before integration. Do not attribute writers by path.
- **Hybrid:** Compare content to the last reconciled snapshot, not merely `HEAD`; human changes may already be committed.
  A scan reports drift. Only explicit reconciliation/checkpointing accepts the new baseline.
- **Extensions:** Exercise the adapter's callback handling and Python policy bridge without claiming this proves
  registration or event delivery in an interactive Copilot session.
- **Live results:** Retain fixture, model, effort, run identity, outcomes, and errors. A small successful sample is
  evidence for those runs, not a universal reliability guarantee.

## Further integration coverage

Parent reconciliation across a common ancestor, atomic seam changes, and live interactive extension delivery require
their own end-to-end evidence. Unit tests, a logged attempt, or a successful file assertion alone do not establish them.
