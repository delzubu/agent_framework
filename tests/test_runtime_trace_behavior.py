"""Tests for RuntimeTraceBehavior and host.publish_trace_event wiring."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agent_framework.config import HostConfig
from agent_framework.host import AgentHost
from agent_framework.model import ModelResponse
from agent_framework.tracing import CompositeRuntimeTracer, TraceContext, TraceEvent, utc_now_iso
from tests.test_headless import FakeDriver


class _Recorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def consume(self, event: TraceEvent) -> None:
        self.events.append(event)


@pytest.fixture
def tiny_root_agent_tree(tmp_path: Path) -> Path:
    agents_dir = tmp_path / "agents"
    tools_dir = tmp_path / "tools"
    world_dir = tmp_path / "world"
    agents_dir.mkdir()
    tools_dir.mkdir()
    world_dir.mkdir()
    (agents_dir / "root.md").write_text(
        """---
id: root
role: narrator
parameters:
  instruction:
    description: First instruction.
    required: true
---
You are the root narrator.
---
<agent_input><instruction>{{instruction}}</instruction></agent_input>
""",
        encoding="utf-8",
    )
    return tmp_path


def test_run_agent_emits_runtime_lifecycle_and_decision_events(tiny_root_agent_tree: Path) -> None:
    cfg = HostConfig(
        agent_directory=(tiny_root_agent_tree / "agents").resolve(),
        tools_directory=(tiny_root_agent_tree / "tools").resolve(),
        world_directory=(tiny_root_agent_tree / "world").resolve(),
        root_agent_id="root",
    )
    response = ModelResponse(
        payload={"kind": "final_message", "message": "done"},
        raw_text="{}",
    )
    recorder = _Recorder()
    tracer = CompositeRuntimeTracer(subscribers=[recorder])
    host = AgentHost.create(model_driver=FakeDriver(response), config=cfg, mcp_enabled=False)
    host.runtime_tracer = tracer
    host.tool_registry.discover()
    host.agent_registry.discover()
    host.trace_context_overlay = TraceContext(session_id="sess-test")
    try:
        result = host.run_agent("root", initial_instruction="hello")
    finally:
        host.trace_context_overlay = None
    assert result.message == "done"
    kinds = [e.kind for e in recorder.events]
    assert "runtime.agent_started" in kinds
    assert "runtime.decision_made" in kinds
    assert "runtime.agent_finished" in kinds
    assert any(e.context.session_id == "sess-test" for e in recorder.events)


def test_composite_runtime_tracer_publish_survives_concurrent_publishers() -> None:
    recorder = _Recorder()
    tracer = CompositeRuntimeTracer(subscribers=[recorder])

    def make_event(n: int) -> TraceEvent:
        return TraceEvent(
            event_id=f"e{n}",
            parent_event_id=None,
            span_id=None,
            parent_span_id=None,
            timestamp=utc_now_iso(),
            channel="runtime",
            level="info",
            kind="runtime.session_started",
            title="x",
        )

    def worker(start: int) -> None:
        for i in range(40):
            tracer.publish(make_event(start * 100 + i))

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(recorder.events) == 160
