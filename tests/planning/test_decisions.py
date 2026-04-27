"""Tests for planning-kind AgentDecision parsing and validation."""
from __future__ import annotations

import pytest

from agent_framework.agents.agent_decision import AgentDecision, _PLANNING_DECISION_KINDS
from agent_framework.model import ModelResponse


def _resp(payload: dict) -> ModelResponse:
    return ModelResponse(payload=payload, raw_text=str(payload))


def _submit(plan: list[dict], message: str = "plan") -> ModelResponse:
    return _resp({"kind": "submit_plan", "message": message, "plan": plan})


def _continue(message: str = "") -> ModelResponse:
    return _resp({"kind": "continue_plan", "message": message})


def _amend(plan: list[dict], message: str = "amend") -> ModelResponse:
    return _resp({"kind": "amend_plan", "message": message, "plan": plan})


_MINIMAL_STEP = {
    "id": "step_a",
    "kind": "call_tool",
    "tool_name": "fetch",
    "parameters": {},
}
_SUBAGENT_STEP = {
    "id": "step_b",
    "kind": "call_subagent",
    "subagent_id": "my_agent",
    "parameters": {"input": "{{step_a.result}}"},
    "depends_on": ["step_a"],
}


# ---------------------------------------------------------------------------
# Gating: planning kinds rejected without planning_active
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["submit_plan", "amend_plan", "continue_plan"])
def test_planning_kind_without_planning_active_raises(kind):
    payload = {"kind": kind, "message": "x", "plan": [_MINIMAL_STEP]}
    with pytest.raises(ValueError, match="only valid for planning-enabled agents"):
        AgentDecision.from_model_response(_resp(payload))


@pytest.mark.parametrize("kind", ["submit_plan", "amend_plan", "continue_plan"])
def test_planning_kind_with_planning_active_accepted(kind):
    payload = {"kind": kind, "message": "x", "plan": [_MINIMAL_STEP]}
    decision = AgentDecision.from_model_response(_resp(payload), planning_active=True)
    assert decision.kind == kind


# ---------------------------------------------------------------------------
# Non-planning kinds unaffected by planning_active flag
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,extra", [
    ("final_message", {}),
    ("call_tool", {"tool_name": "my_tool"}),
    ("call_subagent", {"subagent_id": "my_agent"}),
])
def test_non_planning_kinds_parse_identically(kind, extra):
    payload = {"kind": kind, "message": "ok", **extra}
    d1 = AgentDecision.from_model_response(_resp(payload), planning_active=False)
    d2 = AgentDecision.from_model_response(_resp(payload), planning_active=True)
    assert d1.kind == kind
    assert d2.kind == kind


# ---------------------------------------------------------------------------
# Valid submit_plan
# ---------------------------------------------------------------------------

def test_submit_plan_minimal_single_step():
    decision = AgentDecision.from_model_response(
        _submit([_MINIMAL_STEP]), planning_active=True
    )
    assert decision.kind == "submit_plan"
    assert len(decision.plan) == 1
    assert decision.plan[0].id == "step_a"
    assert decision.plan[0].kind == "call_tool"
    assert decision.plan[0].tool_name == "fetch"


def test_submit_plan_two_steps_with_dependency():
    decision = AgentDecision.from_model_response(
        _submit([_MINIMAL_STEP, _SUBAGENT_STEP]), planning_active=True
    )
    assert len(decision.plan) == 2
    assert decision.plan[1].depends_on == ("step_a",)
    assert decision.plan[1].subagent_id == "my_agent"


def test_submit_plan_invoke_skill_step():
    step = {"id": "do_skill", "kind": "invoke_skill", "skill_name": "my_skill", "parameters": {}}
    decision = AgentDecision.from_model_response(
        _submit([step]), planning_active=True
    )
    assert decision.plan[0].skill_name == "my_skill"


def test_submit_plan_callback_step():
    step = {"id": "ask_user", "kind": "callback", "callback_intent": "information_request",
            "parameters": {}, "message": "need info"}
    decision = AgentDecision.from_model_response(
        _submit([step]), planning_active=True
    )
    assert decision.plan[0].callback_intent == "information_request"


def test_submit_plan_message_preserved():
    decision = AgentDecision.from_model_response(
        _submit([_MINIMAL_STEP], message="I'll fetch first"), planning_active=True
    )
    assert decision.message == "I'll fetch first"


# ---------------------------------------------------------------------------
# submit_plan validation errors
# ---------------------------------------------------------------------------

def test_submit_plan_missing_plan_raises():
    with pytest.raises(ValueError, match="non-empty list"):
        AgentDecision.from_model_response(
            _resp({"kind": "submit_plan", "message": "x"}), planning_active=True
        )


