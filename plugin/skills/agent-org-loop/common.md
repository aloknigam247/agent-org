# Common node core

## Orient and isolate

Read `org.json` and your live charter. Read the installed agent-org instruction profiles if they were not
already supplied. Your node identity comes from `AgentOrgActingNode`, never from a shared worktree pathname.
Follow the selected collaboration profile's orientation step before preparing a plan.

Only the root session creates a worktree, once per task run, before source edits. Use the exact
`AgentOrgRunId` from the current context; if it is missing, return to the Host instead of inventing an id.
Replace the placeholders below with absolute paths and that run id:

```pwsh
python "<source-repo>\.github\agent-org\tools\worktree.py" create --repo "<source-repo>" --session "<run-id>"
```

Preserve the returned `session_id`, `path`, `branch`, `base_sha`, and `base_branch`. A different concurrent root
session must use its own context's session id.
Shell invocations do not preserve variables or `Set-Location`. Supply the absolute worktree path on every
tool call and reassign any shell variables within that call. To recover the descriptor, repeat `create` with
the same run id and either the source repo or shared worktree; it reuses the registered workspace.
Every descendant reuses that exact worktree path from its task prompt; descendants never create, integrate,
or delete worktrees. Stop and return to the parent if that context is missing.

After creation, start task prompts with this context, keeping the root run id and worktree unchanged:

```text
AgentOrgActingNode: <invoked-node-id>
AgentOrgRunId: <root-session-id>
AgentOrgWorktree: <shared-worktree-path>
ParentAgentSessionId: <parent-cli-session-id>
```

Keep these markers explicit even when the task hook supplies bound context. The acting-node marker must be
the first line; a shared pathname never identifies a child.

Prepare a plan before changes. Resolve ownership with
`python .github\agent-org\tools\owner_validator.py --owner <repo-relative-path>`.
Work only in the shared session worktree, without staging or reverting unrelated changes.

## Explicit file-tool boundary policy

| Relationship to the acting node | Policy |
| --- | --- |
| Actor owns the path | ALLOW |
| Owner is a descendant of the actor | DENY in every mode; delegate through the owning direct child |
| Another node owns the path | WARN, log the foreign write, and reconcile with its owner |
| Path is outside the managed scope | ALLOW as unmanaged |
| Managed path has no owner | Coverage problem: UNOWNED; resolve before integration |
| Managed write has no known actor | DENY; establish valid acting-node context before writing |

The warning path preserves evidence; it is not permission to bypass delegation. Check ownership before
writing. These rules cover explicit file-tool paths, not arbitrary shell-script side effects. Hooks and
session markers are operational guardrails, not a security boundary or proof of child authorship.
Shared worktrees isolate root sessions; they do not serialize siblings, indexes, or build outputs.

## Finish

Maintain only bundle artifacts that meet the payback rule in the on-demand design (§3). Use
`.github\agent-org\skills\wiki-curate\SKILL.md` when recording durable knowledge.
Run the task's targeted checks and the owner validator. Report touched paths, validation, and unresolved
foreign changes. Query `owner_validator.py --split-advice` with your node id; return a SplitProposal when
advised, using the shape in `.github\agents\splitter.md`. Do not mutate the organization yourself.

After reconciliation, only the root integrates and cleans up. Use the source repo and run id from the descriptor:

```pwsh
python "<shared-worktree>\.github\agent-org\tools\worktree.py" integrate --repo "<source-repo>" --session "<run-id>"
```

On successful integration, follow the selected collaboration profile's completion step, then:

```pwsh
python "<source-repo>\.github\agent-org\tools\worktree.py" cleanup --repo "<source-repo>" --session "<run-id>"
```

Keep a failed worktree for diagnosis. Use cleanup's explicit `--discard` only when failed work is intentionally
being abandoned; never discard a child's work unilaterally.
