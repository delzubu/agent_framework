---
title: SyncToAsyncAdapter
layout: default
sdk_page: true
---


# `SyncToAsyncAdapter`

Module: [`agent_framework.model`](../model.html)

## API Summary

```python
class SyncToAsyncAdapter
```

Wrap a synchronous ``ModelDriver`` for async callers.

Runs the blocking ``decide()`` call in a thread pool via
``asyncio.to_thread`` so it does not block the event loop.

## Methods

### `decide`

```python
async def decide(self, *, agent_id: str | None, provider_name: str, model_names: tuple[str, ...], temperature: float, context: ModelContext) -> ModelResponse
```

No method docstring is available yet.

### `set_trace_callbacks`

```python
def set_trace_callbacks(self, *, on_request: Any | None = None, on_response: Any | None = None) -> None
```

No method docstring is available yet.