def test_submit_plan_empty_plan_raises():
    with pytest.raises(ValueError, match="non-empty list"):
        AgentDecision.from_model_response(
            _resp({"kind": "submit_plan", "plan": []}), planning_active=True
        )


def test_submit_plan_duplicate_step_id_raises():
    step2 = dict(_MINIMAL_STEP, id="step_a")
    with pytest.raises(ValueError, match="duplicate step id"):
        AgentDecision.from_model_response(
            _submit([_MINIMAL_STEP, step2]), planning_active=True
        )


def test_submit_plan_invalid_step_id_regex_raises():
    step = dict(_MINIMAL_STEP, id="1bad_id")
    with pytest.raises(ValueError, match=r"\^"):
        AgentDecision.from_model_response(_submit([step]), planning_active=True)


def test_submit_plan_unsupported_step_kind_raises():
    step = dict(_MINIMAL_STEP, kind="submit_plan")
    with pytest.raises(ValueError, match="unsupported kind"):
        AgentDecision.from_model_response(_submit([step]), planning_active=True)


def test_submit_plan_both_tool_and_subagent_raises():
    step = {
        "id": "bad_step", "kind": "call_tool",
        "tool_name": "t", "subagent_id": "a",
        "parameters": {},
    }
    with pytest.raises(ValueError, match="both 'tool_name' and 'subagent_id'"):
        AgentDecision.from_model_response(_submit([step]), planning_active=True)


def test_submit_plan_call_tool_missing_tool_name_raises():
    step = {"id": "s", "kind": "call_tool", "parameters": {}}
    with pytest.raises(ValueError, match="missing 'tool_name'"):
        AgentDecision.from_model_response(_submit([step]), planning_active=True)


def test_submit_plan_call_subagent_missing_subagent_id_raises():
    step = {"id": "s", "kind": "call_subagent", "parameters": {}}
    with pytest.raises(ValueError, match="missing 'subagent_id'"):
        AgentDecision.from_model_response(_submit([step]), planning_active=True)


def test_submit_plan_invoke_skill_missing_skill_name_raises():
    step = {"id": "s", "kind": "invoke_skill", "parameters": {}}
    with pytest.raises(ValueError, match="missing 'skill_name'"):
        AgentDecision.from_model_response(_submit([step]), planning_active=True)


def test_submit_plan_callback_missing_intent_raises():
    step = {"id": "s", "kind": "callback", "parameters": {}}
    with pytest.raises(ValueError, match="missing 'callback_intent'"):
        AgentDecision.from_model_response(_submit([step]), planning_active=True)


def test_submit_plan_forward_ref_raises():
    # step_a tries to depend on step_b which comes after it
    step_a = dict(_MINIMAL_STEP, depends_on=["step_b"])
    step_b = {"id": "step_b", "kind": "call_subagent", "subagent_id": "x", "parameters": {}}
    with pytest.raises(ValueError, match="forward references are not allowed"):
        AgentDecision.from_model_response(_submit([step_a, step_b]), planning_active=True)


def test_submit_plan_unknown_dependency_raises():
    step = dict(_MINIMAL_STEP, depends_on=["nonexistent"])
    with pytest.raises(ValueError, match="forward references are not allowed"):
        AgentDecision.from_model_response(_submit([step]), planning_active=True)


# ---------------------------------------------------------------------------
# continue_plan
# ---------------------------------------------------------------------------

def test_continue_plan_empty_message():
    decision = AgentDecision.from_model_response(_continue(), planning_active=True)
    assert decision.kind == "continue_plan"
    assert decision.message == ""
    assert decision.plan == ()


def test_continue_plan_with_message():
    decision = AgentDecision.from_model_response(
        _continue("progress looks good"), planning_active=True
    )
    assert decision.message == "progress looks good"


def test_continue_plan_with_resolution_in_parameters():
    payload = {
        "kind": "continue_plan",
        "message": "resolved",
        "parameters": {"resolution": "use the east gate"},
    }
    decision = AgentDecision.from_model_response(_resp(payload), planning_active=True)
    assert decision.parameters["resolution"] == "use the east gate"


# ---------------------------------------------------------------------------
# amend_plan — accepted but driver raises NotImplementedError
# ---------------------------------------------------------------------------

def test_amend_plan_parses_plan():
    decision = AgentDecision.from_model_response(
        _amend([_MINIMAL_STEP]), planning_active=True
    )
    assert decision.kind == "amend_plan"
    assert len(decision.plan) == 1


# ---------------------------------------------------------------------------
# _PLANNING_DECISION_KINDS constant
# ---------------------------------------------------------------------------

def test_planning_decision_kinds_set():
    assert "submit_plan" in _PLANNING_DECISION_KINDS
    assert "amend_plan" in _PLANNING_DECISION_KINDS
    assert "continue_plan" in _PLANNING_DECISION_KINDS
    assert "call_tool" not in _PLANNING_DECISION_KINDS
