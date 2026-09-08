import assert from "node:assert/strict";
import test from "node:test";
import { createRuntime } from "../plugin/extensions/agent-org/runtime.mjs";
import { createRuntime as createProjectRuntime } from "../.github/extensions/agent-org/runtime.mjs";

function makeOracle(advice) {
  return async (event, input) => {
    assert.notEqual(event, "claimCompletion", "a pre-delivery claim cannot prove delivered output");
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

async function bindChild(runtime, child, node = "stock", parent = "parent", run = "root", worktree = "D:\\wt") {
  await runtime.hooks.onUserPromptSubmitted({
    sessionId: child, cwd: worktree,
    prompt: `AgentOrgActingNode: ${node}\nAgentOrgRunId: ${run}\nAgentOrgWorktree: ${worktree}\nParentAgentSessionId: ${parent}`,
  }, { sessionId: parent });
}

function taskInput(child, text = "body") {
  return {
    sessionId: "parent", timestamp: 1788841292820, cwd: "D:\\wt", toolName: "task",
    toolArgs: { agent_type: "stock", name: "same display name" },
    toolResult: { textResultForLlm: text, resultType: "success",
      toolTelemetry: { properties: { execution_mode: "sync" }, restrictedProperties: { agent_id: child } } },
  };
}

function readInput(child, text = "final") {
  return { sessionId: "parent", cwd: "D:\\wt", toolName: "read_agent", toolArgs: { agent_id: child },
    toolResult: { textResultForLlm: text, resultType: "success" } };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
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
  await bindChild(runtime, "child", "stock", "parent", "root", "D:\\repo");
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

test("literal live native task result without hook toolCallId retains sentinel and adds exact child footer", async () => {
  const runtime = createRuntime(makeOracle());
  const parent = "6d1a58d4-da7a-4d60-983d-a0ab33e0257d";
  const child = "b454ab27-2395-4066-ac41-d0ad801b75f8";
  const worktree = `D:\\agentOrg\\.worktrees\\${parent}`;
  const prompt = `AgentOrgActingNode: kernel\nAgentOrgRunId: ${parent}\n` +
    `AgentOrgWorktree: ${worktree}\nParentAgentSessionId: ${parent}\nWork`;
  await runtime.hooks.onUserPromptSubmitted({
    sessionId: parent, cwd: "D:\\agentOrg",
    prompt: `AgentOrgActingNode: main\nAgentOrgRunId: ${parent}\nAgentOrgWorktree: ${worktree}`,
  }, { sessionId: parent });
  await runtime.hooks.onUserPromptSubmitted({
    sessionId: child, cwd: "D:\\agentOrg", prompt,
  }, { sessionId: parent });
  await runtime.onEvent({ type: "subagent.started", agentId: child,
    data: { agentName: "kernel", toolCallId: "toolu_01PWmumTQvH8n8LAB76F7Rxz" } });
  await runtime.onEvent({
    type: "subagent.completed", agentId: child, timestamp: "2026-09-08T04:21:32.818Z",
    data: { toolCallId: "toolu_01PWmumTQvH8n8LAB76F7Rxz", agentName: "kernel",
      totalTokens: 59960, durationMs: 32185, totalToolCalls: 2 },
  });
  const toolResult = {
    textResultForLlm: "PROOF-SENTINEL b04cdd43-4037-4919-bd08-1386a33f1d79",
    resultType: "success",
    toolTelemetry: {
      properties: { agent_type: "custom-agent", execution_mode: "sync", resolved_model: "claude-opus-4.8",
        prompt_length: "808", response_length: "51",
        agent_name_hash: "6923dd1bc0460082c5d55a831908c24a282860b7f1cd6c2b79cf1bc8857c639c" },
      restrictedProperties: { agent_name: "kernel", agent_id: child },
      metrics: { numberOfToolCallsMadeByAgent: 2, response_length: 51 },
    },
  };
  const input = { sessionId: parent, timestamp: 1788841292820, cwd: "D:\\agentOrg", toolName: "task",
    toolArgs: { agent_type: "kernel", name: "kernel", prompt }, toolResult };
  const before = structuredClone(input);
  assert.deepEqual(Object.keys(input), ["sessionId", "timestamp", "cwd", "toolName", "toolArgs", "toolResult"]);
  const result = await runtime.hooks.onPostToolUse(input, { sessionId: parent });
  assert.deepEqual(result, { modifiedResult: { ...toolResult, textResultForLlm:
    "PROOF-SENTINEL b04cdd43-4037-4919-bd08-1386a33f1d79\n\n" +
    "[agent-org] Authoritative child completion (appended by org runtime)\n" +
    "node: kernel\n" +
    `agentId: ${child}\n` +
    "tokens: 59960 total input+output (authoritative; not billed AI credits)\n" +
    "durationMs: 32185\n" +
    "status: completed\n" +
    `worktree: ${worktree}\n` +
    "split-advice: no split recommended for kernel\n" +
    "advisory only: a parent must PROPOSE any split for human approval; " +
    "org topology and charters do not change automatically.\n" +
    `[agent-org completion ["${child}","2026-09-08T04:21:32.818Z","toolu_01PWmumTQvH8n8LAB76F7Rxz"]]`,
  } });
  assert.deepEqual(input, before);
  assert.equal(result.modifiedResult.toolTelemetry, toolResult.toolTelemetry);
});

test("a child completion with no self-reported usage is annotated with authoritative tokens + split advice", async () => {
  const runtime = createRuntime(makeOracle(
    { agent: "stock", recommend_split: true, reasons: ["peak session tokens 130000 >= 120000"] }));
  await bindParent(runtime);
  await bindChild(runtime, "child");
  await runtime.onEvent({ type: "subagent.started", agentId: "child", data: { agentName: "stock", toolCallId: "call-1" } });
  await runtime.onEvent({
    type: "subagent.completed", agentId: "child",
    data: { agentName: "stock", toolCallId: "call-1", totalTokens: 4242, durationMs: 999, model: "gpt-x" },
  });
  const result = await runtime.hooks.onPostToolUse({
    toolName: "task", toolCallId: "call-1", sessionId: "parent", cwd: "D:\\wt",
    toolResult: taskInput("child", "child-authored summary").toolResult,
  }, { sessionId: "parent" });
  assert.ok(result.modifiedResult, "task completion must be annotated");
  assert.equal(result.modifiedResult.resultType, "success");
  assert.match(result.modifiedResult.textResultForLlm, /^child-authored summary\n\n\[agent-org\] Authoritative child completion/);
  assert.match(result.modifiedResult.textResultForLlm, /tokens: 4242 total input\+output/);
  assert.match(result.modifiedResult.textResultForLlm, /model: gpt-x/);
  assert.match(result.modifiedResult.textResultForLlm, /RECOMMEND SPLIT for stock — peak session tokens 130000 >= 120000/);
  assert.match(result.modifiedResult.textResultForLlm, /must PROPOSE any split for human approval/);
});

test("native agentId selects distinct same-name siblings, never hook callId or parent identity", async () => {
  const runtime = createRuntime(makeOracle());
  await bindParent(runtime);
  for (const [child, total] of [["first", 10], ["second", 20]]) {
    await bindChild(runtime, child);
    await runtime.onEvent({ type: "subagent.completed", agentId: child,
      data: { agentName: "stock", toolCallId: `call-${child}`, totalTokens: total } });
  }
  const miss = await runtime.hooks.onPostToolUse(taskInput("parent"));
  assert.equal(miss.modifiedResult, undefined);
  const results = await Promise.all(["first", "second"].map((child) =>
    runtime.hooks.onPostToolUse({ ...taskInput(child), toolCallId: "call-second" }, { sessionId: "wrong-invocation" })));
  for (const [index, child, total] of [[0, "first", 10], [1, "second", 20]]) {
    assert.match(results[index].modifiedResult.textResultForLlm, new RegExp(`agentId: ${child}\\ntokens: ${total} total`));
  }
});

test("background read_agent is annotated only after a real completion is recorded", async () => {
  const runtime = createRuntime(makeOracle());
  await bindParent(runtime);
  await bindChild(runtime, "bg", "shipping");
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

test("duplicate provider output chaining and repeated reads deliver one footer per result", async () => {
  const first = createProjectRuntime(makeOracle());
  // Even a different split-advice snapshot must preserve the first provider's modifiedResult.
  const second = createRuntime(makeOracle({ agent: "stock", recommend_split: true, reasons: ["changed snapshot"] }));
  for (const runtime of [first, second]) {
    await bindParent(runtime);
    await bindChild(runtime, "child");
    await runtime.onEvent({ type: "subagent.completed", agentId: "child",
      data: { agentName: "stock", toolCallId: "call-1", totalTokens: 5 } });
  }
  for (const input of [taskInput("child"), readInput("child"), readInput("child")]) {
    const a = await first.hooks.onPostToolUse(input);
    const b = await second.hooks.onPostToolUse({ ...input, toolResult: a.modifiedResult });
    assert.deepEqual(b.modifiedResult, a.modifiedResult);
    assert.equal(b.modifiedResult.textResultForLlm.split("[agent-org] Authoritative child completion").length - 1, 1);
    const again = await first.hooks.onPostToolUse({ ...input, toolResult: b.modifiedResult });
    assert.deepEqual(again.modifiedResult, b.modifiedResult);
  }
  // Independent raw output still gets a footer; no claim was consumed ahead of delivery.
  assert.ok((await second.hooks.onPostToolUse(taskInput("child"))).modifiedResult);
});

test("a completion with no usage at all states tokens are unavailable, not zero", async () => {
  const runtime = createRuntime(makeOracle());
  await bindParent(runtime);
  await runtime.onEvent({ type: "subagent.completed", agentId: "child",
    data: { agentName: "stock", toolCallId: "call-2" } });
  const result = await runtime.hooks.onPostToolUse({
    toolName: "task", toolCallId: "call-2", sessionId: "parent", cwd: "D:\\wt",
    toolResult: taskInput("child").toolResult,
  }, { sessionId: "parent" });
  assert.match(result.modifiedResult.textResultForLlm, /tokens: unavailable/);
  assert.doesNotMatch(result.modifiedResult.textResultForLlm, /tokens: 0\b/);
});

test("a failed child completion preserves the failure resultType and reports the error", async () => {
  const runtime = createRuntime(makeOracle());
  await bindParent(runtime);
  await bindChild(runtime, "child");
  await runtime.onEvent({ type: "subagent.failed", agentId: "child",
    data: { agentName: "stock", toolCallId: "call-3", totalTokens: 12, error: "boom" } });
  const result = await runtime.hooks.onPostToolUse({
    toolName: "task", toolCallId: "call-3", sessionId: "parent", cwd: "D:\\wt",
    toolResult: { ...taskInput("child", "partial work").toolResult, resultType: "failure" },
  }, { sessionId: "parent" });
  assert.equal(result.modifiedResult.resultType, "failure");
  assert.match(result.modifiedResult.textResultForLlm, /^partial work\n\n/);
  assert.match(result.modifiedResult.textResultForLlm, /status: failed: boom/);
  assert.match(result.modifiedResult.textResultForLlm, /tokens: 12 total/);
});

test("completion is published before deferred usage with an unawaited native event handler", async () => {
  const usage = deferred();
  const audit = deferred();
  const calls = [];
  let recorded = false;
  const runtime = createRuntime(async (event, input) => {
    calls.push({ event, input });
    if (event === "usage") {
      await usage.promise;
      recorded = true;
    }
    if (event === "postToolUse") audit.resolve();
    if (event === "splitAdvice") {
      assert.equal(recorded, true, "advice must see the completed usage write");
      return { agent: input.node, recommend_split: true, reasons: ["native usage burden"] };
    }
    return {};
  });
  await bindParent(runtime);
  await bindChild(runtime, "child");
  await runtime.onEvent({ type: "assistant.usage", agentId: "child", data: { inputTokens: 3 } });
  // Matches extension.mjs session.on: invoke the event handler WITHOUT awaiting it.
  const completion = runtime.onEvent({ type: "subagent.completed", agentId: "child",
    data: { agentName: "stock", toolCallId: "native-call", totalTokens: 500 } });
  const post = runtime.hooks.onPostToolUse(taskInput("child"), { sessionId: "registering-provider" });
  await audit.promise;
  assert.equal(recorded, false);
  usage.resolve();
  const [, result] = await Promise.all([completion, post]);
  assert.match(result.modifiedResult.textResultForLlm, /agentId: child\ntokens: 500 total/);
  assert.match(result.modifiedResult.textResultForLlm, /RECOMMEND SPLIT for stock — native usage burden/);
  assert.deepEqual(calls.filter(({ event }) => event === "usage").map(({ input }) => input),
    [{ cwd: "D:\\wt", node: "stock", sessionId: "child", tokens: 500 }]);
});

test("a background task launch never gets a finished footer, even if completion already arrived", async () => {
  const runtime = createRuntime(makeOracle());
  await bindParent(runtime);
  await bindChild(runtime, "bg");
  const launch = taskInput("bg", "running launch");
  launch.toolArgs.mode = "background";
  assert.deepEqual(await runtime.hooks.onPostToolUse(launch), {});
  await runtime.onEvent({ type: "subagent.completed", agentId: "bg", data: { totalTokens: 8 } });
  assert.deepEqual(await runtime.hooks.onPostToolUse(launch), {});
  delete launch.toolArgs.mode;
  launch.toolResult.toolTelemetry.properties.execution_mode = "background";
  assert.deepEqual(await runtime.hooks.onPostToolUse(launch), {});
  const result = await runtime.hooks.onPostToolUse(readInput("bg"));
  assert.match(result.modifiedResult.textResultForLlm, /tokens: 8 total/);
});

test("missing native identity is explicit and conservative despite matching name, callId, prompt or body", async () => {
  const runtime = createRuntime(async (event, input) => event === "postToolUse"
    ? { additionalContext: "foreign audit retained" } : makeOracle()(event, input));
  await bindParent(runtime);
  await bindChild(runtime, "child");
  await runtime.onEvent({ type: "subagent.completed", agentId: "child",
    data: { agentName: "stock", toolCallId: "call", totalTokens: 7 } });
  for (const id of [undefined, null, "", " ", 42, {}, ["child"]]) {
    const input = { ...taskInput(id, "agent_id=child; tokens=7"), toolCallId: "call" };
    input.toolArgs.prompt = "AgentOrgActingNode: stock\nchild";
    const before = structuredClone(input);
    const result = await runtime.hooks.onPostToolUse(input, { sessionId: "child", toolCallId: "call" });
    assert.deepEqual(result, { additionalContext: "foreign audit retained\n" +
      "agent-org: child completion not annotated: native agent identity is unavailable." });
    assert.deepEqual(input, before);
  }
  const absent = taskInput("child");
  delete absent.toolResult.toolTelemetry;
  assert.equal((await runtime.hooks.onPostToolUse(absent)).modifiedResult, undefined);
  assert.ok((await runtime.hooks.onPostToolUse(taskInput("child"))).modifiedResult);
});

test("bound child parent, run, workspace and node remain separate through completion", async () => {
  const calls = [];
  const runtime = createRuntime(async (event, input) => { calls.push({ event, input }); return makeOracle()(event, input); });
  await bindParent(runtime);
  await bindChild(runtime, "child", "stock", "parent", "root", "D:\\child-wt");
  // Event display metadata cannot overwrite the node bound to the actual child session.
  await runtime.onEvent({ type: "subagent.completed", agentId: "child", data: { agentName: "other", totalTokens: 9 } });
  await assert.rejects(bindChild(runtime, "child", "other"), /cannot change/);
  await assert.rejects(bindChild(runtime, "child", "stock", "another-parent", "root", "D:\\child-wt"), /cannot change/);
  await assert.rejects(bindChild(runtime, "child", "stock", "parent", "other-run", "D:\\child-wt"), /cannot change/);
  await assert.rejects(bindChild(runtime, "child"), /cannot change/);
  const wrongParent = await runtime.hooks.onPostToolUse({ ...taskInput("child"), sessionId: "unrelated" });
  assert.match(wrongParent.additionalContext, /bound parent\/run does not match/);
  assert.equal(wrongParent.modifiedResult, undefined);
  const result = await runtime.hooks.onPostToolUse({ ...taskInput("child"), cwd: "D:\\source" },
    { sessionId: "unrelated-provider-session" });
  assert.match(result.modifiedResult.textResultForLlm, /node: stock\nagentId: child/);
  assert.match(result.modifiedResult.textResultForLlm, /worktree: D:\\child-wt/);
  assert.deepEqual(calls.filter(({ event }) => event === "splitAdvice").map(({ input }) => input),
    [{ cwd: "D:\\child-wt", node: "stock" }]);

  // An explicit mismatched run is not repaired by the parent session id.
  await bindChild(runtime, "other-child", "stock", "parent", "other-run", "D:\\other-wt");
  await runtime.onEvent({ type: "subagent.completed", agentId: "other-child", data: {} });
  const wrongRun = await runtime.hooks.onPostToolUse(taskInput("other-child"));
  assert.match(wrongRun.additionalContext, /bound parent\/run does not match/);
});

test("an unbound event does not guess usage workspace from the only root in the provider", async () => {
  const calls = [];
  const errors = [];
  const runtime = createRuntime(async (event, input) => { calls.push({ event, input }); return {}; },
    (error) => errors.push(error));
  await bindParent(runtime);
  await runtime.onEvent({ type: "subagent.started", agentId: "unbound", data: { agentName: "stock" } });
  await runtime.onEvent({ type: "subagent.completed", agentId: "unbound", data: { totalTokens: 10 } });
  assert.deepEqual(calls, []);
  assert.match(errors[0], /no unambiguous workspace/);
});

test("partial or absent usage never becomes an invented authoritative total", async () => {
  for (const [usage, totalTokens, expected] of [
    [{}, undefined, "tokens: unavailable"],
    [{ cacheReadTokens: 99, cacheWriteTokens: 7 }, undefined, "tokens: unavailable"],
    [{ inputTokens: 12 }, undefined, "tokens: 12 observed input/output"],
    [{ inputTokens: 12, outputTokens: 3 }, null, "tokens: 15 observed input/output"],
    [{ inputTokens: -10, outputTokens: "3" }, NaN, "tokens: unavailable"],
    [{ inputTokens: 12 }, -1, "tokens: 12 observed input/output"],
    [{ inputTokens: 0, outputTokens: 0 }, undefined, "tokens: 0 observed input/output"],
    [{}, 0, "tokens: 0 total input+output (authoritative; not billed AI credits)"],
  ]) {
    const runtime = createRuntime(makeOracle());
    await bindParent(runtime);
    await bindChild(runtime, "child");
    await runtime.onEvent({ type: "assistant.usage", agentId: "child", data: usage });
    await runtime.onEvent({ type: "subagent.failed", agentId: "child", data: { totalTokens, error: "stopped" } });
    const result = await runtime.hooks.onPostToolUse(taskInput("child", "partial"));
    const text = result.modifiedResult.textResultForLlm;
    assert.ok(text.includes(expected), text);
    if (expected.includes("observed")) {
      assert.ok(text.includes("may be partial; not an authoritative total; not billed AI credits"), text);
      assert.doesNotMatch(text, /total input\+output \(authoritative/);
    }
  }
});

test("usage and advice failures keep the completion available without consuming a delivery claim", async () => {
  const errors = [];
  let fail = true;
  const runtime = createRuntime(async (event, input) => {
    if (event === "usage" || (event === "splitAdvice" && fail)) throw new Error(`${event} unavailable`);
    return makeOracle()(event, input);
  }, (error) => errors.push(error));
  await bindParent(runtime);
  await bindChild(runtime, "child");
  await runtime.onEvent({ type: "subagent.completed", agentId: "child", data: { totalTokens: 6, cancelled: true } });
  const first = await runtime.hooks.onPostToolUse(taskInput("child"));
  assert.match(first.modifiedResult.textResultForLlm, /tokens: 6 total/);
  assert.match(first.modifiedResult.textResultForLlm, /status: cancelled/);
  assert.match(first.modifiedResult.textResultForLlm, /usage-recording: failed/);
  assert.match(first.modifiedResult.textResultForLlm, /split-advice: unavailable/);
  assert.equal(errors.length, 2);
  fail = false;
  const retry = await runtime.hooks.onPostToolUse(readInput("child"));
  assert.match(retry.modifiedResult.textResultForLlm, /split-advice: no split recommended for stock/);
});

test("missing split advice is unavailable rather than a fabricated no-split verdict", async () => {
  const runtime = createRuntime(async () => ({}));
  await bindParent(runtime);
  await bindChild(runtime, "child");
  await runtime.onEvent({ type: "subagent.completed", agentId: "child", data: {} });
  const result = await runtime.hooks.onPostToolUse(taskInput("child"));
  assert.match(result.modifiedResult.textResultForLlm, /split-advice: unavailable/);
  assert.doesNotMatch(result.modifiedResult.textResultForLlm, /no split recommended/);
});

test("reused child turns invalidate old reads and do not permanently suppress new completions", async () => {
  const providers = [createProjectRuntime(makeOracle()), createRuntime(makeOracle())];
  for (const runtime of providers) {
    await bindParent(runtime);
    await bindChild(runtime, "child");
  }
  for (const turn of [1, 2, 3]) {
    for (const runtime of providers) {
      if (turn === 2) await runtime.hooks.onPreToolUse({
        sessionId: "parent", cwd: "D:\\wt", toolName: "write_agent", toolArgs: { agent_id: "child", message: "next" },
      });
      if (turn === 3) await runtime.hooks.onUserPromptSubmitted({
        sessionId: "child", cwd: "D:\\wt", prompt: "next plain follow-up",
      }, { sessionId: "parent" });
      assert.deepEqual(await runtime.hooks.onPostToolUse(readInput("child", "running")), {});
      await runtime.onEvent({ type: "subagent.started", agentId: "child", data: { agentName: "stock" } });
      await runtime.onEvent({ type: "assistant.usage", agentId: "child", data: { inputTokens: turn } });
      await runtime.onEvent({ type: "subagent.completed", agentId: "child", timestamp: `turn-${turn}`,
        data: { toolCallId: "same-call" } });
    }
    let output = readInput("child", `turn ${turn} body`).toolResult;
    for (const runtime of providers) {
      output = (await runtime.hooks.onPostToolUse({ ...readInput("child"), toolResult: output })).modifiedResult;
    }
    assert.match(output.textResultForLlm, new RegExp(`tokens: ${turn} observed`));
    assert.equal(output.textResultForLlm.split("[agent-org] Authoritative child completion").length - 1, 1);
    assert.ok(output.textResultForLlm.endsWith(`[agent-org completion ["child","turn-${turn}","same-call"]]`));
  }
});

test("a started turn supersedes an old completion still awaiting oracle work", async () => {
  for (const delayedEvent of ["usage", "splitAdvice"]) {
    const entered = deferred();
    const release = deferred();
    const runtime = createRuntime(async (event, input) => {
      if (event === delayedEvent) {
        entered.resolve();
        await release.promise;
      }
      return makeOracle()(event, input);
    });
    await bindParent(runtime);
    await bindChild(runtime, "child");
    const completion = runtime.onEvent({ type: "subagent.completed", agentId: "child", data: { totalTokens: 1 } });
    const post = runtime.hooks.onPostToolUse(readInput("child"));
    await entered.promise;
    await runtime.onEvent({ type: "subagent.started", agentId: "child", data: { agentName: "stock" } });
    release.resolve();
    await completion;
    assert.deepEqual(await post, {});
    await runtime.onEvent({ type: "subagent.completed", agentId: "child", timestamp: "new", data: { totalTokens: 2 } });
    const fresh = await runtime.hooks.onPostToolUse(readInput("child"));
    assert.match(fresh.modifiedResult.textResultForLlm, /tokens: 2 total/);
  }
});

test("denied follow-up permissions do not invalidate a valid completion", async () => {
  const runtime = createRuntime(async (event, input) => event === "preToolUse"
    ? { permissionDecision: "deny" } : makeOracle()(event, input));
  await bindParent(runtime);
  await bindChild(runtime, "child");
  await runtime.onEvent({ type: "subagent.completed", agentId: "child", data: { totalTokens: 1 } });
  assert.deepEqual(await runtime.hooks.onPreToolUse({
    toolName: "write_agent", toolArgs: { agent_id: "child" }, sessionId: "parent", cwd: "D:\\wt",
  }), { permissionDecision: "deny" });
  assert.ok((await runtime.hooks.onPostToolUse(readInput("child"))).modifiedResult);
});

test("SessionEnd before native completion preserves already observed child usage", async () => {
  const runtime = createRuntime(makeOracle(), () => {});
  await bindParent(runtime);
  await bindChild(runtime, "child");
  await runtime.onEvent({ type: "subagent.started", agentId: "child", data: { agentName: "stock" } });
  await runtime.onEvent({ type: "assistant.usage", agentId: "child", data: { inputTokens: 12 } });
  await runtime.hooks.onSessionEnd({ sessionId: "child" }, { sessionId: "parent" });
  await runtime.onEvent({ type: "subagent.completed", agentId: "child", data: {} });
  const result = await runtime.hooks.onPostToolUse(readInput("child"));
  assert.match(result.modifiedResult.textResultForLlm, /node: stock\nagentId: child\ntokens: 12 observed input\/output/);
});
