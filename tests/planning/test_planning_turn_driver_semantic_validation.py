"""Tests for post-LLM semantic validation in PlanningTurnDriver.

Covers:
  - {{token}} reference validation for submit_plan
  - Inappropriate callback at end_of_plan with failed steps
  - Retry-on-first-failure / abort-on-second-failure behaviour
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from agent_framework.host import AgentHost
from agent_framework.model import ModelContext, ModelResponse
from agent_framework.planning.turn_driver import _extract_token_roots


# ---------------------------------------------------------------------------
# Unit: _extract_token_roots
# ---------------------------------------------------------------------------

def test_extract_token_roots_simple():
    assert _extract_token_roots("{{step_a}}") == {"step_a"}


def test_extract_token_roots_dotpath():
    assert _extract_token_roots("{{step_a.field}}") == {"step_a"}


def test_extract_token_roots_nested_dict():
    roots = _extract_token_roots({"k": "{{foo.bar}}", "nested": {"k2": "{{baz}}"}})
    assert roots == {"foo", "baz"}


def test_extract_token_roots_list():
    roots = _extract_token_roots(["{{x}}", "{{y.z}}", "literal"])
    assert roots == {"x", "y"}


def test_extract_token_roots_none():
    assert _extract_token_roots(None) == set()


def test_extract_token_roots_no_tokens():
    assert _extract_token_roots("just a string") == set()


def test_extract_token_roots_multiple_in_string():
    roots = _extract_token_roots("{{a}} and {{b.c}}")
    assert roots == {"a", "b"}


# ---------------------------------------------------------------------------
# Helpers for integration tests
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
            raise RuntimeError("No more scripted payloads")
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
        f"---\nid: {agent_id}\nrole: planner\nplanning:\n  enabled: true\n"
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


# ---------------------------------------------------------------------------
# Token-reference validation: initial plan (_plan_phase)
# ---------------------------------------------------------------------------

def test_initial_plan_with_bad_token_ref_injects_reminder_and_retries(tmp_path: Path):
    """A submit_plan referencing an unknown {{token}} root is rejected with a correction reminder."""
    good_step = {"id": "step_a", "kind": "call_tool", "tool_name": "echo", "parameters": {}}
    bad_step = {
        "id": "step_a", "kind": "call_tool", "tool_name": "echo",
        "parameters": {"msg": "{{step_nonexistent}}"},  # wrong step id
    }

    host, scripted = _make_host(tmp_path, [
        # First attempt: token root 'step_nonexistent' doesn't match any step id
        {"kind": "submit_plan", "message": "bad tokens", "plan": [bad_step]},
        # Corrected after reminder
        {"kind": "submit_plan", "message": "corrected", "plan": [good_step]},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "test")
    assert result.status == "completed"
    assert result.message == "done"


def test_initial_plan_with_bad_token_ref_twice_aborts(tmp_path: Path):
    """Two consecutive invalid {{token}} submissions abort execution."""
    bad_step = {
        "id": "step_a", "kind": "call_tool", "tool_name": "echo",
        "parameters": {"msg": "{{bad_ref}}"},
    }

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "bad", "plan": [bad_step]},
        {"kind": "submit_plan", "message": "still bad", "plan": [bad_step]},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "test")
    assert result is not None  # must terminate, not loop forever


def test_initial_plan_with_valid_invocation_param_token_passes(tmp_path: Path):
    """A {{token}} referencing an invocation parameter name is valid."""
    step = {
        "id": "step_a", "kind": "call_tool", "tool_name": "echo",
        "parameters": {"msg": "{{instruction}}"},  # 'instruction' is a built-in param
    }

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "ok", "plan": [step]},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "hello")
    assert result.status == "completed"


def test_initial_plan_inter_step_token_ref_passes(tmp_path: Path):
    """A step referencing another step's result via {{step_id}} is valid."""
    steps = [
        {"id": "step_a", "kind": "call_tool", "tool_name": "echo", "parameters": {}},
        {
            "id": "step_b", "kind": "call_tool", "tool_name": "echo",
            "parameters": {"msg": "{{step_a}}"},
            "depends_on": ["step_a"],
        },
    ]

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "chained", "plan": steps},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "chain test")
    assert result.status == "completed"


