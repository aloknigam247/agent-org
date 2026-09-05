const marker = /^AgentOrgActingNode:[ \t]*([a-z][a-z0-9-]*)[ \t]*(?:\r?\n|$)/;

export function createRuntime(callOracle, reportError = console.error) {
  const contexts = new Map();
  const names = new Map();
  const tokens = new Map();
  const roots = new Map();

  function payload(input, invocation) {
    const sessionId = invocation?.sessionId ?? input.sessionId;
    return {
      ...input,
      agentOrgContext: contexts.get(sessionId),
      cwd: input.workingDirectory ?? input.cwd,
      sessionId,
    };
  }

  async function toolHook(input, invocation, after) {
    try {
      return await callOracle(after ? "postToolUse" : "preToolUse", payload(input, invocation));
    } catch (error) {
      reportError(`agent-org: ${error.message}`);
      if (after) return { additionalContext: `agent-org audit failed: ${error.message}` };
      return { permissionDecision: "deny", permissionDecisionReason: `agent-org audit failed: ${error.message}` };
    }
  }

  const hooks = {
    onPostToolUse: (input, invocation) => toolHook(input, invocation, true),
    onPreToolUse: (input, invocation) => toolHook(input, invocation, false),
    onSessionEnd: async (input, invocation) => {
      contexts.delete(invocation?.sessionId ?? input.sessionId);
    },
    onUserPromptSubmitted: async (input, invocation) => {
      const value = payload(input, invocation);
      const prompt = input.prompt ?? "";
      const match = marker.exec(prompt);
      if (!match || !value.sessionId) return;
      const previous = contexts.get(value.sessionId);
      if (previous && previous.node !== match[1]) {
        throw new Error("agent-org: a session cannot change its acting node");
      }
      const context = { ...previous, node: match[1], run_id: previous?.run_id ?? value.sessionId };
      for (const [name, key] of [
        ["AgentOrgRunId", "run_id"],
        ["AgentOrgWorktree", "worktree"],
        ["ParentAgentSessionId", "parent_session_id"],
      ]) {
        const field = new RegExp(`^${name}:[ \\t]*(.+?)[ \\t]*\\r?$`, "m").exec(prompt);
        if (field) context[key] = field[1];
      }
      for (const key of ["parent_session_id", "run_id", "worktree"]) {
        if (previous?.[key] !== undefined && previous[key] !== context[key]) {
          throw new Error(`agent-org: a session cannot change its bound ${key}`);
        }
      }
      contexts.set(value.sessionId, context);
      roots.set(context.run_id, value.cwd);
    },
  };

  async function onEvent(event) {
    const id = event.agentId;
    if (!id) return;
    if (event.type === "subagent.started") {
      names.set(id, event.data.agentName);
    } else if (event.type === "assistant.usage") {
      const usage = event.data;
      // inputTokens already includes cached input in the CLI usage total.
      const total = (usage.inputTokens ?? 0) + (usage.outputTokens ?? 0);
      tokens.set(id, (tokens.get(id) ?? 0) + total);
    } else if (event.type === "subagent.completed" || event.type === "subagent.failed") {
      const context = contexts.get(id);
      const cwd = context?.worktree ?? roots.get(context?.run_id) ??
        (roots.size === 1 ? roots.values().next().value : undefined);
      try {
        if (cwd && names.has(id) && tokens.has(id)) {
          await callOracle("usage", { cwd, node: names.get(id), sessionId: id, tokens: tokens.get(id) });
        } else if (tokens.has(id)) {
          reportError(`agent-org: no unambiguous workspace for usage from ${id}`);
        }
      } finally {
        contexts.delete(id);
        names.delete(id);
        tokens.delete(id);
      }
    }
  }

  return { hooks, onEvent };
}
