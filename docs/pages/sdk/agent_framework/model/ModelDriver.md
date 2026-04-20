---
title: ModelDriver
layout: default
sdk_page: true
---


# `ModelDriver`

Module: [`agent_framework.model`](../model.html)

## API Summary

```python
class ModelDriver(Protocol)
```

Provider-agnostic protocol for a single agent decision step.

## Methods

### `decide`

```python
def decide(self, *, agent_id: str | None, provider_name: str, model_names: tuple[str, ...], temperature: float, context: ModelContext) -> ModelResponse
```

Return a normalized structured response.

### `set_trace_callbacks`

```python
def set_trace_callbacks(self, *, on_request: Any | None = None, on_response: Any | None = None) -> None
```

Attach optional adapter-boundary trace callbacks.
