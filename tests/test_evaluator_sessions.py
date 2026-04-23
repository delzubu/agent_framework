from __future__ import annotations

from pathlib import Path

import pytest

from agent_framework.agents.agent_decision import AgentDecision
from agent_framework.agent import AgentResult
from agent_framework.tracing import TraceContext, TraceEvent
from agent_framework_evaluator.runtime.debug_subscriber import DebuggerSubscriber
from agent_framework_evaluator.runtime.session_runner import SessionRunner
from agent_framework_evaluator.runtime.setup_loader import load_setup_module
from agent_framework_evaluator.usage import EvaluatorUsageTracker


def test_setup_loader_loads_prompt_template(tmp_path: Path) -> None:
    path = tmp_path / "setup.py"
    path.write_text('PROMPT_TEMPLATE = "Hello {name}"\n', encoding="utf-8")
    module = load_setup_module(path)
    assert module.PROMPT_TEMPLATE == "Hello {name}"


def test_debugger_subscriber_buffers_events_by_session() -> None:
    subscriber = DebuggerSubscriber()
    subscriber.consume(
        TraceEvent(
            event_id="e1",
            parent_event_id=None,
            span_id="s1",
            parent_span_id=None,
            timestamp="2026-04-13T00:00:00+00:00",
            channel="runtime",
            level="info",
            kind="runtime.session_started",
            title="Session started",
            context=TraceContext(session_id="sess-1"),
        )
    )
    events = subscriber.drain("sess-1")
    assert len(events) == 1
    assert events[0].context.session_id == "sess-1"


class _FakeHost:
    trace_context_overlay = None

    def run_agent(self, agent_id: str, initial_instruction: str):
        return AgentResult(status="completed", message=f"{agent_id}:{initial_instruction}")

    def session_usage_totals(self) -> dict[str, int]:
        return {
            "input_tokens": 0,
            "input_cached_tokens": 0,
            "output_tokens": 0,
            "output_cached_tokens": 0,
            "total_tokens": 0,
        }


def test_session_runner_returns_final_message(tmp_path: Path) -> None:
    setup_path = tmp_path / "setup.py"
    setup_path.write_text("", encoding="utf-8")
    runner = SessionRunner(env_path=tmp_path / ".env", host_factory=lambda **_: _FakeHost())
    result = runner.run_once(agent_id="root", prompt="hello", setup_path=setup_path)
    assert result["status"] == "completed"
    assert result["message"] == "root:hello"


class _FakeStructuredHost:
    trace_context_overlay = None

    def run_agent(self, agent_id: str, initial_instruction: str):
        del agent_id, initial_instruction
        decision = AgentDecision(
            kind="final_message",
            message="human text",
            parameters={"status": "ready", "declared_intents": [{"actor_id": "actor.player"}]},
        )
        return AgentResult(status="completed", message=decision.message, decision=decision)


def test_session_runner_returns_decision_parameters(tmp_path: Path) -> None:
    setup_path = tmp_path / "setup.py"
    setup_path.write_text("", encoding="utf-8")
    runner = SessionRunner(env_path=tmp_path / ".env", host_factory=lambda **_: _FakeStructuredHost())
    result = runner.run_once(agent_id="root", prompt="hello", setup_path=setup_path)
    assert result["status"] == "completed"
    assert result["message"] == "human text"
    assert result["kind"] == "final_message"
    assert result["parameters"] == {
        "status": "ready",
        "declared_intents": [{"actor_id": "actor.player"}],
    }


def test_session_runner_suite_teardown_if_any(tmp_path: Path) -> None:
    log = tmp_path / "suite.log"
    setup_path = tmp_path / "setup.py"
    setup_path.write_text(
        f"def suite_teardown(ctx):\n    open({repr(str(log))}, 'a', encoding='utf-8').write('x')\n",
        encoding="utf-8",
    )
    runner = SessionRunner(env_path=tmp_path / ".env", host_factory=lambda **_: _FakeHost())
    runner.run_once(agent_id="root", prompt="p", setup_path=setup_path)
    assert not log.exists()
    runner.suite_teardown_if_any()
    assert log.read_text(encoding="utf-8") == "x"
    runner.suite_teardown_if_any()
    assert log.read_text(encoding="utf-8") == "x"


def test_session_runner_can_resume_after_user_input(tmp_path: Path) -> None:
    setup_path = tmp_path / "setup.py"
    setup_path.write_text("", encoding="utf-8")
    runner = SessionRunner(env_path=tmp_path / ".env", host_factory=lambda **_: _FakeHost())
    result = runner.run_once(agent_id="needs_input", prompt="clarified", setup_path=setup_path)
    assert result["status"] == "completed"
    assert result["message"] == "needs_input:clarified"


