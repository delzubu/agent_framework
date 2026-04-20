---
title: McpToolInfo
layout: default
sdk_page: true
---


# `McpToolInfo`

Module: [`agent_framework.mcp.types`](../types.html)

## API Summary

```python
class McpToolInfo
```

Metadata for a tool exposed by an MCP server.

## Attributes

- `description`
- `input_schema`
- `qualified_name`
- `server_name`
- `tool_name`

## Methods

### `make_qualified_name`

```python
def make_qualified_name(server_name: str, tool_name: str) -> str
```

Build the canonical qualified name: mcp__<server>__<tool>.
