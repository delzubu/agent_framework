# Conversation Model

> This document is part of the `agent_framework` architecture reference.
> See also: [Overview](./overview.md) · [Host & Orchestration](./host-orchestration.md) · [Drivers](./drivers.md) · [Memory System](./memory-system.md)

---

## 1. Design Rationale

The conversation store is an **opt-in add-on**. When not configured, the framework works exactly as it did before — no multi-turn state is maintained between calls. When configured, it enables stateful multi-turn workflows where the full conversation history is automatically loaded before each model call and saved after.

This is separate from the framework memory subsystem. The conversation store persists ordered chat messages by `conversation_id`; memory stores scoped resources by `mem://...` URI for shared agent access, large parameter normalization, and deterministic prompt projection.

Key design constraints:
- **Storage-agnostic**: The `ConversationStore` and `AsyncConversationStore` are `typing.Protocol` classes. In-process (in-memory dict), out-of-process (Redis, database), and hybrid implementations all satisfy the same interface.
- **No HTTP protocol implied**: The protocol is defined in terms of Python method calls, not HTTP verbs or REST endpoints. An implementation could back the store with anything.
- **Additive**: `AgentHost.complete()` and `complete_async()` only interact with the store when a `conversation_id` is provided. The store is never required.

---

## 2. Protocol Reference

### 2.1 `ConversationStore` — Synchronous

```python
class ConversationStore(Protocol):
    def create(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str: ...
    """Create a new conversation, return its ID."""

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]: ...
    """Return a copy of the stored messages list."""

    def append(
        self,
        conversation_id: str,
        messages: Sequence[dict[str, Any]],
    ) -> None: ...
    """Append messages to an existing conversation."""

    def get_metadata(self, conversation_id: str) -> dict[str, Any]: ...
    """Return a copy of the conversation metadata dict."""

    def delete(self, conversation_id: str) -> None: ...
    """Delete a conversation. No-op if not found."""
```

`ConversationStore` is `@runtime_checkable`, so `isinstance(store, ConversationStore)` works for structural type checking.

Raises `ConversationNotFoundError(KeyError)` for `get_messages`, `append`, and `get_metadata` when the `conversation_id` does not exist.

### 2.2 `AsyncConversationStore` — Asynchronous

Identical structure to `ConversationStore`, all methods are `async`:

```python
class AsyncConversationStore(Protocol):
    async def create(self, messages, *, metadata=None) -> str: ...
    async def get_messages(self, conversation_id) -> list[dict]: ...
    async def append(self, conversation_id, messages) -> None: ...
    async def get_metadata(self, conversation_id) -> dict: ...
    async def delete(self, conversation_id) -> None: ...
```

---

## 3. `InMemoryConversationStore` — Reference Implementation

```python
from agent_framework.conversation import InMemoryConversationStore

store = InMemoryConversationStore(ttl_seconds=3600)  # optional TTL in seconds
```

**Satisfies:** `ConversationStore` (structural) and provides async wrappers for `AsyncConversationStore`.

**Features:**
- Thread-safe (uses `threading.Lock` for all mutations)
- Unique IDs generated with `uuid4`
- Returns **copies** of messages and metadata — external mutation does not affect stored state
- Optional TTL: `cleanup_expired()` deletes conversations not updated within `ttl_seconds`
- `len(store)` returns the number of active conversations

### 3.1 Sync API

```python
# Create
cid = store.create([{"role": "system", "content": "Be helpful."}])
cid = store.create([], metadata={"job_id": "abc", "user": "alice"})

# Read
msgs = store.get_messages(cid)      # list copy
meta = store.get_metadata(cid)      # dict copy

# Write
store.append(cid, [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello"},
])

# Delete
store.delete(cid)           # no-op if not found

# Cleanup (for long-running services)
n_deleted = store.cleanup_expired()
```

### 3.2 Async Wrappers

`InMemoryConversationStore` also implements async aliases so it can be used with `AsyncConversationStore`-expecting code:

