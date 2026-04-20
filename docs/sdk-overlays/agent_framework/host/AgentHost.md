# Purpose

`AgentHost` is the primary orchestration object in `agent_framework`.

It owns the runtime context for agent execution: model driver access, agent discovery, tool discovery, command dispatch, skill lookup, conversation persistence, tracing, user communication, and optional MCP bridge setup.

## Typical Lifecycle

1. Construct the host from environment configuration or explicit dependencies.
2. Start the host if the selected construction path does not already do it.
3. Run agents or direct model completions.
4. Inspect traces, results, callbacks, or conversation state.
5. Close the host when asynchronous resources such as MCP clients or async drivers need shutdown.

## Usage Guidance

Use `AgentHost` directly when building applications, tests, evaluators, or command-line tools around the framework.

For simple console projects, prefer `AgentHost.from_env_console(...)`. For embedded services, prefer `AgentHost.create(...)` with explicit dependencies so the surrounding application controls configuration and lifecycle.

## Common Mistakes

- Creating registries manually when `AgentHost` can assemble them from configuration.
- Forgetting lifecycle management when MCP or async drivers are enabled.
- Treating `complete(...)` as an agent run. `complete(...)` is a direct model call; `run_agent(...)` executes the markdown-defined agent loop.
- Hiding invalid model decisions with repair logic. `AgentHost` relies on strict decision parsing so invalid structured output is visible during development and evaluation.
