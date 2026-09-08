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
apply an approved organization change. Present each SplitProposal or narrowly scoped ConfigRelocation via
`ask_user` for approve / edit / reject;
on approval, invoke `splitter` with the root's existing session worktree and the approved proposal.

Normal discovery has no legacy fallback. If both live config paths exist, stop: neither wins. For the single
approved v4 -> v5 relocation, follow the bounded activation procedure in
`.github\agent-org\tools\README.md` before invoking splitter. Keep the existing run descriptor; do not bootstrap
the live legacy repository, alter charters yourself, disable hooks, or reload incomplete runtime dependencies.

Invoke `agent-org-design` through the native skill tool when a design question requires it. Do not read SKILL.md
as a substitute for skill invocation. If it was installed during this session, request `/skills reload` first.
