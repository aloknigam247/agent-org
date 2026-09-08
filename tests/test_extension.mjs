import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRuntime } from "../plugin/extensions/agent-org/runtime.mjs";

// Shared helper: a mock oracle whose claimCompletion is a real cross-process exclusive-create in `stateDir`,
// mirroring owner_validator.claim_completion. splitAdvice returns `advice` (or a no-split default).
function makeOracle(stateDir, advice) {
  return async (event, input) => {
    if (event === "claimCompletion") {
      fs.mkdirSync(stateDir, { recursive: true });
      const safe = String(input.key).replace(/[^A-Za-z0-9._-]/g, "_") || "unkeyed";
      try {
        fs.writeFileSync(path.join(stateDir, `${safe}.claim`), "", { flag: "wx" });
        return { claimed: true };
      } catch {
        return { claimed: false };
      }
    }
    if (event === "splitAdvice") return advice ?? { agent: input.node, recommend_split: false, reasons: [] };
    return {};
  };
}

async function bindParent(runtime) {
  await runtime.hooks.onUserPromptSubmitted(
    { sessionId: "parent", cwd: "D:\\wt", prompt: "AgentOrgActingNode: main\nAgentOrgRunId: root\nAgentOrgWorktree: D:\\wt" },
    { sessionId: "parent" },
  );
}

function tmpState() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "agentorg-completion-"));
}


test("parallel child sessions retain distinct actors in one shared workspace", async () => {
  const calls = [];
  const runtime = createRuntime(async (event, input) => {
    calls.push({ event, input });
    return input.agentOrgContext.node === "coordinator" ? { permissionDecision: "deny" } : {};
  });
  for (const [sessionId, node] of [["parent", "coordinator"], ["first", "stock"], ["second", "shipping"]]) {
    await runtime.hooks.onUserPromptSubmitted({
      cwd: "D:\\repo",
      prompt: `AgentOrgActingNode: ${node}\nAgentOrgRunId: root-session\nAgentOrgWorktree: D:\\repo\\.worktrees\\run`,
    }, { sessionId });
  }
  const inputs = { toolName: "create", toolArgs: { path: "stock\\data.txt" }, cwd: "D:\\repo" };
  const [parent, first, second] = await Promise.all(
    ["parent", "first", "second"].map((sessionId) => runtime.hooks.onPreToolUse(inputs, { sessionId })),
  );
  assert.equal(parent.permissionDecision, "deny");
  assert.deepEqual(first, {});
  assert.deepEqual(second, {});
  assert.deepEqual(calls.map(({ input }) => input.agentOrgContext.node), ["coordinator", "stock", "shipping"]);
  assert.ok(calls.every(({ input }) => input.agentOrgContext.run_id === "root-session"));
});

