import { execFile, execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export function findOracle(cwd) {
  if (!cwd) throw new Error("hook input has no working directory");
  let directory;
  try {
    directory = execFileSync("git", ["-C", path.resolve(cwd), "rev-parse", "--show-toplevel"], {
      encoding: "utf8", windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    }).trim();
  } catch (error) {
    if (error.status === 128) return undefined;
    throw error;
  }
  // Stop at this Git worktree boundary. Never walk out of a linked worktree into its source.
  const installed = path.join(directory, ".github", "agent-org");
  const tool = path.join(installed, "tools", "owner_validator.py");
  if (fs.existsSync(tool)) return { directory, tool };
  if (fs.existsSync(installed)) throw new Error(`missing installed owner oracle: ${tool}`);
  return undefined;
}

export function createOracle() {
  return async function callOracle(event, payload) {
    const found = findOracle(payload.agentOrgContext?.worktree ?? payload.cwd);
    if (!found) return {};
    const args = ["-X", "utf8", found.tool];
    if (event === "usage") {
      args.push("--usage-record", payload.node, "--session", payload.sessionId,
        "--tokens", String(payload.tokens), "--root", found.directory);
      if (payload.partial) args.push("--partial");
    } else if (event === "splitAdvice") {
      args.push("--split-advice", payload.node, "--root", found.directory);
    } else if (event === "rootSplitCheck") {
      args.push("--root-split-check", payload.node, "--root", found.directory);
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
  };
}
