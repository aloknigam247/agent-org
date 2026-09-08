const marker = /^AgentOrgActingNode:[ \t]*([a-z][a-z0-9-]*)[ \t]*(?:\r?\n|$)/;
const validTokens = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
const nativeId = (value) => typeof value === "string" && value.trim() ? value : undefined;

export function createRuntime(callOracle, reportError = console.error) {
  const contexts = new Map();
  const names = new Map();
  const tokens = new Map();
  const roots = new Map();
  // One latest completion per native child id, replaced/invalidated at the next turn.
  // A hook has no delivery acknowledgement: never consume a report or persist a pre-delivery claim.
  const reports = new Map();

  function beginTurn(id) {
    reports.delete(id);
    tokens.delete(id);
  }

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
      if (!injection) return base;
      const result = { ...base, ...injection };
      if (base?.additionalContext && injection.additionalContext) {
        result.additionalContext = base.additionalContext + "\n" + injection.additionalContext;
      }
      return result;
    },
    onPreToolUse: async (input, invocation) => {
      const result = await toolHook(input, invocation, false);
      if (result?.permissionDecision !== "deny" && input.toolName === "write_agent") {
        const ids = [input.toolArgs?.agent_id, ...(Array.isArray(input.toolArgs?.agent_ids) ? input.toolArgs.agent_ids : [])];
        for (const id of ids) if (nativeId(id)) beginTurn(id);
      }
      return result;
    },
    onSessionEnd: async (input, invocation) => {
      contexts.delete(input.sessionId ?? invocation?.sessionId);
      // The native completion event can follow SessionEnd; retain observed usage until it is captured.
    },
    onUserPromptSubmitted: async (input, invocation) => {
      const value = payload(input, invocation);
      const prompt = input.prompt ?? "";
      const match = marker.exec(prompt);
      if (!value.sessionId) return;
      if (!match) {
        beginTurn(value.sessionId); // Native follow-up turns need not repeat the identity headers.
        return;
      }
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
      roots.set(context.run_id, context.worktree ?? value.cwd);
      beginTurn(value.sessionId);
    },
  };

  async function onEvent(event) {
    const id = nativeId(event.agentId);
    if (!id) return;
    if (event.type === "subagent.started") {
      beginTurn(id);
      names.set(id, event.data?.agentName);
    } else if (event.type === "assistant.usage") {
      const usage = event.data ?? {};
      // inputTokens already includes cached input in the CLI usage total.
      // Missing/invalid components are not invented zeros; observed usage may be partial.
      const components = [usage.inputTokens, usage.outputTokens].filter(validTokens);
      if (components.length) {
        tokens.set(id, (tokens.get(id) ?? 0) + components.reduce((sum, value) => sum + value, 0));
      }
    } else if (event.type === "subagent.completed" || event.type === "subagent.failed") {
      const context = contexts.get(id);
      const cwd = context?.worktree ?? roots.get(context?.run_id);
      const data = event.data ?? {};
      const node = context?.node ?? data.agentName ?? names.get(id);
      const authoritative = validTokens(data.totalTokens);
      const report = {
        agentId: id, node, context, cwd,
        tokens: authoritative ? data.totalTokens : tokens.get(id),
        authoritative,
        durationMs: data.durationMs,
        model: data.model,
        failed: event.type === "subagent.failed",
        cancelled: data.cancelled === true,
        error: data.error,
        // Native event identity, not child-authored text or a hook invocation call id.
        tag: `[agent-org completion ${JSON.stringify([id, event.id ?? event.timestamp ?? null, data.toolCallId ?? null])}]`,
      };
      let ready;
      report.ready = new Promise((resolve) => { ready = resolve; });
      // Publish BEFORE the first asynchronous oracle call. session.on does not await this handler.
      reports.set(id, report);
      tokens.delete(id);
      names.delete(id);
      try {
        if (cwd && node && report.tokens !== undefined) {
          await callOracle("usage", { cwd, node, sessionId: id, tokens: report.tokens });
        } else if (report.tokens !== undefined) {
          reportError(`agent-org: no unambiguous workspace for usage from ${id}`);
        }
      } catch (error) {
        report.usageError = true;
        reportError(`agent-org: usage recording failed: ${error.message}`);
      } finally {
        // Keep immutable session bindings through reused turns; only SessionEnd releases them.
        ready();
      }
    }
  }

  function completionFooter(report, advice) {
    const lines = ["[agent-org] Authoritative child completion (appended by org runtime)"];
    lines.push(`node: ${report.node ?? "unknown"}`);
    lines.push(`agentId: ${report.agentId}`);
    if (report.tokens === undefined || report.tokens === null) {
      lines.push("tokens: unavailable (no authoritative or accumulated usage was recorded)");
    } else if (report.authoritative) {
      lines.push(`tokens: ${report.tokens} total input+output (authoritative; not billed AI credits)`);
    } else {
      lines.push(`tokens: ${report.tokens} observed input/output (accumulated usage; may be partial; ` +
        "not an authoritative total; not billed AI credits)");
    }
    if (report.durationMs !== undefined) lines.push(`durationMs: ${report.durationMs}`);
    if (report.model) lines.push(`model: ${report.model}`);
    lines.push(`status: ${report.failed
      ? `failed: ${report.error ?? "unknown error"}`
      : report.cancelled ? "cancelled" : "completed"}`);
    if (report.cwd) lines.push(`worktree: ${report.cwd}`);
    if (report.usageError) lines.push("usage-recording: failed (split advice may omit this completion)");
    if (advice && advice.recommend_split) {
      lines.push(`split-advice: RECOMMEND SPLIT for ${advice.agent} — ${(advice.reasons ?? []).join("; ")}`);
    } else if (advice) {
      lines.push(`split-advice: no split recommended for ${advice.agent}`);
    } else {
      lines.push("split-advice: unavailable");
    }
    lines.push("advisory only: a parent must PROPOSE any split for human approval; " +
      "org topology and charters do not change automatically.");
    lines.push(report.tag);
    return lines.join("\n");
  }

  async function injectCompletion(input) {
    let key;
    if (input.toolName === "task") {
      const telemetry = input.toolResult?.toolTelemetry;
      if (input.toolArgs?.mode === "background" || telemetry?.properties?.execution_mode === "background") return;
      key = nativeId(telemetry?.restrictedProperties?.agent_id);
    } else if (input.toolName === "read_agent") {
      key = nativeId(input.toolArgs?.agent_id);
    } else return undefined;
    if (!key) return { additionalContext: "agent-org: child completion not annotated: native agent identity is unavailable." };
    const report = reports.get(key);
    // No recorded completion (e.g. a still-running read_agent) => never annotate.
    if (!report) return undefined;
    const parent = report.context?.parent_session_id;
    const run = report.context?.run_id;
    if ((parent && parent !== input.sessionId) ||
        (run && input.agentOrgContext?.run_id && run !== input.agentOrgContext.run_id)) {
      return { additionalContext: "agent-org: child completion not annotated: bound parent/run does not match." };
    }
    const toolResult = input.toolResult;
    if (!toolResult || typeof toolResult.textResultForLlm !== "string") return undefined;
    await report.ready;
    if (reports.get(key) !== report) return undefined; // A new turn superseded this pending completion.
    const original = toolResult.textResultForLlm;
    // Inspect only our own native-event suffix for pipeline idempotence, never the body for identity.
    // Returning the prior modifiedResult explicitly preserves the first provider's appended footer.
    if (original.endsWith("\n" + report.tag)) return { modifiedResult: toolResult };
    const cwd = report.cwd ?? input.agentOrgContext?.worktree ?? input.cwd;
    let advice;
    try {
      if (cwd && report.node) {
        advice = await callOracle("splitAdvice", { cwd, node: report.node });
        if (advice?.agent !== report.node || typeof advice?.recommend_split !== "boolean") advice = undefined;
      }
    } catch (error) {
      reportError(`agent-org: split advice failed: ${error.message}`);
    }
    if (reports.get(key) !== report) return undefined;
    return { modifiedResult: { ...toolResult, textResultForLlm: original + "\n\n" + completionFooter(report, advice) } };
  }

  return { hooks, onEvent };
}