# ---------------------------------------------------------------------------
# Token-reference validation: replan (_reflect_phase submit_plan)
# ---------------------------------------------------------------------------

def test_replan_with_bad_abbreviated_token_ref_injects_reminder(tmp_path: Path):
    """A replan that uses abbreviated step id tokens (wrong names) is rejected."""
    initial_step = {
        "id": "step_get_actor_details", "kind": "call_tool", "tool_name": "echo",
        "parameters": {},
    }
    # Replan uses abbreviated/wrong token root 'step_get_actor' instead of 'step_get_actor_details'
    replan_step = {
        "id": "step_route", "kind": "call_tool", "tool_name": "echo",
        "parameters": {"msg": "{{step_get_actor}}"},  # wrong: should be step_get_actor_details
    }
    corrected_replan_step = {
        "id": "step_route", "kind": "call_tool", "tool_name": "echo",
        "parameters": {"msg": "{{step_get_actor_details}}"},  # correct
    }

    host, scripted = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "initial plan", "plan": [initial_step]},
        # After initial step completes → reflect → replan with wrong token
        {"kind": "submit_plan", "message": "replan bad", "plan": [replan_step]},
        # After reminder → corrected replan
        {"kind": "submit_plan", "message": "replan good", "plan": [corrected_replan_step]},
        {"kind": "final_message", "message": "success"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "test")
    assert result.status == "completed"
    assert result.message == "success"


def test_replan_referencing_completed_step_id_token_passes(tmp_path: Path):
    """A replan may use {{completed_step_id}} references since those steps are done."""
    initial_step = {
        "id": "gather", "kind": "call_tool", "tool_name": "echo", "parameters": {},
    }
    # Replan references 'gather' which is now a completed step
    replan_step = {
        "id": "process", "kind": "call_tool", "tool_name": "echo",
        "parameters": {"msg": "{{gather}}"},
    }

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "phase1", "plan": [initial_step]},
        {"kind": "submit_plan", "message": "phase2", "plan": [replan_step]},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "test")
    assert result.status == "completed"


# ---------------------------------------------------------------------------
# Callback-at-end-of-plan with failed steps
# ---------------------------------------------------------------------------

def test_callback_at_eop_with_failed_steps_is_rejected(tmp_path: Path):
    """Emitting callback at end_of_plan when steps have errors is blocked with a reminder."""
    from agent_framework.tool import Tool, ToolDefinition

    class BrokenTool(Tool):
        definition = ToolDefinition(tool_id="broken", description="Raises.", parameters=())

        def invoke(self, parameters: dict, host: Any) -> str:
            raise RuntimeError("deliberate failure")

    host, scripted = _make_host(tmp_path, [
        {
            "kind": "submit_plan",
            "message": "will fail",
            "plan": [{"id": "fail_step", "kind": "call_tool", "tool_name": "broken", "parameters": {}}],
        },
        # Reflect: model misattributes step error as user ambiguity → callback
        {"kind": "callback", "intent": "information_request", "message": "need clarification"},
        # After reminder: model emits final_message instead
        {"kind": "final_message", "message": "plan failed due to step error"},
    ])
    host.tool_registry.register(BrokenTool(definition=BrokenTool.definition))

    result = host.run_agent("planner", "test")
    assert result.status == "completed"
    assert result.message == "plan failed due to step error"


def test_callback_at_eop_with_failed_steps_twice_aborts(tmp_path: Path):
    """Two consecutive inappropriate callbacks at end_of_plan abort execution."""
    from agent_framework.tool import Tool, ToolDefinition

    class BrokenTool(Tool):
        definition = ToolDefinition(tool_id="broken2", description="Raises.", parameters=())

        def invoke(self, parameters: dict, host: Any) -> str:
            raise RuntimeError("always fails")

    host, _ = _make_host(tmp_path, [
        {
            "kind": "submit_plan",
            "message": "fail plan",
            "plan": [{"id": "fail_step", "kind": "call_tool", "tool_name": "broken2", "parameters": {}}],
        },
        {"kind": "callback", "intent": "information_request", "message": "clarify?"},
        {"kind": "callback", "intent": "information_request", "message": "clarify again?"},
    ])
    host.tool_registry.register(BrokenTool(definition=BrokenTool.definition))

    result = host.run_agent("planner", "test")
    assert result is not None  # must terminate


