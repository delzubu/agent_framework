---
title: agent_framework.builtin_tools.base
layout: default
sdk_page: true
---


# `agent_framework.builtin_tools.base`

## API Summary

Shared helpers for built-in tools.

## Source

`src/agent_framework/builtin_tools/base.py`

## Classes

- [`PermissionGatedTool`](base/PermissionGatedTool.html)

## Functions

### `build_definition`

```python
def build_definition(tool_id: str, description: str, parameters: list[ToolParameter]) -> ToolDefinition
```

Construct a ToolDefinition for a built-in tool.
