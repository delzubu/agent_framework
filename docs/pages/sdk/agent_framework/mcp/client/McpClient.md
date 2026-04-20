---
title: McpClient
layout: default
sdk_page: true
---


# `McpClient`

Module: [`agent_framework.mcp.client`](../client.html)

## API Summary

```python
class McpClient
```

High-level async MCP client wrapping a single server connection.

## Methods

### `connect`

```python
async def connect(self) -> None
```

No method docstring is available yet.

### `disconnect`

```python
async def disconnect(self) -> None
```

No method docstring is available yet.

### `list_tools`

```python
async def list_tools(self) -> list[McpToolInfo]
```

No method docstring is available yet.

### `call_tool`

```python
async def call_tool(self, tool_name: str, arguments: dict) -> str
```

No method docstring is available yet.

### `reconnect`

```python
async def reconnect(self) -> None
```

No method docstring is available yet.
