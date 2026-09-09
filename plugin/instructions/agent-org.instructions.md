---
applyTo: "**"
---

# Host routing

## Applicability

The Host procedure below and routing in the selected scope profile apply to the Host session only. They do
not assign or override your role.

If you are a named org node, `splitter`, or running under another custom-agent definition, or your task prompt
starts with `AgentOrgActingNode:`, do not apply these Host directives and do not invoke `org.root` because of
them. Follow your own agent definition and any explicit `loop` reference instead. The selected collaboration
profile still applies within its stated roles.

## Host procedure

Use Windows and PowerShell. Read `.github/agent-org/org.json` to resolve `root`; route managed work to that named custom agent.
The selected scope and collaboration profiles supply only their respective policies. The Host is domain-less
inside the managed area and gates organizational changes rather than implementing managed feature work.

Start the root task prompt with `AgentOrgActingNode: <org.root>`. Every live node must explicitly read the
repository-relative file named by the `loop:` line in its definition's Markdown body.

Handle kernel governance only when the human explicitly requests it. After bootstrap, only `splitter` may
apply an approved organization change. For each returned SplitProposal or split verdict, the Host MUST use
`ask_user` for approve / edit / reject before proceeding. A split verdict requires human triage: the Host MUST
NOT treat it as advisory, silently defer, or take no action. Require a structured SplitProposal using the shape
in `.github\agents\splitter.md` for the gate; on approval, invoke `splitter` with the root's existing session
worktree and the approved proposal. Never mutate topology merely because a verdict is true.

Invoke `agent-org-design` through the native skill tool when a design question requires it. Do not read SKILL.md
as a substitute for skill invocation. If it was installed during this session, request `/skills reload` first.