def test_evaluator_usage_tracker_prefers_runtime_summaries() -> None:
    tracker = EvaluatorUsageTracker()
    tracker.consume_trace_event({
        "kind": "runtime.audit.agent_call_started",
        "payload": {"run_id": "parent", "parent_run_id": None, "agent_name": "root"},
        "context": {"run_id": "parent", "agent_id": "root"},
    })
    tracker.consume_trace_event({
        "kind": "runtime.audit.agent_call_started",
        "payload": {"run_id": "child", "parent_run_id": "parent", "agent_name": "worker"},
        "context": {"run_id": "child", "agent_id": "worker"},
    })
    tracker.consume_trace_event({
        "kind": "llm.response",
        "payload": {
            "run_id": "parent",
            "agent_id": "root",
            "usage": {"input_tokens": 10, "input_cached_tokens": 2, "output_tokens": 5, "output_cached_tokens": 0, "total_tokens": 15},
            "raw_usage": {"prompt_tokens": 10},
        },
        "context": {"run_id": "parent", "agent_id": "root"},
    })
    tracker.consume_trace_event({
        "kind": "llm.response",
        "payload": {
            "run_id": "child",
            "agent_id": "worker",
            "usage": {"input_tokens": 7, "input_cached_tokens": 1, "output_tokens": 3, "output_cached_tokens": 0, "total_tokens": 10},
            "raw_usage": {"prompt_tokens": 7},
        },
        "context": {"run_id": "child", "agent_id": "worker"},
    })
    tracker.consume_trace_event({
        "kind": "runtime.agent_finished",
        "payload": {
            "usage_self": {"input_tokens": 10, "input_cached_tokens": 2, "output_tokens": 5, "output_cached_tokens": 0, "total_tokens": 15},
            "usage_inclusive": {"input_tokens": 17, "input_cached_tokens": 3, "output_tokens": 8, "output_cached_tokens": 0, "total_tokens": 25},
        },
        "context": {"run_id": "parent", "agent_id": "root"},
    })
    tracker.consume_trace_event({
        "kind": "runtime.agent_finished",
        "payload": {
            "usage_self": {"input_tokens": 7, "input_cached_tokens": 1, "output_tokens": 3, "output_cached_tokens": 0, "total_tokens": 10},
            "usage_inclusive": {"input_tokens": 7, "input_cached_tokens": 1, "output_tokens": 3, "output_cached_tokens": 0, "total_tokens": 10},
        },
        "context": {"run_id": "child", "agent_id": "worker"},
    })
    tracker.consume_trace_event({
        "kind": "runtime.session_finished",
        "payload": {
            "usage_session_totals": {"input_tokens": 17, "input_cached_tokens": 3, "output_tokens": 8, "output_cached_tokens": 0, "total_tokens": 25},
        },
        "context": {},
    })

    snapshot = tracker.snapshot()
    assert snapshot["session_totals"]["total_tokens"] == 25
    assert snapshot["runs"]["parent"]["self_totals"]["total_tokens"] == 15
    assert snapshot["runs"]["parent"]["inclusive_totals"]["total_tokens"] == 25
    assert snapshot["runs"]["child"]["inclusive_totals"]["total_tokens"] == 10
    assert snapshot["runs"]["parent"]["llm_calls"][0]["usage"]["total_tokens"] == 15


def test_session_runner_populates_usage_summary_without_external_runtime_tracer(tmp_path: Path) -> None:
    setup_path = tmp_path / "setup.py"
    setup_path.write_text("", encoding="utf-8")
    runner = SessionRunner(env_path=tmp_path / ".env", host_factory=lambda **_: _FakeHost())
    runner.run_once(agent_id="root", prompt="hello", setup_path=setup_path)
    assert runner._last_usage_summary is not None
    assert "session_totals" in runner._last_usage_summary


class _FailingHost(_FakeHost):
    def run_agent(self, agent_id: str, initial_instruction: str):
        raise RuntimeError(f"{agent_id}:{initial_instruction}:boom")

    def session_usage_totals(self) -> dict[str, int]:
        return {
            "input_tokens": 11,
            "input_cached_tokens": 2,
            "output_tokens": 7,
            "output_cached_tokens": 0,
            "total_tokens": 18,
        }


def test_session_runner_preserves_usage_summary_on_failure(tmp_path: Path) -> None:
    setup_path = tmp_path / "setup.py"
    setup_path.write_text("", encoding="utf-8")
    runner = SessionRunner(env_path=tmp_path / ".env", host_factory=lambda **_: _FailingHost())

    with pytest.raises(RuntimeError, match="root:hello:boom"):
        runner.run_once(agent_id="root", prompt="hello", setup_path=setup_path)

    assert runner._last_usage_summary is not None
    assert runner._last_usage_summary["session_totals"]["total_tokens"] == 18
