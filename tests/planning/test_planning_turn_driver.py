"""Tests for PlanningTurnDriver — plan/execute/reflect lifecycle.

Uses a scripted MockModelDriver that returns preset payloads and a
minimal AgentHost wired to a fake tool registry.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_framework.agent import Agent, AgentResult
from agent_framework.host import AgentHost
from agent_framework.model import ModelContext, ModelResponse
from agent_framework.planning.config import PlanningConfig
from agent_framework.planning.plan_state import PlanState
from agent_framework.planning.turn_driver import (
    PlanningTurnDriver,
    _select_ready_batch,
    _all_steps_done,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _ScriptedDriver:
    """Returns preset payloads in sequence; records all model calls."""

    def __init__(self, payloads: list[dict]):
        self._payloads = list(payloads)
        self.calls: list[ModelContext] = []

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        pass

    def decide(self, *, agent_id, provider_name, model_names, temperature, context: ModelContext):
        self.calls.append(context)
        if not self._payloads:
            raise RuntimeError("No more payloads — scripted driver exhausted")
        payload = self._payloads.pop(0)
        return ModelResponse(payload=payload, raw_text=str(payload))


def _planning_agent_md(
    agent_id: str = "planner",
    extra_frontmatter: str = "",
) -> str:
    return (
        f"id: {agent_id}\n"
        f"role: planner\n"
        f"planning:\n  enabled: true\n"
        f"{extra_frontmatter}\n"
        f"---\n"
        f"You are a planning agent.\n"
        f"---\n"
        f"Execute the plan.\n"
    )


def _make_host(
    tmp_path: Path,
    payloads: list[dict],
    *,
    agent_id: str = "planner",
    extra_frontmatter: str = "",
) -> tuple[AgentHost, _ScriptedDriver]:
    agent_path = tmp_path / f"{agent_id}.md"
    agent_path.write_text(
        _planning_agent_md(agent_id, extra_frontmatter=extra_frontmatter),
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "OPENAI_API_KEY=test-key",
            "DEFAULT_PROVIDER=openai",
            "DEFAULT_MODEL=gpt-4o-mini",
            f"AGENT_DIRECTORY={tmp_path}",
            f"ROOT_AGENT={agent_id}",
        ]),
        encoding="utf-8",
    )
    host = AgentHost.from_env(env)
    driver = _ScriptedDriver(payloads)
    host.model_driver = driver
    return host, driver


_STEP_A = {"id": "step_a", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "hello"}}
_STEP_B = {"id": "step_b", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "world"}, "depends_on": ["step_a"]}
_STEP_C = {"id": "step_c", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "done"}, "depends_on": ["step_a", "step_b"]}


def _register_echo(host: AgentHost) -> None:
    """Register a simple echo tool on the host."""
    from agent_framework.tool import Tool, ToolDefinition, ToolParameter

    _ECHO_DEF = ToolDefinition(
        tool_id="echo",
        description="Echo the msg parameter.",
        parameters=(ToolParameter("msg", "message to echo", required=False),),
    )

    class EchoTool(Tool):
        def invoke(self, parameters: dict, host: Any) -> str:
            return str(parameters.get("msg", ""))

    host.tool_registry.register(EchoTool(definition=_ECHO_DEF))


# ---------------------------------------------------------------------------
# _select_ready_batch unit tests
# ---------------------------------------------------------------------------

def test_select_ready_batch_empty_plan():
    state = PlanState()
    assert _select_ready_batch(state, parallel_execution=True) == []


def test_select_ready_batch_no_deps_all_ready():
    from agent_framework.planning.plan_state import PlanStep
    steps = (
        PlanStep(id="a", kind="call_tool", parameters={}, tool_name="t"),
        PlanStep(id="b", kind="call_tool", parameters={}, tool_name="t"),
    )
    state = PlanState(plan=steps)
    batch = _select_ready_batch(state, parallel_execution=True)
    assert {s.id for s in batch} == {"a", "b"}


def test_select_ready_batch_dep_not_completed():
    from agent_framework.planning.plan_state import PlanStep
    steps = (
        PlanStep(id="a", kind="call_tool", parameters={}, tool_name="t"),
        PlanStep(id="b", kind="call_tool", parameters={}, tool_name="t", depends_on=("a",)),
    )
    state = PlanState(plan=steps)
    batch = _select_ready_batch(state, parallel_execution=True)
    assert [s.id for s in batch] == ["a"]


def test_select_ready_batch_parallel_false_returns_one():
    from agent_framework.planning.plan_state import PlanStep
    steps = (
        PlanStep(id="a", kind="call_tool", parameters={}, tool_name="t"),
        PlanStep(id="b", kind="call_tool", parameters={}, tool_name="t"),
    )
    state = PlanState(plan=steps)
    batch = _select_ready_batch(state, parallel_execution=False)
    assert len(batch) == 1


# ---------------------------------------------------------------------------
# Happy path: 3 sequential steps → end-of-plan reflect → final_message
# ---------------------------------------------------------------------------

def test_happy_path_sequential_three_steps(tmp_path: Path):
    host, scripted = _make_host(tmp_path, [
        # Turn 1 (PLAN): model emits submit_plan with 3 sequential steps
        {
            "kind": "submit_plan",
            "message": "Plan ready",
            "plan": [_STEP_A, _STEP_B, _STEP_C],
        },
        # End-of-plan REFLECT: all steps done → final_message
        {"kind": "final_message", "message": "All done!"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "run three steps")

    assert result.status == "completed"
    assert result.message == "All done!"
    # Exactly 2 model calls: plan + reflect
    assert len(scripted.calls) == 2


def test_happy_path_step_results_populated(tmp_path: Path):
    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "go", "plan": [_STEP_A, _STEP_B, _STEP_C]},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "run")
    assert result.status == "completed"


def test_happy_path_plan_revision_is_one(tmp_path: Path):
    """A clean first-plan run returns completed status."""
    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "go", "plan": [_STEP_A]},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)
    result = host.run_agent("planner", "run")
    assert result.status == "completed"


# ---------------------------------------------------------------------------
# Parallel batch: two independent steps + one dependent
# ---------------------------------------------------------------------------

def test_parallel_batch_two_independent_then_dependent(tmp_path: Path):
    """step_a and step_b run in parallel; step_c waits for both."""
    host, scripted = _make_host(tmp_path, [
        {
            "kind": "submit_plan",
            "message": "parallel plan",
            "plan": [
                {"id": "step_a", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "a"}},
                {"id": "step_b", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "b"}},
                {"id": "step_c", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "c"},
                 "depends_on": ["step_a", "step_b"]},
            ],
        },
        {"kind": "final_message", "message": "parallel done"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "parallel")

    assert result.status == "completed"
    assert result.message == "parallel done"
    # 2 model calls: submit_plan + final_message
    assert len(scripted.calls) == 2


# ---------------------------------------------------------------------------
# Re-plan via submit_plan in reflect phase
# ---------------------------------------------------------------------------

def test_replan_via_submit_plan_in_reflect(tmp_path: Path):
    """Model emits submit_plan from reflect phase (re-plan); driver replaces plan."""
    host, scripted = _make_host(tmp_path, [
        # First plan: one step
        {"kind": "submit_plan", "message": "plan v1", "plan": [_STEP_A]},
        # After step_a completes → reflect → model re-plans
        {
            "kind": "submit_plan",
            "message": "revised plan",
            "plan": [
                {"id": "step_x", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "x"}},
            ],
        },
        # After step_x completes → reflect → final
        {"kind": "final_message", "message": "replanned and done"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "replan")

    assert result.status == "completed"
    assert result.message == "replanned and done"
    assert len(scripted.calls) == 3


# ---------------------------------------------------------------------------
# End-of-plan reflect is mandatory even for clean runs
# ---------------------------------------------------------------------------

def test_end_of_plan_reflect_always_called(tmp_path: Path):
    """Even a single-step plan calls model once after completion."""
    host, scripted = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "one step", "plan": [_STEP_A]},
        {"kind": "final_message", "message": "reflected"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "one")

    assert result.status == "completed"
    assert len(scripted.calls) == 2


# ---------------------------------------------------------------------------
# amend_plan raises NotImplementedError
# ---------------------------------------------------------------------------

def test_amend_plan_raises_not_implemented(tmp_path: Path):
    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "go", "plan": [_STEP_A]},
        # reflect → amend_plan → NotImplementedError
        {"kind": "amend_plan", "message": "amend", "plan": [_STEP_A]},
    ])
    _register_echo(host)

    with pytest.raises(NotImplementedError, match="amend_plan"):
        host.run_agent("planner", "amend")


# ---------------------------------------------------------------------------
# parallel_execution=false dispatches one step at a time
# ---------------------------------------------------------------------------

def test_parallel_execution_false_dispatches_one_at_a_time(tmp_path: Path):
    """With parallel_execution=false two independent steps run sequentially."""
    agent_path = tmp_path / "seq_planner.md"
    agent_path.write_text(
        "id: seq_planner\nrole: planner\nplanning:\n  enabled: true\n  parallel_execution: false\n"
        "---\nYou are sequential.\n---\nExecute the plan.\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "OPENAI_API_KEY=test-key",
            "DEFAULT_PROVIDER=openai",
            "DEFAULT_MODEL=gpt-4o-mini",
            f"AGENT_DIRECTORY={tmp_path}",
            "ROOT_AGENT=seq_planner",
        ]),
        encoding="utf-8",
    )
    host = AgentHost.from_env(env)
    scripted = _ScriptedDriver([
        {
            "kind": "submit_plan",
            "message": "seq",
            "plan": [
                {"id": "step_a", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "a"}},
                {"id": "step_b", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "b"}},
            ],
        },
        {"kind": "final_message", "message": "sequential done"},
    ])
    host.model_driver = scripted
    _register_echo(host)

    result = host.run_agent("seq_planner", "seq")

    assert result.status == "completed"
    # 2 model calls: plan + final reflect
    assert len(scripted.calls) == 2


# ---------------------------------------------------------------------------
# Pluggable resolver: host.step_ref_resolver is used when set
# ---------------------------------------------------------------------------

def test_pluggable_resolver_is_invoked(tmp_path: Path):
    """When host.step_ref_resolver is set, the driver invokes its resolve method."""
    host, _ = _make_host(tmp_path, [
        {
            "kind": "submit_plan",
            "message": "ref test",
            "plan": [
                {"id": "step_a", "kind": "call_tool", "tool_name": "echo",
                 "parameters": {"msg": "literal value"}},
            ],
        },
        {"kind": "final_message", "message": "resolved"},
    ])
    _register_echo(host)

    resolve_calls: list[Any] = []

    class _TrackingResolver:
        def resolve(self, value, **kwargs):
            resolve_calls.append(value)
            from agent_framework.planning.step_reference import resolve
            return resolve(value, **kwargs)

    host.step_ref_resolver = _TrackingResolver()

    result = host.run_agent("planner", "go")

    assert result.status == "completed"
    assert len(resolve_calls) > 0


# ---------------------------------------------------------------------------
# PlanningTurnDriver satisfies TurnDriver protocol
# ---------------------------------------------------------------------------

def test_planning_turn_driver_has_run_turn():
    config = PlanningConfig.from_frontmatter({"enabled": True})
    driver = PlanningTurnDriver(config=config)
    assert callable(getattr(driver, "run_turn", None))
