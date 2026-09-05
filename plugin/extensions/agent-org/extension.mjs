import { joinSession } from "@github/copilot-sdk/extension";
import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { createRuntime } from "./runtime.mjs";

function findOracle(cwd) {
  if (!cwd) throw new Error("hook input has no working directory");
  let directory = path.resolve(cwd);
  for (;;) {
    const tool = path.join(directory, ".github", "agent-org", "tools", "owner_validator.py");
    if (fs.existsSync(tool) && fs.existsSync(path.join(directory, "org.json"))) return { directory, tool };
    const parent = path.dirname(directory);
    if (parent === directory) return undefined;
    directory = parent;
  }
}

async function callOracle(event, payload) {
  const found = findOracle(payload.cwd);
  if (!found) return {};
  const args = ["-X", "utf8", found.tool];
  if (event === "usage") {
    args.push("--usage-record", payload.node, "--tokens", String(payload.tokens), "--root", found.directory);
  } else {
    args.push(event === "postToolUse" ? "--post-hook" : "--hook");
  }
  return new Promise((resolve, reject) => {
    const env = { ...process.env };
    delete env.AGENT_ORG_ACTING;
    const child = execFile("python", args, {
      cwd: found.directory, encoding: "utf8", env, timeout: 15000, windowsHide: true,
    }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(stderr.trim() || stdout.trim() || error.message));
        return;
      }
      try {
        resolve(stdout.trim() ? JSON.parse(stdout) : {});
      } catch (parseError) {
        reject(new Error(`invalid oracle response: ${parseError.message}`));
      }
    });
    child.stdin.on("error", reject);
    child.stdin.end(JSON.stringify(payload));
  });
}

const runtime = createRuntime(callOracle);
const session = await joinSession({ hooks: runtime.hooks, tools: [] });
session.on((event) => {
  runtime.onEvent(event).catch((error) => console.error(`agent-org usage recording failed: ${error.message}`));
});
