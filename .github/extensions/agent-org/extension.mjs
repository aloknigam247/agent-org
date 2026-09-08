import { joinSession } from "@github/copilot-sdk/extension";
import { createOracle } from "./oracle.mjs";
import { createRuntime } from "./runtime.mjs";

const runtime = createRuntime(createOracle());
const session = await joinSession({ hooks: runtime.hooks, tools: [] });
session.on((event) => {
  runtime.onEvent(event).catch((error) => console.error(`agent-org usage recording failed: ${error.message}`));
});
