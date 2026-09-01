// agent-org enforcement extension: holds the sessionId -> acting-node map in memory (populated from the
// parent-injected "AgentOrgActingNode: <id>" marker), and on every write shells out to the Python
// owner-oracle in warn mode to classify + log a foreign write. Fail-open: if the repo is not
// agent-org-managed (no org.json / no tools), it never interferes. The command-hook equivalent is the
// proven fallback for headless -p, where extensions do not attach.
import { joinSession } from "@github/copilot-sdk/extension";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const acting = new Map();       // sessionId -> node id (from the parent-injected marker)
const nameByAgent = new Map();  // agentId -> agentName (from subagent.started)
const tokensByAgent = new Map(); // agentId -> summed tokens (from assistant.usage)
const MARKER = /AgentOrgActingNode:\s*(\S+)/;

const sidOf = (input, invocation) => invocation?.sessionId ?? input?.sessionId;
const cwdOf = (input) => input?.workingDirectory ?? input?.cwd ?? process.cwd();
const allow = { permissionDecision: "allow" };
const toolPath = (cwd) => path.join(cwd, ".github", "tools", "owner_validator.py");

const session = await joinSession({
  hooks: {
    onUserPromptSubmitted: async (input, invocation) => {
      const s = sidOf(input, invocation);
      const m = MARKER.exec(input?.prompt || "");
      if (s && m) acting.set(s, m[1]);
    },
    onPreToolUse: async (input, invocation) => {
      const cwd = cwdOf(input);
      const org = path.join(cwd, "org.json");
      const tool = toolPath(cwd);
      if (!fs.existsSync(org) || !fs.existsSync(tool)) return undefined; // not an agent-org repo
      const s = sidOf(input, invocation);
      const node = s ? acting.get(s) : undefined;
      const payload = JSON.stringify({
        toolName: input?.toolName, toolArgs: input?.toolArgs, cwd, sessionId: s,
      });
      const env = { ...process.env };
      if (node) env.AGENT_ORG_ACTING = node;
      const r = spawnSync("python", [tool, "--hook", "--mode", "warn", "--org", org],
        { input: payload, encoding: "utf-8", env, cwd });
      try { return JSON.parse((r.stdout || "").trim()) || allow; } catch { return undefined; }
    },
    onSessionEnd: async (input, invocation) => {
      const s = sidOf(input, invocation);
      if (s) acting.delete(s);
    },
  },
  tools: [],
});

// Per-child token accounting: attribute usage to a subagent, then on completion persist it to the usage
// log so the parent can detect an over-burdened child and propose a split even if the child did not.
session.on((event) => {
  const t = event?.type || "";
  const aid = event?.agentId ?? null;
  const cwd = process.cwd();
  if (t === "subagent.started") {
    if (aid) nameByAgent.set(aid, event?.data?.agentName);
  } else if (t === "assistant.usage" && aid) {
    const d = event?.data || {};
    const toks = (d.inputTokens || 0) + (d.outputTokens || 0) + (d.cacheReadTokens || 0) + (d.cacheWriteTokens || 0);
    tokensByAgent.set(aid, (tokensByAgent.get(aid) || 0) + toks);
  } else if (t === "subagent.completed" && aid) {
    const node = nameByAgent.get(aid);
    const tokens = tokensByAgent.get(aid) || 0;
    const tool = toolPath(cwd);
    if (node && tokens > 0 && fs.existsSync(tool)) {
      spawnSync("python", [tool, "--usage-record", node, "--tokens", String(tokens), "--root", cwd],
        { encoding: "utf-8" });
    }
    tokensByAgent.delete(aid);
    nameByAgent.delete(aid);
  }
});

