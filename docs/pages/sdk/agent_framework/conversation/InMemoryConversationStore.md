---
title: InMemoryConversationStore
layout: default
sdk_page: true
---


# `InMemoryConversationStore`

Module: [`agent_framework.conversation`](../conversation.html)

## API Summary

```python
class InMemoryConversationStore
```

Thread-safe in-memory conversation store with optional TTL.

Satisfies the synchronous ``ConversationStore`` protocol.  Also exposes
async counterparts (``acreate``, ``aget_messages``, ``aappend``,
``aget_metadata``, ``adelete``) for async callers, since in-memory
operations are non-blocking.

Args:
    ttl_seconds: If set, conversations expire after this many seconds of
        inactivity (measured from last write).  ``cleanup_expired()``
        must be called periodically to reclaim memory.

Example::

    store = InMemoryConversationStore(ttl_seconds=3600)
    cid = store.create([{"role": "user", "content": "Hello"}])
    store.append(cid, [{"role": "assistant", "content": "Hi!"}])
    msgs = store.get_messages(cid)

## Methods

### `create`

```python
def create(self, messages: Sequence[dict[str, Any]], *, metadata: dict[str, Any] | None = None) -> str
```

Create a new conversation and return its id.

### `get_messages`

```python
def get_messages(self, conversation_id: str) -> list[dict[str, Any]]
```

Return a copy of all messages for a conversation.

### `append`

```python
def append(self, conversation_id: str, messages: Sequence[dict[str, Any]]) -> None
```

Append messages to an existing conversation.

### `get_metadata`

```python
def get_metadata(self, conversation_id: str) -> dict[str, Any]
```

Return a copy of the metadata for a conversation.

### `delete`

```python
def delete(self, conversation_id: str) -> None
```

Delete a conversation.  No-op if the id is unknown.

### `cleanup_expired`

```python
def cleanup_expired(self) -> int
```

Remove expired conversations.  Returns the number deleted.

### `acreate`

```python
async def acreate(self, messages: Sequence[dict[str, Any]], *, metadata: dict[str, Any] | None = None) -> str
```

No method docstring is available yet.

### `aget_messages`

```python
async def aget_messages(self, conversation_id: str) -> list[dict[str, Any]]
```

No method docstring is available yet.

### `aappend`

```python
async def aappend(self, conversation_id: str, messages: Sequence[dict[str, Any]]) -> None
```

No method docstring is available yet.

### `aget_metadata`

```python
async def aget_metadata(self, conversation_id: str) -> dict[str, Any]
```

No method docstring is available yet.

### `adelete`

```python
async def adelete(self, conversation_id: str) -> None
```

No method docstring is available yet.
