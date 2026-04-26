"""Tests for TurnDriver protocol and StandardTurnDriver.

Covers:
- StandardTurnDriver produces the same AgentResult as the pre-refactor inline loop.
- planning_override=False forces StandardTurnDriver.
- planning_override=True falls back to StandardTurnDriver (PlanningTurnDriver not yet
  implemented) and does not raise.
- post-agent-hooks continue_run branch is preserved by the new loop structure.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_framework.agent import Agent, AgentResult
from agent_framework.agents.turn_driver import StandardTurnDriver, TurnDriver
from agent_framework.host import AgentHost
from agent_framework.model import ModelContext, ModelResponse


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

class _FakeDriver:
    """Sequentially returns preset payloads."""

    def __init__(self, payloads: list[dict]):
        self._payloads = list(payloads)
        self.calls: list[ModelContext] = []

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        pass

    def decide(self, *, agent_id, provider_name, model_names, temperature, context: ModelContext):
        self.calls.append(context)
        payload = self._payloads.pop(0)
        return ModelResponse(payload=payload, raw_text=str(payload))


def _make_agent(tmp_path: Path, *, agent_id: str = "test_agent") -> Path:
    """Write a minimal agent markdown file and return its path."""
    path = tmp_path / f"{agent_id}.md"
    path.write_text(
        f"""---
id: {agent_id}
role: assistant
parameters:
  instruction:
    description: What to do.
    required: false
---
You are a test assistant.
---
Do: {{{{instruction}}}}
""",
        encoding="utf-8",
    )
    return path


def _make_host(tmp_path: Path, payloads: list[dict], *, agent_id: str = "test_agent") -> AgentHost:
    """Return a minimal AgentHost wired to a fake driver."""
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
    host.model_driver = _FakeDriver(payloads)
    return host


# ---------------------------------------------------------------------------
# TurnDriver protocol structural check
# ---------------------------------------------------------------------------

def test_standard_turn_driver_satisfies_protocol():
    """StandardTurnDriver must have run_turn with the right signature."""
    driver = StandardTurnDriver()
    assert callable(getattr(driver, "run_turn", None))


# ---------------------------------------------------------------------------
# Behavior-equivalence: StandardTurnDriver produces the correct AgentResult
# ---------------------------------------------------------------------------

def test_standard_turn_driver_single_final_message(tmp_path: Path):
    """A one-shot final_message decision returns the expected AgentResult."""
    _make_agent(tmp_path)
    host = _make_host(tmp_path, [{"kind": "final_message", "message": "hello from driver"}])
    result = host.run_agent("test_agent", "say hello")
    assert result.status == "completed"
    assert result.message == "hello from driver"


def test_standard_turn_driver_tool_then_final(tmp_path: Path):
    """A tool call followed by final_message loops correctly via the driver."""
    (tmp_path / "tools").mkdir(exist_ok=True)
    _make_agent(tmp_path)
    host = _make_host(
        tmp_path,
        [
            {"kind": "call_tool", "tool_name": "nonexistent_tool", "parameters": {}},
            {"kind": "final_message", "message": "done"},
        ],
    )
    # nonexistent_tool will return an error message; the agent loop should continue
    result = host.run_agent("test_agent", "do something")
    assert result.status == "completed"
    assert result.message == "done"


# ---------------------------------------------------------------------------
# planning_override forwarding
# ---------------------------------------------------------------------------

def test_planning_override_false_uses_standard_driver(tmp_path: Path):
    """planning_override=False must select StandardTurnDriver (no error)."""
    _make_agent(tmp_path)
    host = _make_host(tmp_path, [{"kind": "final_message", "message": "ok"}])
    result = host.run_agent("test_agent", "go", planning_override=False)
    assert result.status == "completed"


def test_planning_override_true_falls_back_gracefully(tmp_path: Path):
    """planning_override=True falls back to StandardTurnDriver until PlanningTurnDriver
    is implemented; must not raise and must complete normally."""
    _make_agent(tmp_path)
    host = _make_host(tmp_path, [{"kind": "final_message", "message": "ok"}])
    result = host.run_agent("test_agent", "go", planning_override=True)
    assert result.status == "completed"


def test_planning_override_none_uses_standard_driver(tmp_path: Path):
    """planning_override=None (default) must select StandardTurnDriver."""
    _make_agent(tmp_path)
    host = _make_host(tmp_path, [{"kind": "final_message", "message": "ok"}])
    result = host.run_agent("test_agent", "go", planning_override=None)
    assert result.status == "completed"


# ---------------------------------------------------------------------------
# _select_turn_driver directly
# ---------------------------------------------------------------------------

def test_select_turn_driver_returns_standard_for_false(tmp_path: Path):
    agent_path = _make_agent(tmp_path)
    agent = Agent.from_markdown(
        agent_path, default_provider="openai", default_model=("gpt-4o-mini",)
    )
    driver = agent._select_turn_driver(planning_override=False)
    assert isinstance(driver, StandardTurnDriver)


def test_select_turn_driver_returns_standard_for_none(tmp_path: Path):
    agent_path = _make_agent(tmp_path)
    agent = Agent.from_markdown(
        agent_path, default_provider="openai", default_model=("gpt-4o-mini",)
    )
    driver = agent._select_turn_driver(planning_override=None)
    assert isinstance(driver, StandardTurnDriver)


def test_select_turn_driver_returns_standard_for_true_until_planning_implemented(tmp_path: Path):
    """Until PlanningTurnDriver is implemented, True still returns StandardTurnDriver."""
    agent_path = _make_agent(tmp_path)
    agent = Agent.from_markdown(
        agent_path, default_provider="openai", default_model=("gpt-4o-mini",)
    )
    driver = agent._select_turn_driver(planning_override=True)
    assert isinstance(driver, StandardTurnDriver)


# ---------------------------------------------------------------------------
# post-agent-hooks continue_run branch preserved
# ---------------------------------------------------------------------------

def test_post_agent_hook_continue_run_is_respected(tmp_path: Path):
    """If a post-agent hook returns continue_run=True, the loop resumes.

    Simulated by an AgentBehavior that intercepts the first result and injects
    a continue signal, letting the second model response become the final result.
    """
    from agent_framework.agent import AgentBehavior, AgentEndHookDecision

    class _OneRetryBehavior(AgentBehavior):
        def __init__(self):
            self._retried = False

        def after_run(self, agent, host, *, run, caller_id, result):
            if not self._retried:
                self._retried = True
                return AgentEndHookDecision(
                    final_result=result,
                    continue_run=True,
                )
            return None

    _make_agent(tmp_path)
    host = _make_host(
        tmp_path,
        [
            {"kind": "final_message", "message": "first"},
            {"kind": "final_message", "message": "second"},
        ],
    )
    behavior = _OneRetryBehavior()
    from dataclasses import replace as _replace
    agent = host.get_agent("test_agent")
    agent = _replace(agent, behaviors=(behavior,))

    result = agent.run(host=host, parameters={"instruction": "test"}, caller_id="test")
    assert result.message == "second"
