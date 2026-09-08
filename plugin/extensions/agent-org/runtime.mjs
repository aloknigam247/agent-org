const marker = /^AgentOrgActingNode:[ \t]*([a-z][a-z0-9-]*)[ \t]*(?:\r?\n|$)/;

export function createRuntime(callOracle, reportError = console.error) {
  const contexts = new Map();
  const names = new Map();
  const tokens = new Map();
  const roots = new Map();
  // Parent-facing child completion reports, keyed by BOTH the spawning parent tool-call id (sync `task`)
  // and the child agentId (background `read_agent`). Bounded: entries are consumed/evicted on report.
  const reports = new Map();
  const reported = new Set();

  function payload(input, invocation) {
    const sessionId = input.sessionId ?? invocation?.sessionId;
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
    onPostToolUse: async (input, invocation) => {
      const base = await toolHook(input, invocation, true);
      const injection = await injectCompletion(payload(input, invocation));
      return injection ? { ...base, ...injection } : base;
    },
    onPreToolUse: (input, invocation) => toolHook(input, invocation, false),
    onSessionEnd: async (input, invocation) => {
      contexts.delete(input.sessionId ?? invocation?.sessionId);
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
      const data = event.data ?? {};
      try {
        if (cwd && names.has(id) && tokens.has(id)) {
          await callOracle("usage", { cwd, node: names.get(id), sessionId: id, tokens: tokens.get(id) });
        } else if (tokens.has(id)) {
          reportError(`agent-org: no unambiguous workspace for usage from ${id}`);
        }
        // Authoritative total: prefer the event's totalTokens, fall back to accumulated usage, else unknown.
        const total = data.totalTokens ?? (tokens.has(id) ? tokens.get(id) : undefined);
        const report = {
          agentId: id,
          node: data.agentName ?? names.get(id),
          tokens: total,
          durationMs: data.durationMs,
          model: data.model,
          failed: event.type === "subagent.failed",
          cancelled: data.cancelled === true,
          error: data.error,
          cwd,
        };
        reports.set(id, report);
        if (data.toolCallId) reports.set(data.toolCallId, report);
      } finally {
        contexts.delete(id);
        names.delete(id);
        tokens.delete(id);
      }
    }
  }

  function completionFooter(report, advice) {
    const lines = ["[agent-org] Authoritative child completion (appended by org runtime)"];
    lines.push(`node: ${report.node ?? "unknown"}`);
    if (report.tokens === undefined || report.tokens === null) {
      lines.push("tokens: unavailable (no authoritative or accumulated usage was recorded)");
    } else {
      lines.push(`tokens: ${report.tokens} total input+output (authoritative; not billed AI credits)`);
    }
    if (report.durationMs !== undefined) lines.push(`durationMs: ${report.durationMs}`);
    if (report.model) lines.push(`model: ${report.model}`);
    lines.push(`status: ${report.failed
      ? `failed: ${report.error ?? "unknown error"}`
      : report.cancelled ? "cancelled" : "completed"}`);
    if (report.cwd) lines.push(`worktree: ${report.cwd}`);
    if (advice && advice.recommend_split) {
      lines.push(`split-advice: RECOMMEND SPLIT for ${advice.agent} — ${(advice.reasons ?? []).join("; ")}`);
    } else if (advice) {
      lines.push(`split-advice: no split recommended for ${advice.agent}`);
    } else {
      lines.push("split-advice: unavailable");
    }
    lines.push("advisory only: a parent must PROPOSE any split for human approval; " +
      "org topology and charters do not change automatically.");
    return lines.join("\n");
  }

  async function injectCompletion(input) {
    let key;
    if (input.toolName === "task") key = input.toolCallId;
    else if (input.toolName === "read_agent") key = input.toolArgs?.agent_id;
    else return undefined;
    if (key == null) return undefined;
    const report = reports.get(key);
    // No recorded completion (e.g. a still-running read_agent) => never annotate.
    if (!report) return undefined;
    if (reported.has(key)) return undefined;
    reported.add(key); // in-memory guard for repeated reads within this process
    // Race-safe cross-process claim so the footer reaches the model exactly once across both providers.
    let claim;
    try {
      claim = await callOracle("claimCompletion", {
        cwd: input.cwd ?? report.cwd, node: report.node, key: report.agentId ?? key,
      });
    } catch (error) {
      reportError(`agent-org: completion claim failed: ${error.message}`);
      return undefined;
    }
    if (!claim || claim.claimed !== true) return undefined;
    // Evict the consumed report; both keys point at the same object.
    reports.delete(report.agentId);
    if (input.toolName === "task" && input.toolCallId) reports.delete(input.toolCallId);
    let advice;
    try {
      if ((input.cwd ?? report.cwd) && report.node) {
        advice = await callOracle("splitAdvice", { cwd: input.cwd ?? report.cwd, node: report.node });
      }
    } catch (error) {
      reportError(`agent-org: split advice failed: ${error.message}`);
    }
    const toolResult = input.toolResult ?? {};
    const original = toolResult.textResultForLlm ?? "";
    return { modifiedResult: { ...toolResult, textResultForLlm: original + "\n\n" + completionFooter(report, advice) } };
  }

  return { hooks, onEvent };
}
