---
name: debug-embedding-agent-framework
description: |
  Debugging guide for host-layer agent_framework integration.
  Use when host integration, sub-agent dispatch from Python, memory wiring,
  or callback routing is misbehaving.
version: "1.0"
priority: 0
---

# debug-embedding-agent-framework skill

Use this skill when debugging the host layer — AgentHost setup, parameter passing from Python, sub-agent invocation, memory, or callback handling in the host program.

## How to use this skill

| Reference | When to load |
|-----------|-------------|
| `references/known-issues-embedding.md` | When the host code behaves unexpectedly — parameter passing, sub-agent dispatch, memory, callbacks |
| `references/trace-jsonl.md` | Before reading or querying a `.jsonl` audit log |
| `tools/parse_log.py` | Import or run to extract structured data from a `.jsonl` log |

## Related skills

- For the full embedding reference: load **embedding-agent-framework** skill
- For agent definition issues: load **debug-authoring-agents** skill
- For .env / model-provider issues: load **debug-operating-agent-framework** skill

## Base directory

The `references/` and `tools/` folders are in the same directory as this file.

## Debugging workflow

### 1. Locate the log

Audit logs are written by `JsonlTraceSubscriber` to the path configured in `.env`. Check `runtime.audit.agent_call_started` for the parameters and prompts the host passed in.

### 2. Verify parameter binding from host

Look for `runtime.parameters_bound` — this is the authoritative snapshot after all pre-run hooks. If a parameter you passed from `AgentHost.run()` is missing, compare against the agent's `.md` frontmatter: the parameter must be declared there.

### 3. Inspect callback flow from host

If the agent emits a callback decision (`callback`, `callback_to_caller`, `request_user_input`), the host receives `AgentResult(status="waiting")`. Check:
- `result.callback_intent` — what the agent is asking for
- `result.message` — human-readable ask
- `result.parameters` — structured payload

Resume with `host.resume(run_id=result.run_id, response={...})`.

### 4. Inspect sub-agent dispatch

`runtime.agent_finished` events on child runs show each sub-agent's final result. If a sub-agent is not being called, check that `AgentHost.run()` is passing the correct parent run context.

### 5. Inspect memory

Memory errors typically surface as tool failures. Check the `call_tool` decision with `tool_name` matching a memory tool, then find the next `llm.request` to see the tool result that was injected.
