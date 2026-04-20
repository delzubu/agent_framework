---
title: ToolDefinition
layout: default
sdk_page: true
---


# `ToolDefinition`

Module: [`agent_framework.tool`](../tool.html)

## API Summary

```python
class ToolDefinition
```

Caller-visible tool contract loaded from Markdown.

## Attributes

- `description`
- `documentation`
- `parameters`
- `parameters_schema`
- `source_path`
- `tool_id`

## Methods

### `to_model_payload`

```python
def to_model_payload(self) -> dict[str, object]
```

Convert the definition to the model-facing tool shape.
