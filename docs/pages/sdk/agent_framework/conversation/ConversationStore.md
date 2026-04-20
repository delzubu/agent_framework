---
title: ConversationStore
layout: default
sdk_page: true
---


# `ConversationStore`

Module: [`agent_framework.conversation`](../conversation.html)

## API Summary

```python
class ConversationStore(Protocol)
```

Synchronous conversation persistence protocol.

Implementations are storage-agnostic: the framework defines the
operations, not how messages are persisted.

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

Return all messages for a conversation.

Raises:
    ConversationNotFoundError: If the id is unknown.

### `append`

```python
def append(self, conversation_id: str, messages: Sequence[dict[str, Any]]) -> None
```

Append messages to an existing conversation.

Raises:
    ConversationNotFoundError: If the id is unknown.

### `get_metadata`

```python
def get_metadata(self, conversation_id: str) -> dict[str, Any]
```

Return the metadata dict for a conversation.

Raises:
    ConversationNotFoundError: If the id is unknown.

### `delete`

```python
def delete(self, conversation_id: str) -> None
```

Delete a conversation.  No-op if the id is unknown.
