---
title: AsyncConversationStore
layout: default
sdk_page: true
---


# `AsyncConversationStore`

Module: [`agent_framework.conversation`](../conversation.html)

## API Summary

```python
class AsyncConversationStore(Protocol)
```

Async conversation persistence protocol.

Use for stores that perform I/O (Redis, database).  Mirrors
``ConversationStore`` with ``async def`` methods.

## Methods

### `create`

```python
async def create(self, messages: Sequence[dict[str, Any]], *, metadata: dict[str, Any] | None = None) -> str
```

Create a new conversation and return its id.

### `get_messages`

```python
async def get_messages(self, conversation_id: str) -> list[dict[str, Any]]
```

Return all messages for a conversation.

Raises:
    ConversationNotFoundError: If the id is unknown.

### `append`

```python
async def append(self, conversation_id: str, messages: Sequence[dict[str, Any]]) -> None
```

Append messages to an existing conversation.

Raises:
    ConversationNotFoundError: If the id is unknown.

### `get_metadata`

```python
async def get_metadata(self, conversation_id: str) -> dict[str, Any]
```

Return the metadata dict for a conversation.

Raises:
    ConversationNotFoundError: If the id is unknown.

### `delete`

```python
async def delete(self, conversation_id: str) -> None
```

Delete a conversation.  No-op if the id is unknown.