test("sub-agent hooks attribute the executing child session, not the registering parent", async () => {
  const seen = [];
  const runtime = createRuntime(async (event, input) => {
    seen.push({ event, input });
    return input.agentOrgContext?.node === "main" ? { permissionDecision: "deny" } : {};
  });
  // The parent binds itself: input and invocation carry the same parent runtime session id.
  await runtime.hooks.onUserPromptSubmitted(
    { sessionId: "parent", cwd: "D:\\wt", prompt: "AgentOrgActingNode: main\nAgentOrgRunId: root\nAgentOrgWorktree: D:\\wt" },
    { sessionId: "parent" },
  );
  // Inherited child prompts arrive with the child runtime session in input and the parent in invocation.
  for (const [child, node] of [["child-kernel", "kernel"], ["child-eval", "eval"]]) {
    await runtime.hooks.onUserPromptSubmitted(
      { sessionId: child, cwd: "D:\\wt",
        prompt: `AgentOrgActingNode: ${node}\nAgentOrgRunId: root\nAgentOrgWorktree: D:\\wt\nParentAgentSessionId: parent` },
      { sessionId: "parent" },
    );
  }
  const tool = { toolName: "create", toolArgs: { path: "x\\data.txt" }, cwd: "D:\\wt" };
  for (const [child, node] of [["child-kernel", "kernel"], ["child-eval", "eval"]]) {
    const pre = await runtime.hooks.onPreToolUse({ ...tool, sessionId: child }, { sessionId: "parent" });
    const post = await runtime.hooks.onPostToolUse({ ...tool, sessionId: child }, { sessionId: "parent" });
    assert.deepEqual(pre, {});
    assert.deepEqual(post, {});
    for (const event of ["preToolUse", "postToolUse"]) {
      const forwarded = seen.find((s) => s.event === event && s.input.agentOrgContext?.node === node);
      assert.equal(forwarded.input.sessionId, child);
      assert.deepEqual(forwarded.input.agentOrgContext,
        { node, run_id: "root", worktree: "D:\\wt", parent_session_id: "parent" });
    }
  }
  const parentPre = await runtime.hooks.onPreToolUse({ ...tool, sessionId: "parent" }, { sessionId: "parent" });
  const parentPost = await runtime.hooks.onPostToolUse({ ...tool, sessionId: "parent" }, { sessionId: "parent" });
  assert.equal(parentPre.permissionDecision, "deny");
  assert.equal(parentPost.permissionDecision, "deny");
  for (const event of ["preToolUse", "postToolUse"]) {
    const parentCtx = seen.find((s) => s.event === event && s.input.agentOrgContext?.node === "main");
    assert.equal(parentCtx.input.sessionId, "parent");
    assert.equal(parentCtx.input.agentOrgContext.parent_session_id, undefined);
  }
});

test("ending a child session leaves the parent and siblings bound", async () => {
  const seen = [];
  const runtime = createRuntime(async (_event, input) => { seen.push(input.agentOrgContext?.node); return {}; });
  await runtime.hooks.onUserPromptSubmitted(
    { sessionId: "parent", cwd: "D:\\wt", prompt: "AgentOrgActingNode: main\nAgentOrgRunId: root\nAgentOrgWorktree: D:\\wt" },
    { sessionId: "parent" },
  );
  for (const [child, node] of [["child-kernel", "kernel"], ["child-eval", "eval"]]) {
    await runtime.hooks.onUserPromptSubmitted(
      { sessionId: child, cwd: "D:\\wt",
        prompt: `AgentOrgActingNode: ${node}\nAgentOrgRunId: root\nAgentOrgWorktree: D:\\wt\nParentAgentSessionId: parent` },
      { sessionId: "parent" },
    );
  }
  await runtime.hooks.onSessionEnd({ sessionId: "child-kernel", cwd: "D:\\wt" }, { sessionId: "parent" });
  const tool = { toolName: "view", cwd: "D:\\wt" };
  await runtime.hooks.onPreToolUse({ ...tool, sessionId: "child-kernel" }, { sessionId: "parent" });
  await runtime.hooks.onPreToolUse({ ...tool, sessionId: "child-eval" }, { sessionId: "parent" });
  await runtime.hooks.onPreToolUse({ ...tool, sessionId: "parent" }, { sessionId: "parent" });
  assert.deepEqual(seen.slice(-3), [undefined, "eval", "main"]);
});

test("hooks fall back to the invocation session when input omits a session id", async () => {
  let ctx;
  const runtime = createRuntime(async (_event, input) => { ctx = input.agentOrgContext; return {}; });
  await runtime.hooks.onUserPromptSubmitted(
    { cwd: "D:\\wt", prompt: "AgentOrgActingNode: stock" }, { sessionId: "solo" },
  );
  await runtime.hooks.onPreToolUse({ toolName: "create", cwd: "D:\\wt" }, { sessionId: "solo" });
  assert.equal(ctx.node, "stock");
  await runtime.hooks.onSessionEnd({ cwd: "D:\\wt" }, { sessionId: "solo" });
  await runtime.hooks.onPreToolUse({ toolName: "view", cwd: "D:\\wt" }, { sessionId: "solo" });
  assert.equal(ctx, undefined);
});

