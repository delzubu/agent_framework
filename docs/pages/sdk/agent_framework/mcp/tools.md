---
title: agent_framework.mcp.tools
layout: default
sdk_page: true
---


# `agent_framework.mcp.tools`

## API Summary

Bridge MCP tools into the ToolRegistry as standard Tool instances.

## Source

`src/agent_framework/mcp/tools.py`

## Classes

- [`McpBridgeTool`](tools/McpBridgeTool.html)

## Functions

### `bridge_mcp_tools`

```python
def bridge_mcp_tools(manager: Any, tool_registry: Any, run_coro: Callable) -> None
```

Register McpBridgeTool instances for every connected MCP tool.

Args:
    manager: An McpManager instance.
    tool_registry: A ToolRegistry to register tools into.
    run_coro: A callable(coro) that runs an async coroutine synchronously.
