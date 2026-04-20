---
title: OpenAiModelDriver
layout: default
sdk_page: true
---


# `OpenAiModelDriver`

Module: [`agent_framework.model`](../model.html)

## API Summary

```python
class OpenAiModelDriver(ModelDriverBase, _FallbackMixin)
```

OpenAI-backed model driver for the first draft runtime.

Attributes:
    api_key: API key used to construct the OpenAI client lazily per call.
    _fallback_state: Per-model-list fallback index map (managed by
        ``_FallbackMixin``).  Call ``reset_model_fallback()`` to restart
        from the first model.

## Attributes

- `api_key`
- `capabilities`
- `on_request_trace`
- `on_response_trace`

## Methods

### `set_trace_callbacks`

```python
def set_trace_callbacks(self, *, on_request: Any | None = None, on_response: Any | None = None) -> None
```

Attach optional trace callbacks for exact provider I/O logging.

### `decide`

```python
def decide(self, *, agent_id: str | None, provider_name: str, model_names: tuple[str, ...], temperature: float, context: ModelContext) -> ModelResponse
```

Request a structured decision from the OpenAI Responses API.

Tries each model in ``model_names`` in order, starting from the last
known-good index.  Falls back to the next model on any error.
