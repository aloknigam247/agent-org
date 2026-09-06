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
