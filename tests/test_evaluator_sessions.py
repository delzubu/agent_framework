from __future__ import annotations

from pathlib import Path

from agent_framework.agents.agent_decision import AgentDecision
from agent_framework.agent import AgentResult
from agent_framework.tracing import TraceContext, TraceEvent
from agent_framework_evaluator.runtime.debug_subscriber import DebuggerSubscriber
from agent_framework_evaluator.runtime.session_runner import SessionRunner
from agent_framework_evaluator.runtime.setup_loader import load_setup_module


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
