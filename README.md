# agent-org

A self-organizing tree of GitHub Copilot CLI agents for a configurable area of an existing Git repository.
Agents partition work, maintain useful knowledge and automation, and propose human-approved growth.

This plugin supports **Windows and PowerShell**. Managed scope, human collaboration, root name, and local or
tracked storage are configured independently at bootstrap.

## Install and adopt

```pwsh
copilot plugin install aloknigam247/agent-org:plugin
```

Start a new session and confirm installation with `copilot plugin list`. In the target repository, ask
Copilot to run the agent-org **bootstrap** skill. It asks for your choices, checks for collisions, installs only
the selected instruction profiles, and reports validation. It does not initialize Git, overwrite user files,
stage changes, or modify global settings.

Follow the [bootstrap skill](plugin\skills\bootstrap\SKILL.md) for the complete setup procedure and installed
layout. Configuration and compatibility defaults are defined in the [schema](plugin\org.schema.json).

## Use

The Host resolves the configured root from the target's `org.json`. Each node explicitly reads a small Leaf or
Parent operating file through a `loop:` reference in its definition's Markdown body. Each role file is
self-contained. The [full design](plugin\skills\agent-org-design\SKILL.md) is an on-demand reference.

The root creates one session worktree and passes its path to all descendants. Parents delegate descendant-owned
implementation and reconcile foreign changes before root integration. See the
[Leaf](plugin\loops\leaf.md) and [Parent](plugin\loops\parent.md) operating files and
[runnable tool commands](plugin\tools\README.md).

Hooks classify explicit file-tool paths; they are not a security boundary for arbitrary shell writes. Shared
worktrees isolate root sessions, not sibling operations, and do not prove child authorship. Runtime helpers and
written rules must be distinguished from verified behavior in the actual Copilot execution mode.

## Repository

- `plugin\` is the installable kernel; `plugin\seed\org.json` is its canonical bootstrap seed, not live shared state.
- `eval\` and `tests\` contain development validation, including the evaluation-only bundle validator.

See the [evaluation guide](eval\README.md) and [test plan](eval\TEST-PLAN.md) for verification scope.
