"""Tests for the step reference resolver."""
from __future__ import annotations

import logging

import pytest

from agent_framework.planning.step_reference import resolve, StepReferenceResolver


# Shared test data
_STEP_RESULTS = {
    "fetch": {"content": "hello world", "count": 3, "nested": {"x": 99}},
    "parse": ["token1", "token2"],
    "flag": True,
    "num": 42,
}
_INVOCATION = {
    "player_id": "p-123",
    "topic": "dragons",
}
_CTX = dict(
    invocation_parameters=_INVOCATION,
    step_results=_STEP_RESULTS,
    run_id="run-1",
    agent_id="agent-1",
    step_id="step-1",
)


# ---------------------------------------------------------------------------
# Whole-string-is-token — type preservation
# ---------------------------------------------------------------------------

def test_whole_string_token_dict():
    result = resolve("{{fetch}}", **_CTX)
    assert result == {"content": "hello world", "count": 3, "nested": {"x": 99}}


def test_whole_string_token_list():
    result = resolve("{{parse}}", **_CTX)
    assert result == ["token1", "token2"]


def test_whole_string_token_int():
    result = resolve("{{num}}", **_CTX)
    assert result == 42
    assert isinstance(result, int)


def test_whole_string_token_bool():
    result = resolve("{{flag}}", **_CTX)
    assert result is True


def test_whole_string_token_whitespace_padded():
    result = resolve("{{ num }}", **_CTX)
    assert result == 42


# ---------------------------------------------------------------------------
# Embedded tokens — stringify
# ---------------------------------------------------------------------------

def test_embedded_token_string():
    result = resolve("id={{player_id}}", **_CTX)
    assert result == "id=p-123"


def test_embedded_multiple_tokens():
    result = resolve("{{player_id}}/{{topic}}", **_CTX)
    assert result == "p-123/dragons"


def test_embedded_dict_token_stringified():
    result = resolve("data={{fetch}}", **_CTX)
    assert "content" in result
    assert result.startswith("data=")


# ---------------------------------------------------------------------------
# Dot-path traversal
# ---------------------------------------------------------------------------

def test_dot_path_one_level():
    result = resolve("{{fetch.content}}", **_CTX)
    assert result == "hello world"


def test_dot_path_two_levels():
    result = resolve("{{fetch.nested.x}}", **_CTX)
    assert result == 99


def test_dot_path_count_int():
    result = resolve("{{fetch.count}}", **_CTX)
    assert result == 3


# ---------------------------------------------------------------------------
# Missing tokens — warning + empty string
# ---------------------------------------------------------------------------

def test_missing_top_level_resolves_to_empty(caplog):
    with caplog.at_level(logging.WARNING, logger="agent_framework.planning.step_reference"):
        result = resolve("{{nonexistent}}", **_CTX)
    assert result == ""
    assert "nonexistent" in caplog.text


def test_missing_dot_path_segment_resolves_to_empty(caplog):
    with caplog.at_level(logging.WARNING, logger="agent_framework.planning.step_reference"):
        result = resolve("{{fetch.missing_key}}", **_CTX)
    assert result == ""
    assert "missing_key" in caplog.text


def test_dot_path_into_non_dict_resolves_to_empty(caplog):
    with caplog.at_level(logging.WARNING, logger="agent_framework.planning.step_reference"):
        result = resolve("{{parse.anything}}", **_CTX)
    assert result == ""
    assert "parse.anything" in caplog.text


def test_missing_embedded_token_resolves_to_empty(caplog):
    with caplog.at_level(logging.WARNING, logger="agent_framework.planning.step_reference"):
        result = resolve("prefix {{nope}} suffix", **_CTX)
    assert result == "prefix  suffix"


# ---------------------------------------------------------------------------
# Step results win over invocation parameters on name collision
# ---------------------------------------------------------------------------

def test_step_result_wins_over_invocation_param():
    ctx = dict(
        invocation_parameters={"fetch": "wrong"},
        step_results={"fetch": "correct"},
        run_id="r", agent_id="a", step_id="s",
    )
    assert resolve("{{fetch}}", **ctx) == "correct"


def test_invocation_param_used_when_no_step_result():
    ctx = dict(
        invocation_parameters={"topic": "cats"},
        step_results={},
        run_id="r", agent_id="a", step_id="s",
    )
    assert resolve("{{topic}}", **ctx) == "cats"


# ---------------------------------------------------------------------------
# Recursive dict and list walking
# ---------------------------------------------------------------------------

def test_recursive_dict():
    value = {"a": "{{player_id}}", "b": {"c": "{{topic}}"}}
    result = resolve(value, **_CTX)
    assert result == {"a": "p-123", "b": {"c": "dragons"}}


def test_recursive_list():
    value = ["{{player_id}}", "{{topic}}", "static"]
    result = resolve(value, **_CTX)
    assert result == ["p-123", "dragons", "static"]


def test_recursive_mixed():
    value = {"ids": ["{{player_id}}", "{{num}}"], "meta": {"topic": "{{topic}}"}}
    result = resolve(value, **_CTX)
    assert result["ids"][0] == "p-123"
    assert result["ids"][1] == 42
    assert result["meta"]["topic"] == "dragons"


# ---------------------------------------------------------------------------
# Scalar pass-through
# ---------------------------------------------------------------------------

def test_int_passthrough():
    assert resolve(42, **_CTX) == 42


def test_none_passthrough():
    assert resolve(None, **_CTX) is None


def test_bool_passthrough():
    assert resolve(False, **_CTX) is False


# ---------------------------------------------------------------------------
# StepReferenceResolver Protocol structural check
# ---------------------------------------------------------------------------

def test_resolve_satisfies_protocol():
    """The module-level resolve function matches StepReferenceResolver.resolve signature."""

    class _Wrapper:
        def resolve(self, value, *, invocation_parameters, step_results,
                    run_id, agent_id, step_id):
            return resolve(
                value,
                invocation_parameters=invocation_parameters,
                step_results=step_results,
                run_id=run_id, agent_id=agent_id, step_id=step_id,
            )

    r: StepReferenceResolver = _Wrapper()
    result = r.resolve("{{player_id}}", **_CTX)
    assert result == "p-123"