def test_callback_at_eop_without_failed_steps_passes():
    """_validate_reflect_callback returns None when no steps have errors."""
    from agent_framework.agents.agent_decision import AgentDecision
    from agent_framework.planning.plan_state import PlanState, PlanStep
    from agent_framework.planning.turn_driver import PlanningTurnDriver

    step = PlanStep(id="s", kind="call_tool", tool_name="echo", parameters={}, depends_on=())
    plan_state = PlanState(plan=(step,), plan_revision=1)
    plan_state.step_results["s"] = "success"  # no error key

    decision = AgentDecision(kind="callback", callback_intent="proposal_review")
    error = PlanningTurnDriver._validate_reflect_callback(decision, plan_state, end_of_plan=True)
    assert error is None


# ---------------------------------------------------------------------------
# Semantic failure counter resets between independent failures
# ---------------------------------------------------------------------------

def test_semantic_failure_counter_resets_on_valid_replan(tmp_path: Path):
    """A valid replan resets the counter; a subsequent bad token error is retried again."""
    # Each phase uses distinct step IDs — completed IDs are immutable and cannot be reused.
    host, _ = _make_host(tmp_path, [
        # PLAN phase: bad initial plan (bad token ref) → reminder → good initial plan
        {"kind": "submit_plan", "message": "bad",
         "plan": [{"id": "s1", "kind": "call_tool", "tool_name": "echo",
                   "parameters": {"msg": "{{bad_ref}}"}}]},
        {"kind": "submit_plan", "message": "good",
         "plan": [{"id": "s1", "kind": "call_tool", "tool_name": "echo", "parameters": {}}]},
        # s1 completes → REFLECT: bad replan (counter resets to 0 after good initial; this is attempt 1)
        {"kind": "submit_plan", "message": "bad again",
         "plan": [{"id": "s2", "kind": "call_tool", "tool_name": "echo",
                   "parameters": {"msg": "{{bad_ref2}}"}}]},
        # Corrected replan (counter was 1 → resets to 0 on success)
        {"kind": "submit_plan", "message": "good again",
         "plan": [{"id": "s2", "kind": "call_tool", "tool_name": "echo", "parameters": {}}]},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "test")
    assert result.status == "completed"
    assert result.message == "done"


# ---------------------------------------------------------------------------
# Replan preserves completed step results (core fix)
# ---------------------------------------------------------------------------

def test_replan_preserves_completed_step_results(tmp_path: Path):
    """Completed step results remain in step_results after a replan and resolve correctly."""
    received: list[dict] = []

    from agent_framework.tool import Tool, ToolDefinition, ToolParameter

    class RecordingTool(Tool):
        definition = ToolDefinition(
            tool_id="record",
            description="Records parameters passed to it.",
            parameters=(ToolParameter("value", "value to record", required=False),),
        )

        def invoke(self, parameters: dict, host: Any) -> str:
            received.append(dict(parameters))
            return "recorded"

    initial_steps = [
        {"id": "fetch", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "data"}},
    ]
    replan_step = {
        "id": "use_fetch", "kind": "call_tool", "tool_name": "record",
        "parameters": {"value": "{{fetch}}"},  # references completed step
    }

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "phase1", "plan": initial_steps},
        {"kind": "submit_plan", "message": "phase2", "plan": [replan_step]},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)
    host.tool_registry.register(RecordingTool(definition=RecordingTool.definition))

    result = host.run_agent("planner", "test")
    assert result.status == "completed"
    assert len(received) == 1
    assert received[0]["value"] == "data"  # {{fetch}} resolved to echo output, not empty string


def test_replan_rejects_completed_step_id_collision(tmp_path: Path):
    """A replan that redefines a completed step ID is rejected with a reminder; corrected plan succeeds."""
    initial_step = {"id": "gather", "kind": "call_tool", "tool_name": "echo", "parameters": {}}
    # Model tries to reuse same step ID — invalid
    colliding_replan = {"id": "gather", "kind": "call_tool", "tool_name": "echo", "parameters": {}}
    # Corrected with a different ID
    corrected_replan = {"id": "process", "kind": "call_tool", "tool_name": "echo", "parameters": {}}

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "initial", "plan": [initial_step]},
        # Reflect: replan with colliding ID → reminder
        {"kind": "submit_plan", "message": "bad replan", "plan": [colliding_replan]},
        # Corrected
        {"kind": "submit_plan", "message": "good replan", "plan": [corrected_replan]},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "test")
    assert result.status == "completed"
    assert result.message == "done"


