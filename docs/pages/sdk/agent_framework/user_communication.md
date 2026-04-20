---
title: agent_framework.user_communication
layout: default
sdk_page: true
---


# `agent_framework.user_communication`

## API Summary

User communication abstraction for AgentHost.

Defines the async Protocol that all concrete user-communication implementations
must satisfy, plus data types for permission gating and a no-op implementation
suitable for headless/test use. For browser-driven runs, see
:class:`agent_framework.web_communication.WebUserCommunication`, which queues
outbound UI messages and resolves input asynchronously via
``submit_user_input``.

## Source

`src/agent_framework/user_communication.py`

## Classes

- [`PermissionDecision`](user_communication/PermissionDecision.html)
- [`PermissionRequest`](user_communication/PermissionRequest.html)
- [`UserCommunication`](user_communication/UserCommunication.html)
- [`NullUserCommunication`](user_communication/NullUserCommunication.html)
