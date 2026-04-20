---
title: AsyncToSyncAdapter
layout: default
sdk_page: true
---


# `AsyncToSyncAdapter`

Module: [`agent_framework.model`](../model.html)

## API Summary

```python
class AsyncToSyncAdapter
```

Wrap an ``AsyncModelDriver`` for synchronous callers.

Used by the existing sync agent loop when a caller configures an async
driver (e.g. ``DialChatCompletionsDriver``) and then runs a markdown-
defined agent via ``AgentHost.run_agent()``.  Uses ``asyncio.run()`` if no
event loop is running, or ``asyncio.get_event_loop().run_until_complete()``
as a fallback.

## Methods

### `decide`

```python
def decide(self, *, agent_id: str | None, provider_name: str, model_names: tuple[str, ...], temperature: float, context: ModelContext) -> ModelResponse
```

No method docstring is available yet.

### `set_trace_callbacks`

```python
def set_trace_callbacks(self, *, on_request: Any | None = None, on_response: Any | None = None) -> None
```

No method docstring is available yet.