def test_replan_collision_twice_aborts(tmp_path: Path):
    """Two consecutive replans colliding with a completed step ID abort execution."""
    initial_step = {"id": "gather", "kind": "call_tool", "tool_name": "echo", "parameters": {}}
    colliding = {"id": "gather", "kind": "call_tool", "tool_name": "echo", "parameters": {}}

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "initial", "plan": [initial_step]},
        {"kind": "submit_plan", "message": "bad1", "plan": [colliding]},
        {"kind": "submit_plan", "message": "bad2", "plan": [colliding]},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "test")
    assert result is not None  # must terminate


def test_replan_scenario_append(tmp_path: Path):
    """Scenario 2 (append): model re-lists pending steps plus a new one at the end."""
    received: list[dict] = []

    from agent_framework.tool import Tool, ToolDefinition, ToolParameter

    class RecordingTool(Tool):
        definition = ToolDefinition(
            tool_id="record2",
            description="Records parameters.",
            parameters=(ToolParameter("v", "value", required=False),),
        )

        def invoke(self, parameters: dict, host: Any) -> str:
            received.append(dict(parameters))
            return f"recorded:{parameters.get('v', '')}"

    # Initial plan: a, b. Executed: a. Then model replans appending x after b.
    initial_plan = [
        {"id": "step_a", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "a_result"}},
    ]
    # After a completes, replan = [b, x] where x depends on b
    replan = [
        {"id": "step_b", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "b_result"}},
        {"id": "step_x", "kind": "call_tool", "tool_name": "record2",
         "parameters": {"v": "{{step_b}}"}, "depends_on": ["step_b"]},
    ]

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "initial", "plan": initial_plan},
        {"kind": "submit_plan", "message": "append x", "plan": replan},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)
    host.tool_registry.register(RecordingTool(definition=RecordingTool.definition))

    result = host.run_agent("planner", "test")
    assert result.status == "completed"
    assert len(received) == 1
    assert received[0]["v"] == "b_result"  # step_x received step_b's output


def test_replan_scenario_replace_future(tmp_path: Path):
    """Scenario 3 (replace): model replaces remaining pending steps with a single new one."""
    received: list[dict] = []

    from agent_framework.tool import Tool, ToolDefinition, ToolParameter

    class RecordingTool(Tool):
        definition = ToolDefinition(
            tool_id="record3",
            description="Records parameters.",
            parameters=(ToolParameter("v", "value", required=False),),
        )

        def invoke(self, parameters: dict, host: Any) -> str:
            received.append(dict(parameters))
            return "ok"

    # Initial plan executes 'gather'. Model replaces all future work with 'summarise'.
    initial_plan = [
        {"id": "gather", "kind": "call_tool", "tool_name": "echo", "parameters": {"msg": "gathered"}},
    ]
    replan = [
        {"id": "summarise", "kind": "call_tool", "tool_name": "record3",
         "parameters": {"v": "{{gather}}"}},  # references the completed step
    ]

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "initial", "plan": initial_plan},
        {"kind": "submit_plan", "message": "replace", "plan": replan},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)
    host.tool_registry.register(RecordingTool(definition=RecordingTool.definition))

    result = host.run_agent("planner", "test")
    assert result.status == "completed"
    assert received[0]["v"] == "gathered"  # {{gather}} resolved correctly


