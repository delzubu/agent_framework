---
title: agent_framework.conversation
layout: default
sdk_page: true
---


# `agent_framework.conversation`

## API Summary

Conversation store abstractions for resumable multi-turn LLM sessions.

This module provides a storage-agnostic add-on for multi-turn conversation
management.  When no conversation store is configured, agent_framework works
exactly as before (single-run, stateless).  The store is activated by:

1. Attaching an implementation to ``AgentHost.conversation_store``.
2. Passing a ``conversation_id`` to ``AgentHost.complete()`` or
   ``AgentHost.complete_async()``.

The framework defines the protocol; storage implementations (in-memory,
Redis, database) satisfy it structurally without any inheritance.

Protocols
---------
``ConversationStore``
    Synchronous protocol.  Suitable for in-process stores.
``AsyncConversationStore``
    Async protocol.  Use for stores that perform I/O (Redis, DB).

Reference implementation
------------------------
``InMemoryConversationStore``
    Thread-safe in-memory store with optional TTL.  Satisfies the sync
    protocol.  Async wrappers (``acreate``, ``aget_messages``, etc.) are
    provided so it can also be used by async callers.

Implementing an out-of-process store
-------------------------------------
Implement the ``AsyncConversationStore`` protocol::

    class RedisConversationStore:
        async def create(self, messages, *, metadata=None) -> str: ...
        async def get_messages(self, conversation_id) -> list[dict]: ...
        async def append(self, conversation_id, messages) -> None: ...
        async def get_metadata(self, conversation_id) -> dict: ...
        async def delete(self, conversation_id) -> None: ...

No inheritance or registration required — structural typing applies.

## Source

`src/agent_framework/conversation.py`

## Classes

- [`ConversationStore`](conversation/ConversationStore.html)
- [`AsyncConversationStore`](conversation/AsyncConversationStore.html)
- [`InMemoryConversationStore`](conversation/InMemoryConversationStore.html)
