"""Tests for AsyncModelDriver protocol, DriverCapabilities, and adapters."""

import asyncio

import pytest

from agent_framework.model import (
    AsyncModelDriver,
    AsyncToSyncAdapter,
    DriverCapabilities,
    ModelContext,
    ModelDriver,
    ModelResponse,
    OpenAiModelDriver,
    SyncToAsyncAdapter,
    get_driver_capabilities,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSyncDriver:
    """Minimal sync ModelDriver fake."""

    def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        return ModelResponse(payload={"kind": "final_message", "message": "sync"}, raw_text="sync")

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        pass


class FakeAsyncDriver:
    """Minimal async ModelDriver fake."""

    capabilities = DriverCapabilities(is_async=True, supports_tools=True)

    async def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        return ModelResponse(payload={"kind": "final_message", "message": "async"}, raw_text="async")

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        pass


# ---------------------------------------------------------------------------
# DriverCapabilities
# ---------------------------------------------------------------------------


class TestDriverCapabilities:
    def test_defaults(self):
        caps = DriverCapabilities()
        assert caps.is_async is False
        assert caps.supports_multimodal is False
        assert caps.supports_response_format is False
        assert caps.supports_tools is False
        assert caps.supports_streaming is False

    def test_explicit_values(self):
        caps = DriverCapabilities(is_async=True, supports_tools=True)
        assert caps.is_async is True
        assert caps.supports_tools is True
        assert caps.supports_multimodal is False


class TestGetDriverCapabilities:
    def test_returns_declared_capabilities(self):
        driver = FakeAsyncDriver()
        caps = get_driver_capabilities(driver)
        assert caps.is_async is True
        assert caps.supports_tools is True

    def test_defaults_for_legacy_driver(self):
        driver = FakeSyncDriver()
        caps = get_driver_capabilities(driver)
        assert caps == DriverCapabilities()

    def test_openai_driver_capabilities(self):
        caps = get_driver_capabilities(OpenAiModelDriver(api_key="x"))
        assert caps.is_async is False


# ---------------------------------------------------------------------------
# SyncToAsyncAdapter
# ---------------------------------------------------------------------------


class TestSyncToAsyncAdapter:
    @pytest.mark.asyncio
    async def test_decide_returns_response(self):
        adapter = SyncToAsyncAdapter(FakeSyncDriver())
        ctx = ModelContext(system_prompt="", user_prompt="")
        response = await adapter.decide(
            agent_id=None,
            provider_name="test",
            model_names=("m",),
            temperature=0.0,
            context=ctx,
        )
        assert response.raw_text == "sync"

    def test_set_trace_callbacks_delegates(self):
        called = {}
        class TracedDriver:
            def decide(self, **kwargs):
                return ModelResponse(payload={}, raw_text="")
            def set_trace_callbacks(self, *, on_request=None, on_response=None):
                called["on_request"] = on_request

        adapter = SyncToAsyncAdapter(TracedDriver())
        cb = lambda e: None
        adapter.set_trace_callbacks(on_request=cb)
        assert called["on_request"] is cb


# ---------------------------------------------------------------------------
# AsyncToSyncAdapter
# ---------------------------------------------------------------------------


class TestAsyncToSyncAdapter:
    def test_decide_wraps_async_in_sync(self):
        adapter = AsyncToSyncAdapter(FakeAsyncDriver())
        ctx = ModelContext(system_prompt="", user_prompt="")
        response = adapter.decide(
            agent_id=None,
            provider_name="test",
            model_names=("m",),
            temperature=0.0,
            context=ctx,
        )
        assert response.raw_text == "async"

    def test_set_trace_callbacks_delegates(self):
        called = {}
        class TracedAsync:
            async def decide(self, **kwargs):
                return ModelResponse(payload={}, raw_text="")
            def set_trace_callbacks(self, *, on_request=None, on_response=None):
                called["on_request"] = on_request

        adapter = AsyncToSyncAdapter(TracedAsync())
        cb = lambda e: None
        adapter.set_trace_callbacks(on_request=cb)
        assert called["on_request"] is cb
