"""Conversation store abstractions for resumable multi-turn LLM sessions.

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
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol, Sequence, runtime_checkable
from uuid import uuid4

from agent_framework.errors import ConversationNotFoundError


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class ConversationStore(Protocol):
    """Synchronous conversation persistence protocol.

    Implementations are storage-agnostic: the framework defines the
    operations, not how messages are persisted.
    """

    def create(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a new conversation and return its id."""

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return all messages for a conversation.

        Raises:
            ConversationNotFoundError: If the id is unknown.
        """

    def append(
        self,
        conversation_id: str,
        messages: Sequence[dict[str, Any]],
    ) -> None:
        """Append messages to an existing conversation.

        Raises:
            ConversationNotFoundError: If the id is unknown.
        """

    def get_metadata(self, conversation_id: str) -> dict[str, Any]:
        """Return the metadata dict for a conversation.

        Raises:
            ConversationNotFoundError: If the id is unknown.
        """

    def delete(self, conversation_id: str) -> None:
        """Delete a conversation.  No-op if the id is unknown."""


@runtime_checkable
class AsyncConversationStore(Protocol):
    """Async conversation persistence protocol.

    Use for stores that perform I/O (Redis, database).  Mirrors
    ``ConversationStore`` with ``async def`` methods.
    """

    async def create(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a new conversation and return its id."""

    async def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return all messages for a conversation.

        Raises:
            ConversationNotFoundError: If the id is unknown.
        """

    async def append(
        self,
        conversation_id: str,
        messages: Sequence[dict[str, Any]],
    ) -> None:
        """Append messages to an existing conversation.

        Raises:
            ConversationNotFoundError: If the id is unknown.
        """

    async def get_metadata(self, conversation_id: str) -> dict[str, Any]:
        """Return the metadata dict for a conversation.

        Raises:
            ConversationNotFoundError: If the id is unknown.
        """

    async def delete(self, conversation_id: str) -> None:
        """Delete a conversation.  No-op if the id is unknown."""


# ---------------------------------------------------------------------------
# Reference implementation
# ---------------------------------------------------------------------------


class InMemoryConversationStore:
    """Thread-safe in-memory conversation store with optional TTL.

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
    """

    def __init__(self, *, ttl_seconds: int | None = None) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        # {conversation_id: {"messages": [...], "metadata": {...}, "updated_at": float}}
        self._store: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Sync protocol
    # ------------------------------------------------------------------

    def create(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a new conversation and return its id."""
        cid = str(uuid4())
        with self._lock:
            self._store[cid] = {
                "messages": list(messages),
                "metadata": dict(metadata or {}),
                "updated_at": time.monotonic(),
            }
        return cid

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return a copy of all messages for a conversation."""
        with self._lock:
            entry = self._store.get(conversation_id)
        if entry is None:
            raise ConversationNotFoundError(conversation_id)
        return list(entry["messages"])

    def append(
        self,
        conversation_id: str,
        messages: Sequence[dict[str, Any]],
    ) -> None:
        """Append messages to an existing conversation."""
        with self._lock:
            entry = self._store.get(conversation_id)
            if entry is None:
                raise ConversationNotFoundError(conversation_id)
            entry["messages"].extend(messages)
            entry["updated_at"] = time.monotonic()

    def get_metadata(self, conversation_id: str) -> dict[str, Any]:
        """Return a copy of the metadata for a conversation."""
        with self._lock:
            entry = self._store.get(conversation_id)
        if entry is None:
            raise ConversationNotFoundError(conversation_id)
        return dict(entry["metadata"])

    def delete(self, conversation_id: str) -> None:
        """Delete a conversation.  No-op if the id is unknown."""
        with self._lock:
            self._store.pop(conversation_id, None)

    def cleanup_expired(self) -> int:
        """Remove expired conversations.  Returns the number deleted."""
        if self._ttl is None:
            return 0
        cutoff = time.monotonic() - self._ttl
        with self._lock:
            expired = [cid for cid, e in self._store.items() if e["updated_at"] < cutoff]
            for cid in expired:
                del self._store[cid]
        return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    # ------------------------------------------------------------------
    # Async wrappers (non-blocking — delegate to sync methods)
    # ------------------------------------------------------------------

    async def acreate(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self.create(messages, metadata=metadata)

    async def aget_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        return self.get_messages(conversation_id)

    async def aappend(
        self,
        conversation_id: str,
        messages: Sequence[dict[str, Any]],
    ) -> None:
        self.append(conversation_id, messages)

    async def aget_metadata(self, conversation_id: str) -> dict[str, Any]:
        return self.get_metadata(conversation_id)

    async def adelete(self, conversation_id: str) -> None:
        self.delete(conversation_id)


__all__ = [
    "AsyncConversationStore",
    "ConversationStore",
    "InMemoryConversationStore",
]
