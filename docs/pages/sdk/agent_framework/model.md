---
title: agent_framework.model
layout: default
sdk_page: true
---


# `agent_framework.model`

## API Summary

Model driver abstractions and provider-backed implementations.

The runtime depends on the `ModelDriver` protocol instead of any provider SDK
types so agents can remain SDK-agnostic and tests can inject deterministic
fakes.

## Source

`src/agent_framework/model.py`

## Classes

- [`DriverCapabilities`](model/DriverCapabilities.html)
- [`ModelDriverBase`](model/ModelDriverBase.html)
- [`CapabilityParameter`](model/CapabilityParameter.html)
- [`CapabilityDefinition`](model/CapabilityDefinition.html)
- [`ModelContext`](model/ModelContext.html)
- [`ModelResponse`](model/ModelResponse.html)
- [`ProviderRequestTrace`](model/ProviderRequestTrace.html)
- [`ProviderResponseTrace`](model/ProviderResponseTrace.html)
- [`ModelDriver`](model/ModelDriver.html)
- [`AsyncModelDriver`](model/AsyncModelDriver.html)
- [`SyncToAsyncAdapter`](model/SyncToAsyncAdapter.html)
- [`AsyncToSyncAdapter`](model/AsyncToSyncAdapter.html)
- [`OpenAiModelDriver`](model/OpenAiModelDriver.html)

## Functions

### `parse_json_object_model_output`

```python
def parse_json_object_model_output(raw_text: str, *, provider_label: str) -> tuple[dict[str, Any], str]
```

Parse assistant text as one JSON object for structured / ``json_object`` modes.

Used by **all** model drivers: applies fence stripping (via
``agent_framework.validation._normalize_json_text``), :func:`json.loads`, and
requires a JSON **object** at the top level. Invalid text or non-objects
raise :class:`~agent_framework.errors.ModelDriverError` with a short preview
and an ``upstream_body`` excerpt for tracing.

``response_mode == "text"`` bypasses this at the call site and does not
invoke this function.

### `get_driver_capabilities`

```python
def get_driver_capabilities(driver: Any) -> DriverCapabilities
```

Return the declared capabilities of a driver.

Falls back to conservative defaults for legacy drivers that pre-date the
capability contract.

### `build_skills_catalog`

```python
def build_skills_catalog(skills: 'tuple[CapabilityDefinition, ...]', max_tokens: int = 2000) -> str
```

Return a formatted skills catalog string, or empty string if no skills.

Skills are sorted by priority descending (highest first). When the catalog
exceeds ``max_tokens`` (estimated as ``len(text) // 4``), the lowest-priority
skill is dropped and the catalog is rebuilt. At least one skill is always
kept.

### `runtime_prompt_source_paths`

```python
def runtime_prompt_source_paths(response_mode: str) -> tuple[Path, ...]
```

Return the system prompt source files used for the given response mode.

### `assemble_system_prompt`

```python
def assemble_system_prompt(context: 'ModelContext') -> str
```

Return the full system prompt assembled for a provider call.

### `merge_runtime_system_into_messages`

```python
def merge_runtime_system_into_messages(context: 'ModelContext') -> 'ModelContext'
```

Merge runtime templates into the first system message and align ``system_prompt``.

Call from :meth:`Agent.build_context` and :meth:`AgentHost.complete` so every
driver receives identical ``ModelContext.messages`` (unless
``exact_input_payload`` bypasses normal assembly in :meth:`decide`).

### `resolved_response_format_dict`

```python
def resolved_response_format_dict(context: ModelContext) -> dict[str, Any] | None
```

Return the effective chat-completions-style ``response_format`` dict.

Drivers should use this (or :meth:`_FallbackMixin.resolved_response_format_dict`)
to populate provider-native JSON mode when the caller omitted
``context.response_format``.

* ``response_mode == "text"`` → ``None`` (plain text at the API).
* Explicit ``context.response_format`` → shallow copy (non-text modes only;
  text mode still forces ``None``).
* ``response_mode == DEFAULT_RESPONSE_MODE`` with no explicit format →
  ``{"type": "json_object"}``.
* Other modes (e.g. ``"decision"``) → ``None`` unless the caller set
  ``response_format``.

### `openai_responses_text_format_field`

```python
def openai_responses_text_format_field(fmt: dict[str, Any]) -> dict[str, Any]
```

Map a chat-completions-style ``response_format`` dict to OpenAI Responses ``text.format``.

Accepts ``{"type": "json_object"}`` or nested ``json_schema`` payloads
(same shape as :func:`resolved_response_format_dict` / DIAL). Unknown shapes
are shallow-copied.
