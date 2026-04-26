"""Tests for PlanStep, CompletedStep, and PlanState dataclasses."""
from __future__ import annotations

import time

import pytest

from agent_framework.planning.plan_state import CompletedStep, PlanState, PlanStep
from agent_framework.agents.agent_run import AgentRun


# ---------------------------------------------------------------------------
# PlanStep
# ---------------------------------------------------------------------------

def test_plan_step_minimal():
    step = PlanStep(id="s1", kind="call_tool", parameters={"tool_name": "fetch"})
    assert step.id == "s1"
    assert step.kind == "call_tool"
    assert step.parameters == {"tool_name": "fetch"}
    assert step.tool_name is None
    assert step.subagent_id is None
    assert step.skill_name is None
    assert step.callback_intent is None
    assert step.depends_on == ()
    assert step.message == ""


def test_plan_step_with_all_fields():
    step = PlanStep(
        id="lookup",
        kind="call_subagent",
        parameters={"player_id": "{{player_id}}"},
        subagent_id="player_lookup",
        depends_on=("init",),
        message="fetch player state",
    )
    assert step.subagent_id == "player_lookup"
    assert step.depends_on == ("init",)
    assert step.message == "fetch player state"


def test_plan_step_is_frozen():
    step = PlanStep(id="s1", kind="call_tool", parameters={})
    with pytest.raises((AttributeError, TypeError)):
        step.id = "s2"  # type: ignore[misc]


def test_plan_step_parameters_with_refs():
    step = PlanStep(
        id="analyze",
        kind="call_subagent",
        parameters={"data": "{{fetch.content}}", "count": "{{fetch.count}}"},
        subagent_id="analyzer",
    )
    assert "{{fetch.content}}" in step.parameters["data"]


# ---------------------------------------------------------------------------
# CompletedStep
# ---------------------------------------------------------------------------

def test_completed_step_minimal():
    step = PlanStep(id="s1", kind="call_tool", parameters={})
    now = time.time()
    cs = CompletedStep(
        step_id="s1",
        step=step,
        result={"value": 42},
        started_at=now,
        finished_at=now + 1.0,
        plan_revision_at_start=0,
    )
    assert cs.step_id == "s1"
    assert cs.result == {"value": 42}
    assert cs.error is None
    assert cs.finished_at > cs.started_at


def test_completed_step_with_error():
    step = PlanStep(id="bad", kind="call_tool", parameters={})
    now = time.time()
    cs = CompletedStep(
        step_id="bad",
        step=step,
        result={"error": "timeout"},
        started_at=now,
        finished_at=now + 0.5,
        plan_revision_at_start=1,
        error="TimeoutError: step exceeded deadline",
    )
    assert cs.error == "TimeoutError: step exceeded deadline"
    assert cs.plan_revision_at_start == 1


def test_completed_step_is_mutable():
    step = PlanStep(id="s1", kind="call_tool", parameters={})
    now = time.time()
    cs = CompletedStep(
        step_id="s1", step=step, result=None,
        started_at=now, finished_at=now, plan_revision_at_start=0,
    )
    cs.error = "late error"
    assert cs.error == "late error"


# ---------------------------------------------------------------------------
# PlanState
# ---------------------------------------------------------------------------

def test_plan_state_defaults():
    state = PlanState()
    assert state.plan == ()
    assert state.step_results == {}
    assert state.completed_steps == []
    assert state.plan_revision == 0
    assert state.total_steps_executed == 0
    assert state.pending_callback_step_id is None
    assert state.awaiting_caller_callback is False


def test_plan_state_plan_replacement():
    state = PlanState()
    step1 = PlanStep(id="a", kind="call_tool", parameters={})
    step2 = PlanStep(id="b", kind="call_subagent", parameters={}, subagent_id="agent_b")
    state.plan = (step1, step2)
    assert len(state.plan) == 2
    assert state.plan[0].id == "a"
    # Replace entirely (as driver does on replan)
    step3 = PlanStep(id="c", kind="call_tool", parameters={})
    state.plan = (step3,)
    assert len(state.plan) == 1
    assert state.plan[0].id == "c"


def test_plan_state_step_results_mutation():
    state = PlanState()
    state.step_results["fetch"] = {"content": "hello"}
    state.step_results["parse"] = {"tokens": 5}
    assert state.step_results["fetch"]["content"] == "hello"
    assert len(state.step_results) == 2


def test_plan_state_completed_steps_append():
    state = PlanState()
    step = PlanStep(id="s1", kind="call_tool", parameters={})
    now = time.time()
    cs = CompletedStep(
        step_id="s1", step=step, result="ok",
        started_at=now, finished_at=now + 0.1, plan_revision_at_start=0,
    )
    state.completed_steps.append(cs)
    assert len(state.completed_steps) == 1
    assert state.completed_steps[0].step_id == "s1"


def test_plan_state_counters_mutable():
    state = PlanState()
    state.plan_revision += 1
    state.total_steps_executed += 3
    assert state.plan_revision == 1
    assert state.total_steps_executed == 3


def test_plan_state_callback_flags():
    state = PlanState()
    state.pending_callback_step_id = "step_ask"
    state.awaiting_caller_callback = True
    assert state.pending_callback_step_id == "step_ask"
    assert state.awaiting_caller_callback is True


# ---------------------------------------------------------------------------
# AgentRun.plan_state field
# ---------------------------------------------------------------------------

def test_agent_run_plan_state_defaults_to_none():
    run = AgentRun(
        run_id="r1",
        parent_run_id=None,
        rendered_prompt="",
        seed_parameters={},
        parameter_values={},
    )
    assert run.plan_state is None


def test_agent_run_plan_state_can_be_set():
    run = AgentRun(
        run_id="r2",
        parent_run_id=None,
        rendered_prompt="",
        seed_parameters={},
        parameter_values={},
    )
    state = PlanState()
    run.plan_state = state
    assert run.plan_state is state


def test_agent_run_existing_fields_unaffected():
    """Ensure the new field does not break existing AgentRun construction."""
    run = AgentRun(
        run_id="r3",
        parent_run_id="parent",
        rendered_prompt="hello",
        seed_parameters={"x": 1},
        parameter_values={"x": 1},
        in_parallel_batch=True,
    )
    assert run.run_id == "r3"
    assert run.in_parallel_batch is True
    assert run.plan_state is None
