---
title: ToolCallMessage
layout: default
sdk_page: true
---


# `ToolCallMessage`

Module: [`agent_framework.messages`](../messages.html)

## API Summary

```python
class ToolCallMessage
```

A tool call emitted by the model in an assistant message.

Attributes:
    id: Unique tool call identifier assigned by the provider.
    type: Always ``"function"`` for current providers.
    function: Function name and arguments.

## Attributes

- `function`
- `id`
- `type`

## Methods

### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

No method docstring is available yet.

### `from_dict`

```python
def from_dict(cls, data: dict[str, Any]) -> ToolCallMessage
```

No method docstring is available yet.
