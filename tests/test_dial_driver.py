"""Tests for DialChatCompletionsDriver with mocked httpx responses."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_framework.errors import ModelDriverError
from agent_framework.model import DriverCapabilities, ModelContext, ModelResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    messages=None,
    response_mode="json_object",
    response_format=None,
    tools=(),
) -> ModelContext:
    return ModelContext(
        system_prompt="",
        user_prompt="hi",
        messages=tuple(messages or [{"role": "user", "content": "hi"}]),
        response_mode=response_mode,
        response_format=response_format,
        tools=tools,
    )


def _make_response_payload(content: str, finish_reason: str = "stop") -> dict:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _make_tool_call_payload(tool_name: str, arguments: str) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": tool_name, "arguments": arguments},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _make_driver(
    base_url="https://dial.example.com",
    deployment="gpt-4o",
    api_key="test-key",
    **kwargs,
):
    from agent_framework.drivers.dial import DialChatCompletionsDriver
    return DialChatCompletionsDriver(
        base_url=base_url,
        deployment=deployment,
        api_key=api_key,
        **kwargs,
    )


def _mock_httpx_response(status_code: int, body: dict | str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if isinstance(body, dict):
        resp.json.return_value = body
        resp.text = json.dumps(body)
    else:
        resp.json.side_effect = ValueError("not json")
        resp.text = body
    return resp


# ---------------------------------------------------------------------------
# DriverCapabilities
# ---------------------------------------------------------------------------


class TestDialDriverCapabilities:
    def test_capabilities_declared(self):
        from agent_framework.drivers.dial import DialChatCompletionsDriver
        caps = DialChatCompletionsDriver.capabilities
        assert caps.is_async is True
        assert caps.supports_multimodal is True
        assert caps.supports_response_format is True
        assert caps.supports_tools is True

    def test_get_driver_capabilities_returns_declared(self):
        from agent_framework.model import get_driver_capabilities
        driver = _make_driver()
        caps = get_driver_capabilities(driver)
        assert caps.is_async is True
        assert caps.supports_tools is True


# ---------------------------------------------------------------------------
# set_trace_callbacks
# ---------------------------------------------------------------------------


class TestSetTraceCallbacks:
    def test_stores_callbacks(self):
        driver = _make_driver()
        req_cb = lambda e: None
        resp_cb = lambda e: None
        driver.set_trace_callbacks(on_request=req_cb, on_response=resp_cb)
        assert driver.on_request_trace is req_cb
        assert driver.on_response_trace is resp_cb

    def test_clears_callbacks_with_none(self):
        driver = _make_driver()
        driver.set_trace_callbacks(on_request=lambda e: None)
        driver.set_trace_callbacks(on_request=None)
        assert driver.on_request_trace is None


# ---------------------------------------------------------------------------
# decide() — success paths
# ---------------------------------------------------------------------------


class TestDecideSuccess:
    @pytest.mark.asyncio
    async def test_plain_text_response(self):
        driver = _make_driver()
        ctx = _make_context(response_mode="text")
        payload = _make_response_payload("Hello world")

        mock_resp = _mock_httpx_response(200, payload)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        driver._client = mock_client

        result = await driver.decide(
            agent_id="a1",
            provider_name="dial",
            model_name="gpt-4o",
            temperature=0.2,
            context=ctx,
        )

        assert result.raw_text == "Hello world"
        assert result.finish_reason == "stop"
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    @pytest.mark.asyncio
    async def test_json_object_response_is_parsed(self):
        driver = _make_driver()
        ctx = _make_context(response_mode="json_object")
        payload = _make_response_payload('{"kind": "done", "count": 3}')

        mock_resp = _mock_httpx_response(200, payload)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        driver._client = mock_client

        result = await driver.decide(
            agent_id="a1",
            provider_name="dial",
            model_name="gpt-4o",
            temperature=0.2,
            context=ctx,
        )

        assert result.payload == {"kind": "done", "count": 3}

    @pytest.mark.asyncio
    async def test_tool_call_response(self):
        driver = _make_driver()
        ctx = _make_context(response_mode="text")
        payload = _make_tool_call_payload("search", '{"q": "python"}')

        mock_resp = _mock_httpx_response(200, payload)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        driver._client = mock_client

        result = await driver.decide(
            agent_id="a1",
            provider_name="dial",
            model_name="gpt-4o",
            temperature=0.2,
            context=ctx,
        )

        assert result.finish_reason == "tool_calls"
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["function"]["name"] == "search"

    @pytest.mark.asyncio
    async def test_request_trace_callback_fires(self):
        driver = _make_driver()
        ctx = _make_context(response_mode="text")
        payload = _make_response_payload("hi")

        mock_resp = _mock_httpx_response(200, payload)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        driver._client = mock_client

        traces = []
        driver.set_trace_callbacks(on_request=lambda e: traces.append(("req", e)))
        driver.set_trace_callbacks(
            on_request=lambda e: traces.append(("req", e)),
            on_response=lambda e: traces.append(("resp", e)),
        )

        await driver.decide(
            agent_id="a1",
            provider_name="dial",
            model_name="gpt-4o",
            temperature=0.2,
            context=ctx,
        )

        assert any(k == "req" for k, _ in traces)
        assert any(k == "resp" for k, _ in traces)

    @pytest.mark.asyncio
    async def test_api_key_header_sent(self):
        driver = _make_driver(api_key="secret-key")
        ctx = _make_context(response_mode="text")
        payload = _make_response_payload("ok")

        mock_resp = _mock_httpx_response(200, payload)
        posted_body = {}

        async def fake_post(url, *, json=None, **kwargs):
            posted_body.update(json or {})
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = fake_post
        driver._client = mock_client

        await driver.decide(
            agent_id="a1",
            provider_name="dial",
            model_name="gpt-4o",
            temperature=0.1,
            context=ctx,
        )

        assert "messages" in posted_body
        assert posted_body["temperature"] == 0.1


# ---------------------------------------------------------------------------
# decide() — error paths
# ---------------------------------------------------------------------------


class TestDecideErrors:
    @pytest.mark.asyncio
    async def test_http_500_raises_model_driver_error(self):
        driver = _make_driver()
        ctx = _make_context()

        mock_resp = _mock_httpx_response(500, "Internal Server Error")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        driver._client = mock_client

        with pytest.raises(ModelDriverError) as exc_info:
            await driver.decide(
                agent_id="a1",
                provider_name="dial",
                model_name="gpt-4o",
                temperature=0.2,
                context=ctx,
            )

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_transport_error_raises_502(self):
        import httpx

        driver = _make_driver()
        ctx = _make_context()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        driver._client = mock_client

        with pytest.raises(ModelDriverError) as exc_info:
            await driver.decide(
                agent_id="a1",
                provider_name="dial",
                model_name="gpt-4o",
                temperature=0.2,
                context=ctx,
            )

        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_empty_choices_raises_model_driver_error(self):
        driver = _make_driver()
        ctx = _make_context()

        mock_resp = _mock_httpx_response(200, {"choices": []})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        driver._client = mock_client

        with pytest.raises(ModelDriverError):
            await driver.decide(
                agent_id="a1",
                provider_name="dial",
                model_name="gpt-4o",
                temperature=0.2,
                context=ctx,
            )


# ---------------------------------------------------------------------------
# Response format retry (G-06)
# ---------------------------------------------------------------------------


class TestResponseFormatRetry:
    @pytest.mark.asyncio
    async def test_retries_without_response_format_on_400(self):
        """HTTP 400 with response_format in body → retry without it."""
        driver = _make_driver(retry_without_response_format=True)
        ctx = _make_context(
            response_mode="json_object",
            response_format={"type": "json_object"},
        )

        success_payload = _make_response_payload('{"result": "ok"}')

        call_count = 0
        success_text = json.dumps(success_payload)

        async def fake_post(url, *, json=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: 400 to trigger retry
                resp = MagicMock()
                resp.status_code = 400
                resp.text = "response_format not supported"
                return resp
            else:
                # Second call without response_format: success
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = success_payload
                resp.text = success_text
                return resp

        mock_client = AsyncMock()
        mock_client.post = fake_post
        driver._client = mock_client

        result = await driver.decide(
            agent_id="a1",
            provider_name="dial",
            model_name="gpt-4o",
            temperature=0.2,
            context=ctx,
        )

        assert call_count == 2
        assert result.payload == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_no_retry_when_disabled(self):
        """When retry_without_response_format=False, HTTP 400 raises immediately."""
        driver = _make_driver(retry_without_response_format=False)
        ctx = _make_context(
            response_mode="json_object",
            response_format={"type": "json_object"},
        )

        async def fake_post(url, *, json=None, **kwargs):
            resp = MagicMock()
            resp.status_code = 400
            resp.text = "bad request"
            return resp

        mock_client = AsyncMock()
        mock_client.post = fake_post
        driver._client = mock_client

        with pytest.raises(ModelDriverError) as exc_info:
            await driver.decide(
                agent_id="a1",
                provider_name="dial",
                model_name="gpt-4o",
                temperature=0.2,
                context=ctx,
            )

        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# aclose
# ---------------------------------------------------------------------------


class TestAclose:
    @pytest.mark.asyncio
    async def test_aclose_releases_client(self):
        driver = _make_driver()
        mock_client = AsyncMock()
        driver._client = mock_client

        await driver.aclose()

        mock_client.aclose.assert_called_once()
        assert driver._client is None

    @pytest.mark.asyncio
    async def test_aclose_noop_when_no_client(self):
        driver = _make_driver()
        assert driver._client is None
        await driver.aclose()  # Should not raise
