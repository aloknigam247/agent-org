const marker = /^AgentOrgActingNode:[ \t]*([a-z][a-z0-9-]*)[ \t]*(?:\r?\n|$)/;
const validTokens = (value) => Number.isSafeInteger(value) && value >= 0;
const nativeId = (value) => typeof value === "string" && value.trim() ? value : undefined;

export function createRuntime(callOracle, reportError = console.error) {
  const contexts = new Map();
  const endedContexts = new Map();
  const names = new Map();
  // Session-wide peak input context, never cumulative CLI totals or output-token consumption.
  const usageBySession = new Map();
  const roots = new Map();
  const stopEnforced = new Set();
  // One latest completion per native child id, replaced/invalidated at the next turn.
  // A hook has no delivery acknowledgement: never consume a report or persist a pre-delivery claim.
  const reports = new Map();

  function beginTurn(id) {
    reports.delete(id);
    // Reused child turns share one session peak; a new turn only invalidates the completion.
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
    onAgentStop: async (input, invocation) => {
      const sessionId = input.sessionId ?? invocation?.sessionId;
      const context = contexts.get(sessionId);
      if (!context?.node || stopEnforced.has(sessionId)) return;
      const cwd = context.worktree ?? roots.get(context.run_id) ?? input.workingDirectory;
      if (!cwd) return;
      try {
        const result = await callOracle("rootSplitCheck", { cwd, node: context.node });
        if (result.is_root === true && result.recommend_split === true) {
          stopEnforced.add(sessionId);
          return {
            decision: "block",
            reason: "Your --split-advice returned a split verdict requiring human triage. " +
              "As the top node, no parent will surface it, so produce a structured SplitProposal " +
              "(per .github\\agents\\splitter.md) for the human to approve/edit/reject.",
          };
        }
      } catch (error) {
        reportError(`agent-org: root split check failed: ${error.message}`);
      }
    },
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
      const sessionId = input.sessionId ?? invocation?.sessionId;
      if (contexts.has(sessionId)) endedContexts.set(sessionId, contexts.get(sessionId));
      contexts.delete(sessionId);
      stopEnforced.delete(sessionId);
      // Completion can follow SessionEnd; retain its usage and attribution without authorizing more tools.
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
      endedContexts.delete(value.sessionId);
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
      const observed = usageBySession.get(id) ?? { peak: undefined, partial: false };
      // inputTokens already includes cached input. Output/cache counters and totalTokens are not occupancy.
      // A missing/invalid input sample is explicitly partial, not a fabricated zero.
      if (validTokens(usage.inputTokens)) {
        observed.peak = observed.peak === undefined ? usage.inputTokens : Math.max(observed.peak, usage.inputTokens);
      } else {
        observed.partial = true;
      }
      usageBySession.set(id, observed);
    } else if (event.type === "subagent.completed" || event.type === "subagent.failed") {
      const context = contexts.get(id) ?? endedContexts.get(id);
      const cwd = context?.worktree ?? roots.get(context?.run_id);
      const data = event.data ?? {};
      const node = context?.node ?? data.agentName ?? names.get(id);
      const observed = usageBySession.get(id);
      const report = {
        agentId: id, node, context, cwd,
        tokens: observed?.peak,
        partial: observed?.partial === true,
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
      names.delete(id);
      try {
        if (cwd && node && report.tokens !== undefined) {
          // The oracle atomically maintains one peak record per (node, session), across both providers.
          await callOracle("usage", { cwd, node, sessionId: id, tokens: report.tokens, partial: report.partial });
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
      lines.push("peak context: unavailable (no valid inputTokens sample was observed)");
    } else {
      lines.push(`peak context: ${report.tokens} tokens (peak occupancy; ` +
        `${report.partial ? "partial observations; " : ""}not billed AI credits)`);
    }
    if (report.durationMs !== undefined) lines.push(`durationMs: ${report.durationMs}`);
    if (report.model) lines.push(`model: ${report.model}`);
    lines.push(`status: ${report.failed
      ? `failed: ${report.error ?? "unknown error"}`
      : report.cancelled ? "cancelled" : "completed"}`);
    if (report.cwd) lines.push(`worktree: ${report.cwd}`);
    if (report.usageError) lines.push("usage-recording: failed (split advice may omit this completion)");
    if (advice && advice.recommend_split) {
      lines.push(`split-advice: SPLIT VERDICT for ${advice.agent} — ${(advice.reasons ?? []).join("; ")}`);
      lines.push("Human triage required: the parent MUST return a structured SplitProposal " +
        "(per .github\\agents\\splitter.md) through the Host's ask_user approve/edit/reject gate.");
    } else if (advice) {
      lines.push(`split-advice: no split recommended for ${advice.agent}`);
    } else {
      lines.push("split-advice: unavailable");
    }
    lines.push("Org topology and charters do not change automatically.");
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
