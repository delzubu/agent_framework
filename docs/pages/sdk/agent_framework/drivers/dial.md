---
title: agent_framework.drivers.dial
layout: default
sdk_page: true
---


# `agent_framework.drivers.dial`

## API Summary

Async DIAL chat-completions driver for agent_framework.

DIAL uses the OpenAI-compatible chat completions API format with EPAM-specific
extensions (``custom_fields``, ``custom_content``, attachments).  This driver
handles:

- DIAL endpoint format: ``POST {base_url}/openai/deployments/{deployment}/chat/completions?api-version={version}``
- Auth: ``Api-Key`` header
- Request construction via ``aidial_sdk.chat_completion.request`` typed models
- Multimodal messages (``image_url`` content parts)
- ``response_format`` forwarding (``json_object``, ``json_schema``)
- HTTP 400 retry without ``response_format`` when the deployment doesn't support it (G-06)
- Structured ``ModelDriverError`` with HTTP status and upstream body (G-07)
- ``ProviderRequestTrace`` / ``ProviderResponseTrace`` callbacks — same mechanism as
  ``OpenAiModelDriver`` (G-14)

Install with::

    pip install agent_framework[dial]

This installs ``httpx`` and ``aidial-sdk`` as additional dependencies.

## Source

`src/agent_framework/drivers/dial.py`

## Classes

- [`DialChatCompletionsDriver`](dial/DialChatCompletionsDriver.html)
