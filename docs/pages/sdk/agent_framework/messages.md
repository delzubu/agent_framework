---
title: agent_framework.messages
layout: default
sdk_page: true
---


# `agent_framework.messages`

## API Summary

Typed chat message model aligned with the OpenAI chat completions spec.

These types provide type-safe construction of chat messages, including
multimodal content (text + image_url), tool calls, and tool results. They
serialize to and from plain dicts so they are compatible with
``ModelContext.messages``.

Usage::

    from agent_framework.messages import ChatMessage, ContentPart, ImageUrl

    msg = ChatMessage(
        role="user",
        content=(
            ContentPart(type="text", text="Describe this image:"),
            ContentPart(type="image_url", image_url=ImageUrl(url="data:image/png;base64,...")),
        ),
    )
    # Pass to ModelContext.messages as a plain dict:
    context = ModelContext(..., messages=(msg.to_dict(),))

## Source

`src/agent_framework/messages.py`

## Classes

- [`ImageUrl`](messages/ImageUrl.html)
- [`ContentPart`](messages/ContentPart.html)
- [`FunctionCall`](messages/FunctionCall.html)
- [`ToolCallMessage`](messages/ToolCallMessage.html)
- [`ChatMessage`](messages/ChatMessage.html)
