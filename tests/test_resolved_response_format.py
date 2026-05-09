"""Tests for shared driver response-format resolution."""

import logging

import pytest

from agent_framework.errors import ModelDriverError
from agent_framework.model import (
    DEFAULT_RESPONSE_MODE,
    ModelContext,
    ModelResponse,
    _FallbackMixin,
    openai_responses_text_format_field,
    parse_json_object_model_output,
    resolved_response_format_dict,
)


def _ctx(**kwargs) -> ModelContext:
    base = dict(
        system_prompt="",
        user_prompt="",
    )
    base.update(kwargs)
    return ModelContext(**base)


def test_resolved_text_mode_returns_none() -> None:
    assert resolved_response_format_dict(_ctx(response_mode="text")) is None


def test_resolved_text_mode_ignores_explicit_response_format() -> None:
    assert (
        resolved_response_format_dict(
            _ctx(response_mode="text", response_format={"type": "json_object"})
        )
        is None
    )


def test_resolved_default_mode_without_format_is_json_object() -> None:
    assert resolved_response_format_dict(_ctx()) == {"type": "json_object"}
    assert resolved_response_format_dict(_ctx(response_mode=DEFAULT_RESPONSE_MODE)) == {
        "type": "json_object",
    }


def test_resolved_decision_mode_without_format_is_none() -> None:
    assert resolved_response_format_dict(_ctx(response_mode="decision")) is None


def test_resolved_explicit_response_format_passthrough() -> None:
    fmt = {"type": "json_schema", "json_schema": {"name": "n", "schema": {"type": "object"}}}
    out = resolved_response_format_dict(_ctx(response_format=fmt))
    assert out == fmt
    assert out is not fmt


def test_fallback_mixin_delegates_to_module_function() -> None:
    c = _ctx()
    assert _FallbackMixin.resolved_response_format_dict(c) == resolved_response_format_dict(c)


class _FallbackHarness(_FallbackMixin):
    def __init__(self) -> None:
        self._fallback_state: dict[tuple[str, ...], int] = {}


def test_fallback_mixin_does_not_fallback_on_structured_output_parse_error(caplog) -> None:
    driver = _FallbackHarness()
    calls: list[str] = []

    def _try_model(model: str) -> ModelResponse:
        calls.append(model)
        if model == "model_a":
            parse_json_object_model_output('{"kind":"final_message"}{"extra":true}', provider_label="Test")
        return ModelResponse(payload={"kind": "final_message"}, raw_text="{}")

    with caplog.at_level(logging.INFO, logger="agent_framework.model"):
        with pytest.raises(ModelDriverError, match="not valid JSON"):
            driver._fallback_decide(("model_a", "model_b"), _try_model)

    assert calls == ["model_a"]
    assert "not available" not in caplog.text
    assert "returned invalid structured output" in caplog.text


def test_fallback_mixin_tries_next_model_on_fallback_eligible_provider_error(caplog) -> None:
    driver = _FallbackHarness()
    calls: list[str] = []

    def _try_model(model: str) -> ModelResponse:
        calls.append(model)
        if model == "model_a":
            raise ModelDriverError(
                "provider unavailable",
                status_code=503,
                fallback_eligible=True,
                failure_category="communication",
            )
        return ModelResponse(payload={"kind": "final_message"}, raw_text="{}")

    with caplog.at_level(logging.INFO, logger="agent_framework.model"):
        result = driver._fallback_decide(("model_a", "model_b"), _try_model)

    assert calls == ["model_a", "model_b"]
    assert result.payload == {"kind": "final_message"}
    assert "falling back from model_a to model_b" in caplog.text
    assert "category=communication" in caplog.text


@pytest.mark.asyncio
async def test_async_fallback_mixin_does_not_fallback_on_structured_output_parse_error() -> None:
    driver = _FallbackHarness()
    calls: list[str] = []

    async def _try_model(model: str) -> ModelResponse:
        calls.append(model)
        if model == "model_a":
            parse_json_object_model_output(
                '{"kind":"final_message"}{"extra":true}',
                provider_label="Test",
            )
        return ModelResponse(payload={"kind": "final_message"}, raw_text="{}")

    with pytest.raises(ModelDriverError, match="not valid JSON"):
        await driver._fallback_decide_async(("model_a", "model_b"), _try_model)

    assert calls == ["model_a"]


@pytest.mark.asyncio
async def test_async_fallback_mixin_tries_next_model_on_fallback_eligible_provider_error() -> None:
    driver = _FallbackHarness()
    calls: list[str] = []

    async def _try_model(model: str) -> ModelResponse:
        calls.append(model)
        if model == "model_a":
            raise ModelDriverError(
                "provider unavailable",
                status_code=503,
                fallback_eligible=True,
                failure_category="communication",
            )
        return ModelResponse(payload={"kind": "final_message"}, raw_text="{}")

    result = await driver._fallback_decide_async(("model_a", "model_b"), _try_model)

    assert calls == ["model_a", "model_b"]
    assert result.payload == {"kind": "final_message"}


def test_openai_text_format_json_object() -> None:
    assert openai_responses_text_format_field({"type": "json_object"}) == {"type": "json_object"}


def test_openai_text_format_json_schema_nested() -> None:
    fmt = {
        "type": "json_schema",
        "json_schema": {
            "name": "my_schema",
            "schema": {"type": "object", "properties": {"a": {"type": "string"}}},
            "strict": True,
            "description": "d",
        },
    }
    out = openai_responses_text_format_field(fmt)
    assert out["type"] == "json_schema"
    assert out["name"] == "my_schema"
    assert out["strict"] is True
    assert out["description"] == "d"
    assert "properties" in out["schema"]
