---
title: Tool
layout: default
sdk_page: true
---


# `Tool`

Module: [`agent_framework.tool`](../tool.html)

## API Summary

```python
class Tool
```

Base class for concrete tools loaded from sibling Python modules.

## Attributes

- `definition`
- `source_path`

## Methods

### `name`

```python
def name(self) -> str
```

Return the stable tool identifier.

### `description`

```python
def description(self) -> str
```

Return the caller-visible tool description.

### `model_definition`

```python
def model_definition(self) -> ToolDefinition
```

Return the model-visible tool definition.

### `invoke`

```python
def invoke(self, arguments: dict[str, Any], host: 'AgentHost') -> str
```

Execute the tool with validated arguments.

### `from_name`

```python
def from_name(cls, name: str, tools_directory: str | Path) -> 'Tool'
```

Load a tool from `<tools_directory>/<name>.md` and `.py`.
