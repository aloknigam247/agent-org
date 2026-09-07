import assert from "node:assert/strict";
import test from "node:test";
import { createRuntime } from "../plugin/extensions/agent-org/runtime.mjs";

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
