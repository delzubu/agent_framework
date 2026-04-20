---
title: agent_framework.host
layout: default
sdk_page: true
---


# `agent_framework.host`

<!-- BEGIN sdk-overlay -->

# Purpose

`agent_framework.host` is the main runtime boundary for applications that embed or run markdown-defined agents.

The module brings together the pieces that are intentionally kept separate elsewhere in the package: agents, tools, skills, model drivers, conversation storage, user communication, callbacks, tracing, and optional MCP integration.

Use this module when you need to run an agent, call a model through the framework, or host a tool-calling loop without building the orchestration plumbing yourself.

## Usage Pattern

Most applications start with one of two host construction paths:

- `AgentHost.from_env(...)` or `AgentHost.from_env_console(...)` when the runtime should be configured from environment variables and project directories.
- `AgentHost.create(...)` when an application wants to supply dependencies directly, usually for tests, services, or embedded use.

Once created, a host is responsible for discovering registries, managing lifecycle, and running agent or model calls.

## Common Entry Points

- `AgentHost.from_env_console(...)` for CLI-style interactive runs.
- `AgentHost.create(...)` for programmatic construction.
- `AgentHost.run_agent(...)` for markdown-defined agents.
- `AgentHost.complete(...)` and `AgentHost.complete_async(...)` for direct model calls.
- `run_tool_loop(...)` for service-style tool-calling loops without a markdown agent file.

## Design Notes

The host is intentionally central. If code needs to coordinate model calls, tools, skills, callbacks, audit records, and user communication, it should usually do that through `AgentHost` instead of reaching into lower-level registries directly.

<!-- END sdk-overlay -->

## API Summary

Root host for loading agents, tools, and servicing runtime interactions.

## Source

`src/agent_framework/host.py`

## Classes

- [`SubagentBatchItemResult`](host/SubagentBatchItemResult.html)
- [`AgentHost`](host/AgentHost.html)

## Functions

### `run_tool_loop`

```python
async def run_tool_loop(host: AgentHost, *, messages: list[dict[str, Any]], tools: Sequence[ToolDefinition], tool_executor: Callable[[str, dict[str, Any]], Awaitable[str]] | None = None, terminal_tools: Sequence[str] = (), max_iterations: int = 10, conversation_id: str | None = None, model_names: str | tuple[str, ...] | None = None, temperature: float = 0.2, response_format: dict[str, Any] | None = None, response_mode: str = DEFAULT_RESPONSE_MODE) -> ModelResponse
```

Run a multi-turn tool-calling loop using ``complete_async()``.

Loops until one of:
- The model returns ``finish_reason="stop"`` (or no tool calls).
- A terminal tool is called — returns immediately with ``finish_reason=
  "terminal_tool"`` and the tool call arguments as ``raw_text``.
- ``max_iterations`` is reached.

This gives callers the equivalent of dial-agent's ``DialProvider.run()``
with clarification/terminal tool support, without requiring markdown agent
definitions.

Args:
    host: The ``AgentHost`` to use for model calls.
    messages: Initial message list.  Modified in-place as turns progress.
    tools: Tool definitions exposed to the model.
    tool_executor: Async callable ``(tool_name, arguments) -> result_str``.
        When ``None``, tool calls are recorded but not executed.
    terminal_tools: Tool names that cause an immediate loop exit when called
        by the model.  The tool is not executed; its arguments are returned.
    max_iterations: Maximum number of model calls before raising
        ``RuntimeError``.
    conversation_id: Passed through to ``complete_async()`` for store
        integration.
    model_names: Model(s) to use.  Accepts a comma-separated string, a
        tuple, or ``None`` to use ``host.config.default_model``.
    temperature: Passed to ``complete_async()``.
    response_format: Passed to ``complete_async()``.
    response_mode: ``"json_object"`` (default) or ``"text"``.  Controls
        how the driver parses the assistant turn.  Use ``"text"`` when the
        loop is purely tool-driven and the final assistant message is plain
        text.

Returns:
    The final ``ModelResponse``.  ``finish_reason`` is ``"terminal_tool"``
    when a terminal tool triggered the exit.

Raises:
    RuntimeError: When ``max_iterations`` is reached without a stop
        condition.