def test_replan_drops_pending_callback_sentinel(tmp_path: Path):
    """A _callback_pending sentinel from a callback step not in the new plan is removed."""
    from agent_framework.planning.plan_state import PlanState, PlanStep

    # We'll inspect plan_state directly after the replan by having final_message return.
    # Use two steps: one real, one callback. Replan after real step completes, excluding callback.
    initial_plan = [
        {"id": "real_step", "kind": "call_tool", "tool_name": "echo", "parameters": {}},
        {"id": "ask_user", "kind": "callback", "callback_intent": "information_request",
         "parameters": {}, "depends_on": ["real_step"]},
    ]
    replan = [
        {"id": "finish", "kind": "call_tool", "tool_name": "echo", "parameters": {}},
    ]

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "initial", "plan": initial_plan},
        # real_step runs; ask_user becomes pending callback; reflect → replan without it
        {"kind": "submit_plan", "message": "replan", "plan": replan},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)

    result = host.run_agent("planner", "test")
    assert result.status == "completed"
    # The run must not hang and must succeed — pending callback sentinel was cleaned up.


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def test_semantic_failure_logged_at_error(tmp_path: Path, caplog):
    """Semantic validation failures are logged at ERROR level."""
    bad_step = {"id": "s", "kind": "call_tool", "tool_name": "echo",
                "parameters": {"msg": "{{unknown_step}}"}}
    good_step = {"id": "s", "kind": "call_tool", "tool_name": "echo", "parameters": {}}

    host, _ = _make_host(tmp_path, [
        {"kind": "submit_plan", "message": "bad", "plan": [bad_step]},
        {"kind": "submit_plan", "message": "good", "plan": [good_step]},
        {"kind": "final_message", "message": "done"},
    ])
    _register_echo(host)

    with caplog.at_level(logging.ERROR, logger="agent_framework.planning.turn_driver"):
        host.run_agent("planner", "test")

    assert any("semantic validation failed" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Subagent result stored as dict — {{step.response.field}} and list indexing
# ---------------------------------------------------------------------------

def _register_subagent(host: AgentHost, subagent_id: str, response_payload: dict) -> None:
    """Register a child agent that immediately returns a final_message with response_payload."""
    import textwrap
    from pathlib import Path as _Path

    sub_path = _Path(host.config.agent_directory) / f"{subagent_id}.md"
    sub_path.write_text(
        textwrap.dedent(f"""\
            ---
            id: {subagent_id}
            role: worker
            ---
            You are a worker.
            ---
            Do the work.
        """),
        encoding="utf-8",
    )

    class _FixedDriver:
        def set_trace_callbacks(self, *, on_request=None, on_response=None):
            pass

        def decide(self, *, agent_id, provider_name, model_names, temperature, context):
            from agent_framework.model import ModelResponse
            payload = {"kind": "final_message", "message": "sub done", "response": response_payload}
            return ModelResponse(payload=payload, raw_text=str(payload))

    host.model_driver = _FixedDriver()


def test_subagent_step_result_stored_as_dict(tmp_path: Path):
    """call_subagent step results are stored as dict with message/response/status keys."""
    stored: dict[str, Any] = {}

    from agent_framework.tool import Tool, ToolDefinition, ToolParameter

    class CaptureTool(Tool):
        definition = ToolDefinition(
            tool_id="capture",
            description="Capture the value parameter.",
            parameters=(ToolParameter("value", "value", required=False),),
        )

        def invoke(self, parameters: dict, host: Any) -> str:
            stored.update(parameters)
            return "captured"

    sub_id = "worker_sub"
    response_data = {"items": ["alpha", "beta"], "count": 2}

    # Two-driver approach: first driver handles the planner, but we need a fixed
    # subagent driver. Use _ScriptedDriver for planner, override for subagent calls.
    agent_path = tmp_path / "planner.md"
    agent_path.write_text(
        "---\nid: planner\nrole: planner\nplanning:\n  enabled: true\n\n"
        "---\nYou are a planning agent.\n---\nExecute the plan.\n",
        encoding="utf-8",
    )
    sub_path = tmp_path / f"{sub_id}.md"
    sub_path.write_text(
        f"---\nid: {sub_id}\nrole: worker\n---\nYou are a worker.\n---\nDo work.\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "OPENAI_API_KEY=test-key",
            "DEFAULT_PROVIDER=openai",
            "DEFAULT_MODEL=gpt-4o-mini",
            f"AGENT_DIRECTORY={tmp_path}",
            "ROOT_AGENT=planner",
        ]),
        encoding="utf-8",
    )
    host = AgentHost.from_env(env)
    host.tool_registry.register(CaptureTool(definition=CaptureTool.definition))

    planner_payloads = [
        {
            "kind": "submit_plan",
            "message": "call sub then use result",
            "plan": [
                {"id": "sub_step", "kind": "call_subagent", "subagent_id": sub_id, "parameters": {}},
                {
                    "id": "use_step", "kind": "call_tool", "tool_name": "capture",
                    "parameters": {"value": "{{sub_step.response.items.0}}"},
                    "depends_on": ["sub_step"],
                },
            ],
        },
        {"kind": "final_message", "message": "done"},
    ]

    class _SplitDriver:
        """Routes to planner payloads for the planner agent, fixed response for subagent."""
        _planner_payloads = list(planner_payloads)

        def set_trace_callbacks(self, *, on_request=None, on_response=None):
            pass

        def decide(self, *, agent_id, provider_name, model_names, temperature, context):
            from agent_framework.model import ModelResponse
            if agent_id == "planner":
                payload = self._planner_payloads.pop(0)
            else:
                payload = {"kind": "final_message", "message": "sub done", "response": response_data}
            return ModelResponse(payload=payload, raw_text=str(payload))

    host.model_driver = _SplitDriver()

    result = host.run_agent("planner", "test subagent result")
    assert result.status == "completed"
    # The capture tool should have received "alpha" (items[0]), not a literal token string
    assert stored.get("value") == "alpha", f"Expected 'alpha', got {stored.get('value')!r}"


