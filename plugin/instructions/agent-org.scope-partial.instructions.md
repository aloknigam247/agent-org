---
applyTo: "**"
---

# Partial managed area

Host-only routing: apply the guard in `agent-org.instructions.md` before following this profile.

Read `org.json`'s `scope` before routing. Send only the managed portion of a request to the configured root;
the Host or human can handle the rest normally. Separate mixed requests before dispatch.

Outside-scope paths are allowed and unmanaged, not ownership gaps. Scope does not shrink when the root splits.
