import { execFile, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const protocol = "agent-org-config-relocation-v1";

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

export function providerInfo(entry) {
  const hash = createHash("sha256");
  for (const name of ["extension.mjs", "oracle.mjs", "runtime.mjs"]) {
    hash.update(name + "\0");
    hash.update(fs.readFileSync(path.join(path.dirname(entry), name), "utf8").replaceAll("\r\n", "\n"));
    hash.update("\0");
  }
  return { entry: path.resolve(entry), protocol, fingerprint: hash.digest("hex"), pid: process.pid };
}

export function createOracle(entry) {
  // Capture this process's loaded revision once; changing disk files does not attest a reload.
  const provider = providerInfo(entry);
  return async function callOracle(event, payload) {
    const found = findOracle(payload.agentOrgContext?.worktree ?? payload.cwd);
    if (!found) return {};
    const args = ["-X", "utf8", found.tool];
    if (event === "usage") {
      args.push("--usage-record", payload.node, "--tokens", String(payload.tokens), "--root", found.directory);
    } else if (event === "splitAdvice") {
      args.push("--split-advice", payload.node, "--root", found.directory);
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
      child.stdin.end(JSON.stringify({ ...payload, agentOrgProvider: provider }));
    });
  };
}