```python
cid = await store.acreate([...])
msgs = await store.aget_messages(cid)
await store.aappend(cid, [...])
meta = await store.aget_metadata(cid)
await store.adelete(cid)
```

These are thin wrappers — they call the sync methods directly (in-memory operations are non-blocking).

### 3.3 TTL and Cleanup

```python
store = InMemoryConversationStore(ttl_seconds=1800)  # 30 minutes

# ... after some time ...
n = store.cleanup_expired()  # removes conversations idle > 30 min
```

`cleanup_expired()` checks `updated_at` timestamps and deletes conversations that haven't been accessed within `ttl_seconds`. Returns the count of deleted conversations. No-op if `ttl_seconds` is `None` (the default).

---

## 4. Integration with `AgentHost`

When `conversation_store` is set on the host, `complete()` and `complete_async()` integrate automatically:

```python
host = AgentHost.create(
    model_driver=driver,
    conversation_store=InMemoryConversationStore(ttl_seconds=3600),
)

# Create a conversation
cid = host.conversation_store.create([
    {"role": "system", "content": "You are a helpful assistant."},
])

# First turn
result = await host.complete_async(
    messages=[{"role": "user", "content": "What is the capital of France?"}],
    conversation_id=cid,
)
# Store now has: system + user + assistant (3 messages)

# Second turn — full history is automatically loaded
result = await host.complete_async(
    messages=[{"role": "user", "content": "And Germany?"}],
    conversation_id=cid,
)
# Store now has: system + user + assistant + user + assistant (5 messages)
```

**What happens on each `complete()` / `complete_async()` call when `conversation_id` is provided:**

1. Load existing messages from store (`get_messages` / `aget_messages`)
2. Prepend them to the caller-supplied messages list
3. Call the model with the full history
4. Append `{"role": "assistant", "content": result.raw_text}` to the store
5. Return `ModelResponse`

If `conversation_id` is not found in the store, `ConversationNotFoundError` is raised.

---

## 5. Implementing a Custom Store

### 5.1 In-Process (sync, non-TTL)

```python
class RedisConversationStore:
    def __init__(self, client: redis.Redis, prefix: str = "conv:"):
        self._r = client
        self._prefix = prefix

    def create(self, messages, *, metadata=None) -> str:
        cid = str(uuid4())
        self._r.set(f"{self._prefix}{cid}:messages", json.dumps(messages))
        self._r.set(f"{self._prefix}{cid}:metadata", json.dumps(metadata or {}))
        return cid

    def get_messages(self, conversation_id) -> list:
        raw = self._r.get(f"{self._prefix}{conversation_id}:messages")
        if raw is None:
            raise ConversationNotFoundError(conversation_id)
        return json.loads(raw)

    def append(self, conversation_id, messages) -> None:
        existing = self.get_messages(conversation_id)  # raises if not found
        existing.extend(messages)
        self._r.set(f"{self._prefix}{conversation_id}:messages", json.dumps(existing))

    def get_metadata(self, conversation_id) -> dict:
        raw = self._r.get(f"{self._prefix}{conversation_id}:metadata")
        if raw is None:
            raise ConversationNotFoundError(conversation_id)
        return json.loads(raw)

    def delete(self, conversation_id) -> None:
        self._r.delete(
            f"{self._prefix}{conversation_id}:messages",
            f"{self._prefix}{conversation_id}:metadata",
        )
```

### 5.2 Async (aioredis)

Implement the `AsyncConversationStore` protocol directly with `async def` methods. No base class needed.

### 5.3 Error Handling

Always raise `ConversationNotFoundError(conversation_id)` from `get_messages`, `append`, and `get_metadata` when the conversation does not exist. This is a `KeyError` subclass — callers can `except ConversationNotFoundError` or `except KeyError`.

```python
from agent_framework.errors import ConversationNotFoundError

raise ConversationNotFoundError(conversation_id)
```