def test_subagent_step_response_list_index_resolves(tmp_path: Path):
    """{{sub.response.declared_intents.0.action}} resolves end-to-end."""
    stored: dict[str, Any] = {}

    from agent_framework.tool import Tool, ToolDefinition, ToolParameter

    class CaptureTool(Tool):
        definition = ToolDefinition(
            tool_id="capture2",
            description="Capture intent.",
            parameters=(ToolParameter("intent", "intent", required=False),),
        )

        def invoke(self, parameters: dict, host: Any) -> str:
            stored.update(parameters)
            return "ok"

    sub_id = "intent_parser"
    response_data = {"declared_intents": [{"action": "move", "target": "north"}]}

    agent_path = tmp_path / "planner.md"
    agent_path.write_text(
        "---\nid: planner\nrole: planner\nplanning:\n  enabled: true\n\n"
        "---\nYou are a planning agent.\n---\nExecute the plan.\n",
        encoding="utf-8",
    )
    sub_path = tmp_path / f"{sub_id}.md"
    sub_path.write_text(
        f"---\nid: {sub_id}\nrole: worker\n---\nYou are a worker.\n---\nDo work.\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "OPENAI_API_KEY=test-key",
            "DEFAULT_PROVIDER=openai",
            "DEFAULT_MODEL=gpt-4o-mini",
            f"AGENT_DIRECTORY={tmp_path}",
            "ROOT_AGENT=planner",
        ]),
        encoding="utf-8",
    )
    host = AgentHost.from_env(env)
    host.tool_registry.register(CaptureTool(definition=CaptureTool.definition))

    planner_payloads = [
        {
            "kind": "submit_plan",
            "message": "parse intent then route",
            "plan": [
                {"id": "parse", "kind": "call_subagent", "subagent_id": sub_id, "parameters": {}},
                {
                    "id": "route", "kind": "call_tool", "tool_name": "capture2",
                    "parameters": {"intent": "{{parse.response.declared_intents.0}}"},
                    "depends_on": ["parse"],
                },
            ],
        },
        {"kind": "final_message", "message": "routed"},
    ]

    class _SplitDriver:
        _planner_payloads = list(planner_payloads)

        def set_trace_callbacks(self, *, on_request=None, on_response=None):
            pass

        def decide(self, *, agent_id, provider_name, model_names, temperature, context):
            from agent_framework.model import ModelResponse
            if agent_id == "planner":
                payload = self._planner_payloads.pop(0)
            else:
                payload = {"kind": "final_message", "message": "parsed", "response": response_data}
            return ModelResponse(payload=payload, raw_text=str(payload))

    host.model_driver = _SplitDriver()

    result = host.run_agent("planner", "route intent")
    assert result.status == "completed"
    # route step received the full first intent dict, not a literal token
    assert stored.get("intent") == {"action": "move", "target": "north"}, \
        f"Expected intent dict, got {stored.get('intent')!r}"