test("quoted marker text is not an identity and a session cannot change identity", async () => {
  let captured;
  const runtime = createRuntime(async (_event, input) => { captured = input; return {}; });
  await runtime.hooks.onUserPromptSubmitted(
    { prompt: "Explain: AgentOrgActingNode: wrong", cwd: "D:\\repo" }, { sessionId: "one" },
  );
  await runtime.hooks.onPreToolUse({ toolName: "view", cwd: "D:\\repo" }, { sessionId: "one" });
  assert.equal(captured.agentOrgContext, undefined);
  await runtime.hooks.onUserPromptSubmitted(
    { prompt: "AgentOrgActingNode: stock\nWork", cwd: "D:\\repo" }, { sessionId: "one" },
  );
  await assert.rejects(runtime.hooks.onUserPromptSubmitted(
    { prompt: "AgentOrgActingNode: coordinator\nWork", cwd: "D:\\repo" }, { sessionId: "one" },
  ), /cannot change/);
});

test("a child cannot change root run, workspace or parent under the same session id", async () => {
  const runtime = createRuntime(async () => ({}));
  const prompt = "AgentOrgActingNode: stock\nAgentOrgRunId: run-one\nAgentOrgWorktree: D:\\one\n" +
    "ParentAgentSessionId: parent-one\nWork";
  await runtime.hooks.onUserPromptSubmitted({ cwd: "D:\\one", prompt }, { sessionId: "child" });
  for (const [before, after] of [["run-one", "run-two"], ["D:\\one", "D:\\two"], ["parent-one", "parent-two"]]) {
    await assert.rejects(runtime.hooks.onUserPromptSubmitted(
      { cwd: "D:\\one", prompt: prompt.replace(before, after) }, { sessionId: "child" },
    ), /cannot change/);
  }
});

test("post-tool warnings reach the agent and session end evicts identity", async () => {
  let context;
  const runtime = createRuntime(async (event, input) => {
    context = input.agentOrgContext;
    return event === "postToolUse" ? { additionalContext: "foreign write: reconcile with parent" } : {};
  });
  await runtime.hooks.onUserPromptSubmitted(
    { prompt: "AgentOrgActingNode: stock", cwd: "D:\\repo" }, { sessionId: "one" },
  );
  const result = await runtime.hooks.onPostToolUse(
    { toolName: "create", cwd: "D:\\repo" }, { sessionId: "one" },
  );
  assert.match(result.additionalContext, /reconcile/);
  assert.equal(context.node, "stock");
  await runtime.hooks.onSessionEnd({}, { sessionId: "one" });
  await runtime.hooks.onPreToolUse({ toolName: "view", cwd: "D:\\repo" }, { sessionId: "one" });
  assert.equal(context, undefined);
});

test("an oracle failure is surfaced rather than silently allowing writes", async () => {
  const errors = [];
  const runtime = createRuntime(async () => { throw new Error("oracle unavailable"); }, (error) => errors.push(error));
  const result = await runtime.hooks.onPreToolUse({ toolName: "create", cwd: "D:\\repo" }, { sessionId: "one" });
  assert.equal(result.permissionDecision, "deny");
  assert.match(result.permissionDecisionReason, /oracle unavailable/);
  assert.equal(errors.length, 1);
});

