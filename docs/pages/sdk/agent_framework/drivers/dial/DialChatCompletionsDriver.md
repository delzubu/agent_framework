---
title: DialChatCompletionsDriver
layout: default
sdk_page: true
---


# `DialChatCompletionsDriver`

Module: [`agent_framework.drivers.dial`](../dial.html)

## API Summary

```python
class DialChatCompletionsDriver(ModelDriverBase, _FallbackMixin)
```

Async driver for DIAL (OpenAI-compatible chat completions).

Uses ``aidial_sdk.chat_completion.request`` types for well-typed request
construction.  Uses agent_framework's standard ``ProviderRequestTrace`` /
``ProviderResponseTrace`` callbacks for tracing — dial-agent should adopt
this trace mechanism rather than its custom logging hooks.

Attributes:
    base_url: DIAL API base URL (e.g. ``https://dial.example.com``).
    deployment: Optional default deployment name.  Kept for backward
        compatibility and direct construction in tests.  In normal use the
        active deployment is taken from the ``model_names`` argument passed
        to ``decide()`` on each call.
    api_version: ``api-version`` query parameter (default ``"2024-10-21"``).
    api_key: DIAL API key sent as the ``Api-Key`` header.
    custom_fields: Optional ``custom_fields`` dict merged into the request
        body (DIAL-specific extensions).
    retry_without_response_format: If True (default), re-try once without
        ``response_format`` when DIAL returns HTTP 400.
    timeout: HTTP timeout in seconds (default 120).
    on_request_trace: Optional ``ProviderRequestTrace`` callback.
    on_response_trace: Optional ``ProviderResponseTrace`` callback.
    _fallback_state: Per-model-list fallback index map (managed by
        ``_FallbackMixin``).  Call ``reset_model_fallback()`` to restart
        from the first model.

## Attributes

- `api_key`
- `api_version`
- `base_url`
- `capabilities`
- `custom_fields`
- `deployment`
- `on_request_trace`
- `on_response_trace`
- `retry_without_response_format`
- `timeout`

## Methods

### `decide`

```python
async def decide(self, *, agent_id: str | None, provider_name: str, model_names: tuple[str, ...], temperature: float, context: ModelContext) -> ModelResponse
```

Request a structured response from a DIAL deployment.

Tries each model in ``model_names`` in order, starting from the last
known-good index.  The active deployment name for each attempt is taken
directly from the model list; ``self.deployment`` is not used.

### `set_trace_callbacks`

```python
def set_trace_callbacks(self, *, on_request: Any | None = None, on_response: Any | None = None) -> None
```

Attach optional trace callbacks for provider I/O logging.

### `aclose`

```python
async def aclose(self) -> None
```

Release the underlying ``httpx.AsyncClient``.
