"""Tests for AsyncModelDriver protocol, DriverCapabilities, and adapters."""

import asyncio
from types import SimpleNamespace

import pytest

from agent_framework.drivers import OpenAiModelDriver
from agent_framework.errors import ModelDriverError
from agent_framework.model import (
    AsyncModelDriver,
    AsyncToSyncAdapter,
    DriverCapabilities,
    ModelContext,
    ModelDriver,
    ModelResponse,
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
        assert caps.supports_response_format is True


class TestOpenAiDriverTracing:
    def test_parse_error_emits_raw_response_trace_before_raise(self):
        driver = OpenAiModelDriver(api_key="x")
        seen = []

        class FakeResponse:
            output_text = "not json"

            def model_dump_json(self, indent=2):
                return '{"id":"resp_1","output_text":"not json"}'

        class FakeClient:
            def __init__(self):
                self.responses = SimpleNamespace(create=lambda **kwargs: FakeResponse())

        driver._client = FakeClient()
        driver.set_trace_callbacks(on_response=lambda event: seen.append(event))
        ctx = ModelContext(
            system_prompt="",
            user_prompt="hi",
            messages=({"role": "user", "content": "hi"},),
        )

        with pytest.raises(ModelDriverError, match="structured response is not valid JSON"):
            driver.decide(
                agent_id="a1",
                provider_name="openai",
                model_names=("gpt-4o-mini",),
                temperature=0.0,
                context=ctx,
            )

        assert len(seen) == 1
        assert seen[0].parsed_payload is None
        assert seen[0].raw_text == '{"id":"resp_1","output_text":"not json"}'


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
