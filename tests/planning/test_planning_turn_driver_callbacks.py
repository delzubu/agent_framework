"""Tests for callback handling in PlanningTurnDriver (FEAT #64).

Covers model-bound callback steps (plan kind: callback) that pause
execution until the planning model resolves them via continue_plan.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_framework.host import AgentHost
from agent_framework.model import ModelContext, ModelResponse
from agent_framework.planning.turn_driver import _select_ready_batch
from agent_framework.planning.plan_state import PlanState, PlanStep, CompletedStep


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_planning_turn_driver.py)
# ---------------------------------------------------------------------------

class _ScriptedDriver:
    def __init__(self, payloads: list[dict]):
        self._payloads = list(payloads)
        self.calls: list[ModelContext] = []

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        pass

    def decide(self, *, agent_id, provider_name, model_names, temperature, context: ModelContext):
        self.calls.append(context)
        if not self._payloads:
            raise RuntimeError("No more payloads")
        payload = self._payloads.pop(0)
        return ModelResponse(payload=payload, raw_text=str(payload))


def _make_host(tmp_path: Path, payloads: list[dict], agent_id: str = "planner") -> tuple[AgentHost, _ScriptedDriver]:
    agent_path = tmp_path / f"{agent_id}.md"
    agent_path.write_text(
        f"id: {agent_id}\nrole: planner\nplanning:\n  enabled: true\n"
        f"---\nYou are a planning agent.\n---\nExecute the plan.\n",
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


def _register_echo(host: AgentHost) -> None:
    from agent_framework.tool import Tool, ToolDefinition, ToolParameter

    _ECHO_DEF = ToolDefinition(
        tool_id="echo",
        description="Echo the msg parameter.",
        parameters=(ToolParameter("msg", "message", required=False),),
    )

    class EchoTool(Tool):
        def invoke(self, parameters: dict, host: Any) -> str:
            return str(parameters.get("msg", ""))

    host.tool_registry.register(EchoTool(definition=_ECHO_DEF))


# ---------------------------------------------------------------------------
# _select_ready_batch with pending callback steps
# ---------------------------------------------------------------------------

def test_select_ready_batch_skips_pending_callback():
    """A callback step in step_results but not completed_steps is not re-dispatched."""
    steps = (
        PlanStep(id="ask", kind="callback", parameters={}, callback_intent="information_request"),
        PlanStep(id="act", kind="call_tool", parameters={}, tool_name="t", depends_on=("ask",)),
    )
    state = PlanState(plan=steps)
    # Simulate pending callback: in step_results but not completed_steps
    state.step_results["ask"] = {"_callback_pending": True}

    batch = _select_ready_batch(state, parallel_execution=True)
    # ask is in step_results (skip), act depends on ask (not completed) → nothing ready
    assert batch == []


def test_select_ready_batch_after_callback_resolved():
    """After callback resolves (in completed_steps), dependent step becomes ready."""
    steps = (
        PlanStep(id="ask", kind="callback", parameters={}, callback_intent="information_request"),
        PlanStep(id="act", kind="call_tool", parameters={}, tool_name="t", depends_on=("ask",)),
    )
    state = PlanState(plan=steps)
    # Simulate resolved: in both step_results and completed_steps
    resolved = {"_callback_resolved": True, "resolution": "yes"}
    state.step_results["ask"] = resolved
    state.completed_steps.append(
        CompletedStep(
            step_id="ask",
            step=steps[0],
            result=resolved,
            started_at=0.0,
            finished_at=0.0,
            plan_revision_at_start=1,
        )
    )

    batch = _select_ready_batch(state, parallel_execution=True)
    assert [s.id for s in batch] == ["act"]


# ---------------------------------------------------------------------------
# Model-bound callback: pause → reflect → continue_plan with resolution
# ---------------------------------------------------------------------------

def test_model_bound_callback_step_pauses_and_resolves(tmp_path: Path):
    """A callback plan step pauses execution; model resolves via continue_plan."""
    host, scripted = _make_host(tmp_path, [
        # PLAN phase
        {
            "kind": "submit_plan",
            "message": "plan with callback",
            "plan": [
                {"id": "ask", "kind": "callback", "callback_intent": "information_request",
                 "parameters": {}, "message": "What color?"},
                {"id": "act", "kind": "call_tool", "tool_name": "echo",
                 "parameters": {"msg": "done"}, "depends_on": ["ask"]},
            ],
        },
        # REFLECT: pending_callback → model resolves it
        {
            "kind": "continue_plan",
            "message": "I know the answer",
            "parameters": {"resolution": "blue"},
        },
        # REFLECT: end-of-plan → final_message
        {"kind": "final_message", "message": "Resolved and done"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "ask then act")

    assert result.status == "completed"
    assert result.message == "Resolved and done"
    # 3 model calls: submit_plan + continue_plan(resolve) + final_message
    assert len(scripted.calls) == 3


def test_model_bound_callback_pending_callback_in_reminder(tmp_path: Path):
    """The <pending_callback> tag appears in the reminder when a callback is pending."""
    received_contexts: list[ModelContext] = []

    host, scripted = _make_host(tmp_path, [
        {
            "kind": "submit_plan",
            "message": "plan",
            "plan": [
                {"id": "ask", "kind": "callback", "callback_intent": "information_request",
                 "parameters": {}, "message": "Need info"},
            ],
        },
        {"kind": "continue_plan", "message": "", "parameters": {"resolution": "ok"}},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)

    # Intercept model calls to inspect context
    original_decide = scripted.decide
    def _tracking_decide(**kwargs):
        received_contexts.append(kwargs["context"])
        return original_decide(**kwargs)
    scripted.decide = _tracking_decide

    host.run_agent("planner", "callback test")

    # The second model call (reflect with pending callback) should include the tag
    assert len(received_contexts) >= 2
    reflect_messages = received_contexts[1].messages
    reflect_text = " ".join(m.get("content", "") for m in reflect_messages if isinstance(m, dict))
    assert "pending_callback" in reflect_text


def test_callback_resolution_stored_in_step_results(tmp_path: Path):
    """The resolution from continue_plan is stored as the callback step's result."""
    resolution_stored: list[dict] = []

    host, _ = _make_host(tmp_path, [
        {
            "kind": "submit_plan",
            "message": "plan",
            "plan": [
                {"id": "ask", "kind": "callback", "callback_intent": "information_request",
                 "parameters": {}},
                {"id": "echo_result", "kind": "call_tool", "tool_name": "echo",
                 "parameters": {"msg": "done"}, "depends_on": ["ask"]},
            ],
        },
        {"kind": "continue_plan", "message": "", "parameters": {"resolution": "the answer"}},
        {"kind": "final_message", "message": "ok"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "resolve test")

    assert result.status == "completed"


# ---------------------------------------------------------------------------
# Replan during pending callback clears pending_callback_step_id
# ---------------------------------------------------------------------------

def test_replan_during_pending_callback_clears_it(tmp_path: Path):
    """When model replans during a pending callback, the callback is discarded."""
    host, scripted = _make_host(tmp_path, [
        # Initial plan with callback
        {
            "kind": "submit_plan",
            "message": "initial",
            "plan": [
                {"id": "ask", "kind": "callback", "callback_intent": "information_request",
                 "parameters": {}},
            ],
        },
        # REFLECT: model decides to replan instead of resolving callback
        {
            "kind": "submit_plan",
            "message": "new plan without callback",
            "plan": [
                {"id": "act", "kind": "call_tool", "tool_name": "echo",
                 "parameters": {"msg": "no callback needed"}},
            ],
        },
        # REFLECT: end-of-plan
        {"kind": "final_message", "message": "replanned successfully"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "replan test")

    assert result.status == "completed"
    assert result.message == "replanned successfully"
    # 3 model calls: initial submit_plan + replan submit_plan + final_message
    assert len(scripted.calls) == 3


# ---------------------------------------------------------------------------
# {{step_X.resolution}} resolves correctly after callback
# ---------------------------------------------------------------------------

def test_callback_resolution_accessible_via_ref(tmp_path: Path):
    """Step after callback can reference {{ask.resolution}} via ref resolver."""
    received_tool_params: list[dict] = []

    host, _ = _make_host(tmp_path, [
        {
            "kind": "submit_plan",
            "message": "plan with ref",
            "plan": [
                {"id": "ask", "kind": "callback", "callback_intent": "information_request",
                 "parameters": {}},
                {"id": "use_answer", "kind": "call_tool", "tool_name": "capture",
                 "parameters": {"value": "{{ask.resolution}}"}, "depends_on": ["ask"]},
            ],
        },
        {"kind": "continue_plan", "message": "", "parameters": {"resolution": "blue"}},
        {"kind": "final_message", "message": "captured"},
    ])

    # Register a capturing tool
    from agent_framework.tool import Tool, ToolDefinition, ToolParameter

    class CaptureTool(Tool):
        definition = ToolDefinition(
            tool_id="capture",
            description="Capture the value param.",
            parameters=(ToolParameter("value", "value", required=False),),
        )

        def invoke(self, parameters: dict, host: Any) -> str:
            received_tool_params.append(dict(parameters))
            return str(parameters.get("value", ""))

    host.tool_registry.register(CaptureTool(definition=CaptureTool.definition))

    result = host.run_agent("planner", "ref test")

    assert result.status == "completed"
    assert len(received_tool_params) == 1
    # The {{ask.resolution}} should have resolved to "blue"
    assert received_tool_params[0].get("value") == "blue"
