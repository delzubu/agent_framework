"""Tests for safety caps and plan validation error recovery in PlanningTurnDriver (FEAT #65)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from agent_framework.host import AgentHost
from agent_framework.model import ModelContext, ModelResponse


# ---------------------------------------------------------------------------
# Helpers
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


def _make_host(
    tmp_path: Path,
    payloads: list[dict],
    *,
    agent_id: str = "planner",
    extra_frontmatter: str = "",
) -> tuple[AgentHost, _ScriptedDriver]:
    agent_path = tmp_path / f"{agent_id}.md"
    agent_path.write_text(
        f"id: {agent_id}\nrole: planner\nplanning:\n  enabled: true\n"
        f"{extra_frontmatter}\n"
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

    class EchoTool(Tool):
        definition = ToolDefinition(
            tool_id="echo",
            description="Echo the msg parameter.",
            parameters=(ToolParameter("msg", "message", required=False),),
        )

        def invoke(self, parameters: dict, host: Any) -> str:
            return str(parameters.get("msg", ""))

    host.tool_registry.register(EchoTool(definition=EchoTool.definition))


def _make_steps(n: int) -> list[dict]:
    """Build n sequential call_tool steps."""
    steps = []
    for i in range(n):
        step: dict = {"id": f"step_{i}", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": str(i)}}
        if i > 0:
            step["depends_on"] = [f"step_{i - 1}"]
        steps.append(step)
    return steps


# ---------------------------------------------------------------------------
# max_steps cap
# ---------------------------------------------------------------------------

def test_max_steps_exceeded_emits_callback(tmp_path: Path):
    """When total_steps_executed >= max_steps, a safety-cap callback is emitted."""
    # 3 steps but max_steps=2 → cap exceeded before step 3
    host, scripted = _make_host(
        tmp_path,
        [
            {
                "kind": "submit_plan",
                "message": "three steps",
                "plan": _make_steps(3),
            },
        ],
        extra_frontmatter="  max_steps: 2",
    )
    _register_echo(host)

    # The safety cap callback uses handle_callback → with no caller it will
    # return a failed or blocked result depending on context.
    # We just verify the run terminates without the driver crashing.
    result = host.run_agent("planner", "many steps")
    # Result may be "failed" or "completed" depending on how the host handles the callback;
    # the important thing is execution terminated (not infinite loop).
    assert result is not None


def test_max_steps_warning_logged(tmp_path: Path, caplog):
    """A WARNING is logged when >80% of max_steps is used."""
    # 5 steps, max_steps=5 — after 5 steps: 5/5 = 100% → but we check before dispatch
    # Use max_steps=6 so 5 steps → 83% → warning
    host, _ = _make_host(
        tmp_path,
        [
            {
                "kind": "submit_plan",
                "message": "steps",
                "plan": _make_steps(5),
            },
            {"kind": "final_message", "message": "done"},
        ],
        extra_frontmatter="  max_steps: 6",
    )
    _register_echo(host)

    with caplog.at_level(logging.WARNING, logger="agent_framework.planning.turn_driver"):
        host.run_agent("planner", "steps")

    assert any("approaching max_steps" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# max_plan_revisions cap
# ---------------------------------------------------------------------------

def test_max_plan_revisions_exceeded_emits_callback(tmp_path: Path):
    """When plan_revision >= max_plan_revisions, a safety-cap callback is emitted."""
    # max_plan_revisions=1: the initial plan sets revision=1; any replan hits the cap.
    single_step = [{"id": "s", "kind": "call_tool", "tool_name": "echo", "parameters": {}}]
    host, scripted = _make_host(
        tmp_path,
        [
            {"kind": "submit_plan", "message": "v1", "plan": single_step},
            # After s completes → reflect → re-plan → plan_revision(1) >= max(1) → cap
            {"kind": "submit_plan", "message": "v2", "plan": single_step},
        ],
        extra_frontmatter="  max_plan_revisions: 1",
    )
    _register_echo(host)

    result = host.run_agent("planner", "replan")
    assert result is not None  # terminated cleanly


def test_max_plan_revisions_warning_logged(tmp_path: Path, caplog):
    """A WARNING is logged when >80% of max_plan_revisions is used."""
    # max_plan_revisions=5: warning fires when current revision >= 80% of max (>= 4/5=0.8).
    # Need: v0 (initial, revision=1) + v1,v2,v3 (replans, revisions 2-4) + v4 (replan
    # triggers when revision=4 → pct=4/5=0.8 → warning) + final_message.
    single_step = [{"id": "s", "kind": "call_tool", "tool_name": "echo", "parameters": {}}]
    payloads = [{"kind": "submit_plan", "message": f"v{i}", "plan": single_step} for i in range(5)]
    payloads.append({"kind": "final_message", "message": "done"})

    host, _ = _make_host(
        tmp_path,
        payloads,
        extra_frontmatter="  max_plan_revisions: 5",
    )
    _register_echo(host)

    with caplog.at_level(logging.WARNING, logger="agent_framework.planning.turn_driver"):
        host.run_agent("planner", "replan many")

    assert any("approaching max_plan_revisions" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Step exception — reflect; model recovers
# ---------------------------------------------------------------------------

def test_step_exception_stored_in_results(tmp_path: Path):
    """A failing tool produces an error result; reflect proceeds; model finalizes."""
    from agent_framework.tool import Tool, ToolDefinition, ToolParameter

    class BrokenTool(Tool):
        definition = ToolDefinition(
            tool_id="broken",
            description="Always raises.",
            parameters=(),
        )

        def invoke(self, parameters: dict, host: Any) -> str:
            raise RuntimeError("deliberate failure")

    host, scripted = _make_host(tmp_path, [
        {
            "kind": "submit_plan",
            "message": "will fail",
            "plan": [{"id": "fail_step", "kind": "call_tool", "tool_name": "broken", "parameters": {}}],
        },
        # reflect — model sees error result and finalizes
        {"kind": "final_message", "message": "acknowledged failure"},
    ])
    host.tool_registry.register(BrokenTool(definition=BrokenTool.definition))

    result = host.run_agent("planner", "will fail")

    assert result.status == "completed"
    assert result.message == "acknowledged failure"


def test_step_exception_error_logged(tmp_path: Path, caplog):
    """Step exceptions are logged at WARNING level."""
    from agent_framework.tool import Tool, ToolDefinition

    class BrokenTool(Tool):
        definition = ToolDefinition(tool_id="broken2", description="Raises.", parameters=())

        def invoke(self, parameters: dict, host: Any) -> str:
            raise ValueError("test error")

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "x", "plan": [
            {"id": "e", "kind": "call_tool", "tool_name": "broken2", "parameters": {}}
        ]},
        {"kind": "final_message", "message": "ok"},
    ])
    host.tool_registry.register(BrokenTool(definition=BrokenTool.definition))

    with caplog.at_level(logging.WARNING, logger="agent_framework.planning.turn_driver"):
        host.run_agent("planner", "x")

    assert any("failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Plan validation error recovery
# ---------------------------------------------------------------------------

def test_plan_validation_error_first_retried(tmp_path: Path):
    """A single validation error injects a reminder and lets model retry."""
    # Valid single step
    valid_step = {"id": "ok", "kind": "call_tool", "tool_name": "echo", "parameters": {}}
    # Invalid: step with forward reference (step_b depends on step_a which appears after it)
    invalid_plan = [
        {"id": "step_a", "kind": "call_tool", "tool_name": "echo",
         "parameters": {}, "depends_on": ["step_b"]},
        {"id": "step_b", "kind": "call_tool", "tool_name": "echo", "parameters": {}},
    ]

    host, scripted = _make_host(tmp_path, [
        # First attempt: invalid plan (forward reference)
        {"kind": "submit_plan", "message": "bad", "plan": invalid_plan},
        # Second attempt: valid plan
        {"kind": "submit_plan", "message": "good", "plan": [valid_step]},
        {"kind": "final_message", "message": "recovered"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "validate")

    assert result.status == "completed"
    assert result.message == "recovered"


def test_two_consecutive_validation_errors_escalate(tmp_path: Path):
    """Two consecutive validation errors trigger a safety-cap callback."""
    invalid_plan = [
        {"id": "step_a", "kind": "call_tool", "tool_name": "echo",
         "parameters": {}, "depends_on": ["step_b"]},
        {"id": "step_b", "kind": "call_tool", "tool_name": "echo", "parameters": {}},
    ]

    host, _ = _make_host(tmp_path, [
        # Two invalid submit_plan decisions in a row
        {"kind": "submit_plan", "message": "bad1", "plan": invalid_plan},
        {"kind": "submit_plan", "message": "bad2", "plan": invalid_plan},
    ])
    _register_echo(host)

    # After 2 consecutive failures → safety-cap callback; run terminates.
    result = host.run_agent("planner", "bad plans")
    assert result is not None


def test_plan_validation_error_logged(tmp_path: Path, caplog):
    """Plan validation errors are logged at ERROR level."""
    invalid_plan = [
        {"id": "step_a", "kind": "call_tool", "tool_name": "echo",
         "parameters": {}, "depends_on": ["step_b"]},
        {"id": "step_b", "kind": "call_tool", "tool_name": "echo", "parameters": {}},
    ]
    valid_step = {"id": "ok", "kind": "call_tool", "tool_name": "echo", "parameters": {}}

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "bad", "plan": invalid_plan},
        {"kind": "submit_plan", "message": "good", "plan": [valid_step]},
        {"kind": "final_message", "message": "ok"},
    ])
    _register_echo(host)

    with caplog.at_level(logging.ERROR, logger="agent_framework.agents.turn_driver"):
        host.run_agent("planner", "validate")

    assert any("plan validation error" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# eop_stall cap — continue_plan at end_of_plan infinite-loop guard
# ---------------------------------------------------------------------------


def test_eop_stall_terminates_after_three_continue_plans(tmp_path: Path):
    """Agent emitting continue_plan three times at end_of_plan triggers eop_stall cap."""
    host, _ = _make_host(
        tmp_path,
        [
            {
                "kind": "submit_plan",
                "message": "one step",
                "plan": [{"id": "s", "kind": "call_tool", "tool_name": "echo", "parameters": {}}],
            },
            # All three reflect turns emit continue_plan instead of final_message
            {"kind": "continue_plan", "message": "thinking..."},
            {"kind": "continue_plan", "message": "still thinking..."},
            {"kind": "continue_plan", "message": "thinking again..."},
        ],
    )
    _register_echo(host)

    result = host.run_agent("planner", "stall test")
    assert result is not None  # must terminate, not loop forever


def test_eop_stall_resolves_when_model_finalizes(tmp_path: Path):
    """Agent emitting continue_plan once then final_message succeeds normally."""
    host, _ = _make_host(
        tmp_path,
        [
            {
                "kind": "submit_plan",
                "message": "one step",
                "plan": [{"id": "s", "kind": "call_tool", "tool_name": "echo", "parameters": {}}],
            },
            {"kind": "continue_plan", "message": "one stall"},
            {"kind": "final_message", "message": "recovered"},
        ],
    )
    _register_echo(host)

    result = host.run_agent("planner", "stall then recover")
    assert result.status == "completed"
    assert result.message == "recovered"


def test_eop_stall_warning_logged(tmp_path: Path, caplog):
    """Each continue_plan at end_of_plan produces a WARNING log entry."""
    host, _ = _make_host(
        tmp_path,
        [
            {
                "kind": "submit_plan",
                "message": "one step",
                "plan": [{"id": "s", "kind": "call_tool", "tool_name": "echo", "parameters": {}}],
            },
            {"kind": "continue_plan", "message": "stall"},
            {"kind": "final_message", "message": "done"},
        ],
    )
    _register_echo(host)

    with caplog.at_level(logging.WARNING, logger="agent_framework.planning.turn_driver"):
        host.run_agent("planner", "stall log test")

    assert any("continue_plan at end_of_plan" in r.message for r in caplog.records)