test("usage is per child and cache counters are not counted again", async () => {
  const calls = [];
  const runtime = createRuntime(async (event, input) => calls.push({ event, input }));
  await runtime.hooks.onUserPromptSubmitted({
    cwd: "D:\\repo", prompt: "AgentOrgActingNode: coordinator\nAgentOrgRunId: root",
  }, { sessionId: "parent" });
  await runtime.onEvent({ type: "subagent.started", agentId: "child", data: { agentName: "stock" } });
  await runtime.onEvent({
    type: "assistant.usage", agentId: "child",
    data: { inputTokens: 100, outputTokens: 20, cacheReadTokens: 80, cacheWriteTokens: 10 },
  });
  await runtime.onEvent({ type: "subagent.completed", agentId: "child", data: {} });
  assert.deepEqual(calls, [{ event: "usage", input: {
    cwd: "D:\\repo", node: "stock", sessionId: "child", tokens: 120,
  } }]);
});

test("a child completion with no self-reported usage is annotated with authoritative tokens + split advice", async () => {
  const runtime = createRuntime(makeOracle(tmpState(),
    { agent: "stock", recommend_split: true, reasons: ["peak session tokens 130000 >= 120000"] }));
  await bindParent(runtime);
  await runtime.onEvent({ type: "subagent.started", agentId: "child", data: { agentName: "stock", toolCallId: "call-1" } });
  await runtime.onEvent({
    type: "subagent.completed", agentId: "child",
    data: { agentName: "stock", toolCallId: "call-1", totalTokens: 4242, durationMs: 999, model: "gpt-x" },
  });
  const result = await runtime.hooks.onPostToolUse({
    toolName: "task", toolCallId: "call-1", sessionId: "parent", cwd: "D:\\wt",
    toolResult: { textResultForLlm: "child-authored summary", resultType: "success" },
  }, { sessionId: "parent" });
  assert.ok(result.modifiedResult, "task completion must be annotated");
  assert.equal(result.modifiedResult.resultType, "success");
  assert.match(result.modifiedResult.textResultForLlm, /^child-authored summary\n\n\[agent-org\] Authoritative child completion/);
  assert.match(result.modifiedResult.textResultForLlm, /tokens: 4242 total input\+output/);
  assert.match(result.modifiedResult.textResultForLlm, /model: gpt-x/);
  assert.match(result.modifiedResult.textResultForLlm, /RECOMMEND SPLIT for stock — peak session tokens 130000 >= 120000/);
  assert.match(result.modifiedResult.textResultForLlm, /must PROPOSE any split for human approval/);
});

test("association is by toolCallId / agentId, not session identity", async () => {
  const runtime = createRuntime(makeOracle(tmpState()));
  await bindParent(runtime);
  await runtime.onEvent({ type: "subagent.completed", agentId: "child",
    data: { agentName: "stock", toolCallId: "call-1", totalTokens: 10 } });
  const miss = await runtime.hooks.onPostToolUse({
    toolName: "task", toolCallId: "OTHER-CALL", sessionId: "parent", cwd: "D:\\wt",
    toolResult: { textResultForLlm: "unrelated", resultType: "success" },
  }, { sessionId: "parent" });
  assert.equal(miss.modifiedResult, undefined);
  const hit = await runtime.hooks.onPostToolUse({
    toolName: "task", toolCallId: "call-1", sessionId: "parent", cwd: "D:\\wt",
    toolResult: { textResultForLlm: "matched", resultType: "success" },
  }, { sessionId: "parent" });
  assert.match(hit.modifiedResult.textResultForLlm, /^matched\n\n/);
});

test("background read_agent is annotated only after a real completion is recorded", async () => {
  const runtime = createRuntime(makeOracle(tmpState()));
  await bindParent(runtime);
  // Still-running: no completion recorded for this agent id => never annotate.
  const running = await runtime.hooks.onPostToolUse({
    toolName: "read_agent", toolArgs: { agent_id: "bg" }, sessionId: "parent", cwd: "D:\\wt",
    toolResult: { textResultForLlm: "status: running", resultType: "success" },
  }, { sessionId: "parent" });
  assert.equal(running.modifiedResult, undefined);
  // Completion arrives, then read_agent by agent_id is annotated.
  await runtime.onEvent({ type: "subagent.completed", agentId: "bg",
    data: { agentName: "shipping", toolCallId: "call-bg", totalTokens: 77 } });
  const done = await runtime.hooks.onPostToolUse({
    toolName: "read_agent", toolArgs: { agent_id: "bg" }, sessionId: "parent", cwd: "D:\\wt",
    toolResult: { textResultForLlm: "final child text", resultType: "success" },
  }, { sessionId: "parent" });
  assert.match(done.modifiedResult.textResultForLlm, /^final child text\n\n/);
  assert.match(done.modifiedResult.textResultForLlm, /tokens: 77 total/);
});

