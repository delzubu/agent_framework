# Purpose

`AgentHost` is the primary orchestration object in `agent_framework`.

It owns the runtime context for agent execution: model driver access, agent discovery, tool discovery, command dispatch, skill lookup, conversation persistence, tracing, user communication, and optional MCP bridge setup.

## Typical Lifecycle

1. Construct the host from environment configuration or explicit dependencies.
2. Start the host if the selected construction path does not already do it.
3. Run agents or direct model completions.
4. Inspect traces, results, callbacks, or conversation state.
5. Close the host when asynchronous resources such as MCP clients or async drivers need shutdown.

## Common Mistakes

- Creating registries manually when `AgentHost` can assemble them from configuration.
- Forgetting lifecycle management when MCP or async drivers are enabled.
- Treating `complete(...)` as an agent run. `complete(...)` is a direct model call; `run_agent(...)` executes the markdown-defined agent loop.
