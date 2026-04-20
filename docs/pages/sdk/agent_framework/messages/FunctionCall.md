---
title: FunctionCall
layout: default
sdk_page: true
---


# `FunctionCall`

Module: [`agent_framework.messages`](../messages.html)

## API Summary

```python
class FunctionCall
```

Function call arguments from a model tool call.

Attributes:
    name: Name of the function to call.
    arguments: JSON-encoded arguments string.

## Attributes

- `arguments`
- `name`

## Methods

### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

No method docstring is available yet.

### `from_dict`

```python
def from_dict(cls, data: dict[str, Any]) -> FunctionCall
```

No method docstring is available yet.