test("footer injection is exactly once across duplicate providers and repeated reads", async () => {
  const dir = tmpState();
  const first = createRuntime(makeOracle(dir));
  const second = createRuntime(makeOracle(dir));
  for (const runtime of [first, second]) {
    await bindParent(runtime);
    await runtime.onEvent({ type: "subagent.completed", agentId: "child",
      data: { agentName: "stock", toolCallId: "call-1", totalTokens: 5 } });
  }
  const post = (runtime) => runtime.hooks.onPostToolUse({
    toolName: "task", toolCallId: "call-1", sessionId: "parent", cwd: "D:\\wt",
    toolResult: { textResultForLlm: "body", resultType: "success" },
  }, { sessionId: "parent" });
  const a = await post(first);
  const b = await post(second);
  assert.equal([a, b].filter((r) => r.modifiedResult).length, 1);

  // Repeated read_agent on the same completion within one process injects only once.
  const solo = createRuntime(makeOracle(tmpState()));
  await bindParent(solo);
  await solo.onEvent({ type: "subagent.completed", agentId: "bg",
    data: { agentName: "shipping", toolCallId: "call-bg", totalTokens: 9 } });
  const read = () => solo.hooks.onPostToolUse({
    toolName: "read_agent", toolArgs: { agent_id: "bg" }, sessionId: "parent", cwd: "D:\\wt",
    toolResult: { textResultForLlm: "final", resultType: "success" },
  }, { sessionId: "parent" });
  const r1 = await read();
  const r2 = await read();
  assert.ok(r1.modifiedResult);
  assert.equal(r2.modifiedResult, undefined);
});

test("a completion with no usage at all states tokens are unavailable, not zero", async () => {
  const runtime = createRuntime(makeOracle(tmpState()));
  await bindParent(runtime);
  await runtime.onEvent({ type: "subagent.completed", agentId: "child",
    data: { agentName: "stock", toolCallId: "call-2" } });
  const result = await runtime.hooks.onPostToolUse({
    toolName: "task", toolCallId: "call-2", sessionId: "parent", cwd: "D:\\wt",
    toolResult: { textResultForLlm: "body", resultType: "success" },
  }, { sessionId: "parent" });
  assert.match(result.modifiedResult.textResultForLlm, /tokens: unavailable/);
  assert.doesNotMatch(result.modifiedResult.textResultForLlm, /tokens: 0\b/);
});

test("a failed child completion preserves the failure resultType and reports the error", async () => {
  const runtime = createRuntime(makeOracle(tmpState()));
  await bindParent(runtime);
  await runtime.onEvent({ type: "subagent.failed", agentId: "child",
    data: { agentName: "stock", toolCallId: "call-3", totalTokens: 12, error: "boom" } });
  const result = await runtime.hooks.onPostToolUse({
    toolName: "task", toolCallId: "call-3", sessionId: "parent", cwd: "D:\\wt",
    toolResult: { textResultForLlm: "partial work", resultType: "failure" },
  }, { sessionId: "parent" });
  assert.equal(result.modifiedResult.resultType, "failure");
  assert.match(result.modifiedResult.textResultForLlm, /^partial work\n\n/);
  assert.match(result.modifiedResult.textResultForLlm, /status: failed: boom/);
  assert.match(result.modifiedResult.textResultForLlm, /tokens: 12 total/);
});
