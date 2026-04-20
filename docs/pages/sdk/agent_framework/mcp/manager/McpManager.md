---
title: McpManager
layout: default
sdk_page: true
---


# `McpManager`

Module: [`agent_framework.mcp.manager`](../manager.html)

## API Summary

```python
class McpManager
```

Manages the lifecycle of multiple MCP server connections.

Attributes:
    configs: Server name → McpServerConfig mapping.
    _clients: Connected McpClient instances.
    connect_errors: Per-server connection errors from the last start_all().

## Attributes

- `configs`
- `connect_errors`

## Methods

### `start_all`

```python
async def start_all(self) -> dict[str, Exception | None]
```

Connect all configured servers in parallel.

Returns a dict mapping server name → Exception (or None on success).
Never raises; individual failures are captured and logged.

### `stop_all`

```python
async def stop_all(self) -> None
```

Disconnect all connected servers.

### `all_tools`

```python
def all_tools(self) -> list[McpToolInfo]
```

Return all tools from all connected servers (cached on connect).

### `call_tool`

```python
async def call_tool(self, qualified_name: str, arguments: dict) -> str
```

Call an MCP tool by its qualified name (mcp__server__tool).

Auto-reconnects once if the connection appears to have dropped.
