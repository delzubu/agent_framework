---
title: ModelContext
layout: default
sdk_page: true
---


# `ModelContext`

Module: [`agent_framework.model`](../model.html)

## API Summary

```python
class ModelContext
```

Model-facing prompt payload assembled for a single decision step.

Attributes:
    system_prompt: After :func:`merge_runtime_system_into_messages`, the full
        assembled system text (agent block plus runtime templates). Before
        merge, agent flows use the agent definition only; headless
        ``complete()`` may use ``""`` until merge runs.
    user_prompt: Rendered invocation prompt plus dynamic augmentations.
    messages: Structured conversation history for providers that support
        message-array inputs.
    response_mode: Runtime-level response contract for this model call
        (default: :data:`DEFAULT_RESPONSE_MODE`).
    exact_input_payload: Exact provider-native input payload. When present,
        the adapter must forward it unchanged instead of composing prompt
        messages from the other context fields.
    tools: Tools available to the model for this decision step.
    subagents: Subagents available to the model for this decision step.
    skills: Skills or other future capabilities available to the model.

## Attributes

- `exact_input_payload`
- `messages`
- `response_format`
- `response_mode`
- `run_id`
- `skills`
- `subagents`
- `system_prompt`
- `tools`
- `user_prompt`
