"""Tests for shared driver response-format resolution."""

from agent_framework.model import (
    DEFAULT_RESPONSE_MODE,
    ModelContext,
    _FallbackMixin,
    openai_responses_text_format_field,
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
