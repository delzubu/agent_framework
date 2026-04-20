---
title: ModelResponse
layout: default
sdk_page: true
---


# `ModelResponse`

Module: [`agent_framework.model`](../model.html)

## API Summary

```python
class ModelResponse
```

Normalized model response returned by a `ModelDriver`.

Attributes:
    payload: Parsed structured payload consumed by the agent runtime.
    raw_text: Original model text before runtime normalization.
    tool_calls: Tool calls requested by the model (chat completions
        drivers), or None if not applicable.
    finish_reason: Stop reason reported by the provider (e.g. ``"stop"``,
        ``"tool_calls"``, ``"length"``).
    usage: Token usage reported by the provider, keyed by
        ``"prompt_tokens"``, ``"completion_tokens"``, etc.

## Attributes

- `finish_reason`
- `payload`
- `raw_text`
- `tool_calls`
- `usage`
