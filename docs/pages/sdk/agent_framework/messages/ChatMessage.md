---
title: ChatMessage
layout: default
sdk_page: true
---


# `ChatMessage`

Module: [`agent_framework.messages`](../messages.html)

## API Summary

```python
class ChatMessage
```

A single chat message in the OpenAI chat completions format.

Supports text-only, multimodal, assistant tool calls, and tool result
messages. Serializes to / deserializes from plain dicts for use with
``ModelContext.messages``.

Attributes:
    role: Message role — ``"system"``, ``"user"``, ``"assistant"``, or
        ``"tool"``.
    content: String content, a tuple of ``ContentPart`` objects for
        multimodal messages, or ``None`` for assistant messages that only
        contain tool calls.
    name: Optional name for the participant (some providers use this).
    tool_call_id: Tool call id for ``role="tool"`` result messages.
    tool_calls: Tool calls emitted by an assistant message.

## Attributes

- `content`
- `name`
- `role`
- `tool_call_id`
- `tool_calls`

## Methods

### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

No method docstring is available yet.

### `from_dict`

```python
def from_dict(cls, data: dict[str, Any]) -> ChatMessage
```

No method docstring is available yet.
