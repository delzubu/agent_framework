---
title: AsyncModelDriver
layout: default
sdk_page: true
---


# `AsyncModelDriver`

Module: [`agent_framework.model`](../model.html)

## API Summary

```python
class AsyncModelDriver(Protocol)
```

Provider-agnostic protocol for an async single agent decision step.

Implement this protocol for drivers that run on an ``asyncio`` event loop
(e.g. DIAL, Anthropic).  The sync ``ModelDriver`` protocol continues to
work unchanged; use ``SyncToAsyncAdapter`` or ``AsyncToSyncAdapter`` to
bridge between the two when needed.

## Methods

### `decide`

```python
async def decide(self, *, agent_id: str | None, provider_name: str, model_names: tuple[str, ...], temperature: float, context: ModelContext) -> ModelResponse
```

Return a normalized structured response (coroutine).

### `set_trace_callbacks`

```python
def set_trace_callbacks(self, *, on_request: Any | None = None, on_response: Any | None = None) -> None
```

Attach optional adapter-boundary trace callbacks.
