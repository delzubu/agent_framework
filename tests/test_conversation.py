"""Tests for the conversation store protocols and InMemoryConversationStore."""

import time

import pytest

from agent_framework.conversation import (
    ConversationStore,
    InMemoryConversationStore,
)
from agent_framework.errors import ConversationNotFoundError


class TestInMemoryConversationStore:
    def test_create_and_get_messages(self):
        store = InMemoryConversationStore()
        msgs = [{"role": "user", "content": "hello"}]
        cid = store.create(msgs)
        assert isinstance(cid, str) and len(cid) > 0
        assert store.get_messages(cid) == msgs

    def test_get_messages_returns_copy(self):
        store = InMemoryConversationStore()
        msgs = [{"role": "user", "content": "hello"}]
        cid = store.create(msgs)
        retrieved = store.get_messages(cid)
        retrieved.append({"role": "system", "content": "extra"})
        assert store.get_messages(cid) == msgs  # original unmodified

    def test_append(self):
        store = InMemoryConversationStore()
        cid = store.create([{"role": "user", "content": "q"}])
        store.append(cid, [{"role": "assistant", "content": "a"}])
        msgs = store.get_messages(cid)
        assert len(msgs) == 2
        assert msgs[1]["role"] == "assistant"

    def test_get_metadata(self):
        store = InMemoryConversationStore()
        cid = store.create([], metadata={"job_id": "abc"})
        assert store.get_metadata(cid) == {"job_id": "abc"}

    def test_get_metadata_returns_copy(self):
        store = InMemoryConversationStore()
        cid = store.create([], metadata={"k": "v"})
        meta = store.get_metadata(cid)
        meta["extra"] = "x"
        assert "extra" not in store.get_metadata(cid)

    def test_delete(self):
        store = InMemoryConversationStore()
        cid = store.create([])
        store.delete(cid)
        with pytest.raises(ConversationNotFoundError):
            store.get_messages(cid)

    def test_delete_noop_for_unknown(self):
        store = InMemoryConversationStore()
        store.delete("does-not-exist")  # should not raise

    def test_not_found_raises(self):
        store = InMemoryConversationStore()
        with pytest.raises(ConversationNotFoundError):
            store.get_messages("nonexistent")
        with pytest.raises(ConversationNotFoundError):
            store.append("nonexistent", [])
        with pytest.raises(ConversationNotFoundError):
            store.get_metadata("nonexistent")

    def test_len(self):
        store = InMemoryConversationStore()
        assert len(store) == 0
        c1 = store.create([])
        c2 = store.create([])
        assert len(store) == 2
        store.delete(c1)
        assert len(store) == 1

    def test_ttl_cleanup(self):
        store = InMemoryConversationStore(ttl_seconds=0)  # expire immediately
        cid = store.create([{"role": "user", "content": "old"}])
        # Force updated_at to be in the past
        store._store[cid]["updated_at"] = time.monotonic() - 10
        deleted = store.cleanup_expired()
        assert deleted == 1
        assert len(store) == 0

    def test_cleanup_no_ttl_returns_zero(self):
        store = InMemoryConversationStore()
        store.create([])
        assert store.cleanup_expired() == 0

    def test_unique_ids(self):
        store = InMemoryConversationStore()
        ids = {store.create([]) for _ in range(100)}
        assert len(ids) == 100

    def test_satisfies_sync_protocol(self):
        """InMemoryConversationStore should structurally satisfy ConversationStore."""
        store = InMemoryConversationStore()
        assert isinstance(store, ConversationStore)

    @pytest.mark.asyncio
    async def test_async_wrappers(self):
        store = InMemoryConversationStore()
        cid = await store.acreate([{"role": "user", "content": "hi"}])
        msgs = await store.aget_messages(cid)
        assert msgs[0]["content"] == "hi"
        await store.aappend(cid, [{"role": "assistant", "content": "hey"}])
        msgs2 = await store.aget_messages(cid)
        assert len(msgs2) == 2
        meta = await store.aget_metadata(cid)
        assert isinstance(meta, dict)
        await store.adelete(cid)
        with pytest.raises(ConversationNotFoundError):
            await store.aget_messages(cid)
