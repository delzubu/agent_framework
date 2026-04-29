"""Tests for the message/response split introduced in FEAT #76 Phase 1."""
from __future__ import annotations

import logging
from typing import Any

import pytest

from agent_framework.agents.agent_decision import AgentDecision
from agent_framework.agents.agent_result import AgentResult
from agent_framework.agents.result_envelope import render_subagent_envelope
from agent_framework.model import ModelResponse
from agent_framework_evaluator.evaluation import select_agent_result_field


# ---------------------------------------------------------------------------
# AgentDecision.from_model_response — response field parsing
# ---------------------------------------------------------------------------

def _resp(payload: dict) -> ModelResponse:
    return ModelResponse(payload=payload, raw_text=str(payload))


def test_parser_reads_response_field():
    decision = AgentDecision.from_model_response(
        _resp({"kind": "final_message", "message": "done", "response": {"status": "ok", "count": 3}}),
    )
    assert decision.response == {"status": "ok", "count": 3}
    assert decision.message == "done"


def test_parser_response_takes_precedence_over_parameters():
    decision = AgentDecision.from_model_response(
        _resp({
            "kind": "final_message",
            "message": "done",
            "response": {"a": 1},
            "parameters": {"b": 2},
        }),
    )
    assert decision.response == {"a": 1}
    assert decision.parameters == {"b": 2}


def test_parser_no_response_no_deprecation_warning_for_non_final_message(caplog):
    with caplog.at_level(logging.WARNING):
        decision = AgentDecision.from_model_response(
            _resp({"kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "hi"}}),
        )
    assert decision.response is None
    assert not any("Deprecated" in r.message for r in caplog.records)


def test_parser_parameters_on_final_message_raises():
    with pytest.raises(ValueError, match="final_message with structured output must use 'response'"):
        AgentDecision.from_model_response(
            _resp({"kind": "final_message", "message": "done", "parameters": {"x": 42}}),
        )


def test_parser_response_none_when_not_set():
    decision = AgentDecision.from_model_response(
        _resp({"kind": "final_message", "message": "plain text result"}),
    )
    assert decision.response is None


# ---------------------------------------------------------------------------
# AgentResult — response field present
# ---------------------------------------------------------------------------

def test_agent_result_has_response_field():
    r = AgentResult(status="completed", message="hi", response={"key": "val"})
    assert r.response == {"key": "val"}


def test_agent_result_response_defaults_to_none():
    r = AgentResult(status="completed", message="hi")
    assert r.response is None


# ---------------------------------------------------------------------------
# render_subagent_envelope
# ---------------------------------------------------------------------------

def test_envelope_returns_message_when_response_none():
    assert render_subagent_envelope(message="hello", response=None) == "hello"


def test_envelope_wraps_response_in_xml():
    out = render_subagent_envelope(message="summary", response={"a": 1})
    assert '<subagent_result message="summary">' in out
    assert '"a": 1' in out
    assert "</subagent_result>" in out


def test_envelope_escapes_quotes_in_message():
    out = render_subagent_envelope(message='say "hi"', response={"x": 0})
    assert 'message="say &quot;hi&quot;"' in out


def test_envelope_escapes_newlines_in_message():
    out = render_subagent_envelope(message="line1\nline2", response={"x": 0})
    assert "&#10;" in out


# ---------------------------------------------------------------------------
# select_agent_result_field — response.* traversal
# ---------------------------------------------------------------------------

def _result(message: str = "", response: dict | None = None, parameters: dict | None = None) -> dict:
    r: dict[str, Any] = {"status": "completed", "message": message}
    if response is not None:
        r["response"] = response
    if parameters is not None:
        r["parameters"] = parameters
    return r


def test_select_message_field():
    assert select_agent_result_field(_result(message="hello"), "message") == "hello"


def test_select_response_dot_path():
    r = _result(response={"nested": {"value": 42}})
    assert select_agent_result_field(r, "response.nested.value") == "42"


def test_select_response_takes_precedence_over_parameters():
    r = _result(response={"x": "from_response"}, parameters={"x": "from_params"})
    assert select_agent_result_field(r, "response.x") == "from_response"


def test_select_parameters_fallback():
    r = _result(parameters={"y": "val"})
    assert select_agent_result_field(r, "parameters.y") == "val"


def test_select_missing_field_returns_none():
    r = _result(message="hi")
    assert select_agent_result_field(r, "response.missing") is None


def test_select_dot_returns_full_payload():
    r = _result(message="hi", response={"a": 1})
    full = select_agent_result_field(r, ".")
    assert full is not None
    assert "message" in full
