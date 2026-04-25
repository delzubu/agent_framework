"""Tests for AsyncModelDriver protocol, DriverCapabilities, and adapters."""

from types import SimpleNamespace

import pytest

from agent_framework.drivers import OpenAiModelDriver
from agent_framework.errors import ModelDriverError
from agent_framework.model import (
    AsyncToSyncAdapter,
    DriverCapabilities,
    ModelContext,
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
    def test_decide_normalizes_usage_and_preserves_raw_usage(self):
        driver = OpenAiModelDriver(api_key="x")

        class FakeUsageDetails:
            def __init__(self, cached_tokens):
                self.cached_tokens = cached_tokens

            def model_dump(self):
                return {"cached_tokens": self.cached_tokens}

        class FakeUsage:
            input_tokens = 120
            output_tokens = 45
            total_tokens = 165
            input_tokens_details = FakeUsageDetails(80)
            output_tokens_details = FakeUsageDetails(0)

            def model_dump(self):
                return {
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "total_tokens": self.total_tokens,
                    "input_tokens_details": {"cached_tokens": 80},
                    "output_tokens_details": {"cached_tokens": 0},
                }

        class FakeResponse:
            output_text = '{"kind":"final_message","message":"ok"}'
            usage = FakeUsage()

            def model_dump_json(self, indent=2):
                return '{"id":"resp_1","output_text":"{\\"kind\\":\\"final_message\\",\\"message\\":\\"ok\\"}"}'

        class FakeClient:
            def __init__(self):
                self.responses = SimpleNamespace(create=lambda **kwargs: FakeResponse())

        driver._client = FakeClient()
        ctx = ModelContext(
            system_prompt="",
            user_prompt="hi",
            messages=({"role": "user", "content": "hi"},),
        )

        result = driver.decide(
            agent_id="a1",
            provider_name="openai",
            model_names=("gpt-4o-mini",),
            temperature=0.0,
            context=ctx,
        )

        assert result.usage is not None
        assert result.usage.to_dict() == {
            "input_tokens": 120,
            "input_cached_tokens": 80,
            "output_tokens": 45,
            "output_cached_tokens": 0,
            "total_tokens": 165,
        }
        assert result.raw_usage == {
            "input_tokens": 120,
            "output_tokens": 45,
            "total_tokens": 165,
            "input_tokens_details": {"cached_tokens": 80},
            "output_tokens_details": {"cached_tokens": 0},
        }

    def test_parse_error_emits_raw_response_trace_before_raise(self):
        driver = OpenAiModelDriver(api_key="x")
        seen = []

        class FakeUsage:
            def model_dump(self):
                return {
                    "input_tokens": 12,
                    "output_tokens": 0,
                    "total_tokens": 12,
                    "input_tokens_details": {"cached_tokens": 4},
                }

        class FakeResponse:
            output_text = "not json"
            usage = FakeUsage()

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
        assert seen[0].usage is not None
        assert seen[0].usage.to_dict() == {
            "input_tokens": 12,
            "input_cached_tokens": 4,
            "output_tokens": 0,
            "output_cached_tokens": 0,
            "total_tokens": 12,
        }
        assert seen[0].raw_usage == {
            "input_tokens": 12,
            "output_tokens": 0,
            "total_tokens": 12,
            "input_tokens_details": {"cached_tokens": 4},
        }


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
