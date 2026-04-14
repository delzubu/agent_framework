from __future__ import annotations

import json
import logging
from pathlib import Path

from agent_framework.llm_trace_logging import build_llm_trace_event
from agent_framework.model import ProviderRequestTrace
from agent_framework.tracing import (
    CompositeRuntimeTracer,
    NullRuntimeTracer,
    TraceContext,
    TraceEvent,
)
from agent_framework.tracing_consumers.log_handler import LoggingTraceHandler
from agent_framework.tracing_subscribers.jsonl_subscriber import JsonlTraceSubscriber
from agent_framework.tracing_subscribers.llm_trace_file_subscriber import LlmTraceFileSubscriber


class _Recorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def consume(self, event: TraceEvent) -> None:
        self.events.append(event)


def test_composite_runtime_tracer_fanout() -> None:
    left = _Recorder()
    right = _Recorder()
    tracer = CompositeRuntimeTracer(subscribers=[left, right])
    tracer.publish(
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
        )
    )
    assert len(left.events) == 1
    assert len(right.events) == 1


def test_runtime_tracer_child_inherits_context() -> None:
    rec = _Recorder()
    tracer = CompositeRuntimeTracer(
        subscribers=[rec],
        base_context=TraceContext(session_id="sess-1", run_id="run-1"),
    )
    child = tracer.child(agent_id="root")
    child.publish(
        TraceEvent(
            event_id="e2",
            parent_event_id=None,
            span_id="s2",
            parent_span_id="s1",
            timestamp="2026-04-13T00:00:00+00:00",
            channel="runtime",
            level="info",
            kind="runtime.agent_started",
            title="Agent started",
        )
    )
    assert rec.events[0].context.session_id == "sess-1"
    assert rec.events[0].context.run_id == "run-1"
    assert rec.events[0].context.agent_id == "root"


def test_null_runtime_tracer_is_safe_noop() -> None:
    tracer = NullRuntimeTracer()
    tracer.publish(
        TraceEvent(
            event_id="e3",
            parent_event_id=None,
            span_id=None,
            parent_span_id=None,
            timestamp="2026-04-13T00:00:00+00:00",
            channel="log",
            level="debug",
            kind="log.record",
            title="Debug line",
        )
    )


def test_logging_trace_handler_emits_log_channel_event() -> None:
    recorder = _Recorder()
    tracer = CompositeRuntimeTracer(subscribers=[recorder])
    logger = logging.getLogger("tests.runtime_tracing.handler")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(LoggingTraceHandler(tracer))

    logger.warning("hello from logger")

    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event.channel == "log"
    assert event.level == "warning"
    assert event.kind == "log.record"
    assert event.payload["message"] == "hello from logger"


def test_jsonl_trace_subscriber_writes_one_json_per_event(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    tracer = CompositeRuntimeTracer(subscribers=[JsonlTraceSubscriber(path)])
    tracer.publish(
        TraceEvent(
            event_id="e-jsonl",
            parent_event_id=None,
            span_id=None,
            parent_span_id=None,
            timestamp="2026-04-13T00:00:00+00:00",
            channel="runtime",
            level="info",
            kind="runtime.session_started",
            title="Session started",
        )
    )
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["event_id"] == "e-jsonl"


def test_llm_trace_file_subscriber_filters_to_llm_channel(tmp_path: Path) -> None:
    out_dir = tmp_path / "logs"
    llm = LlmTraceFileSubscriber(out_dir)
    tracer = CompositeRuntimeTracer(subscribers=[llm])
    tracer.publish(
        TraceEvent(
            event_id="e-sys",
            parent_event_id=None,
            span_id=None,
            parent_span_id=None,
            timestamp="2026-04-13T00:00:00+00:00",
            channel="log",
            level="info",
            kind="log.record",
            title="Skip me",
        )
    )
    tracer.publish(
        TraceEvent(
            event_id="e-llm",
            parent_event_id=None,
            span_id=None,
            parent_span_id=None,
            timestamp="2026-04-13T00:00:00+00:00",
            channel="llm",
            level="info",
            kind="llm.request",
            title="Request",
            payload={"agent_id": "root"},
        )
    )
    log_file = out_dir / "root.log"
    assert log_file.exists()
    assert "llm.request" in log_file.read_text(encoding="utf-8")


class _RuntimeChannelOnly:
    trace_channels = frozenset({"runtime"})

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def consume(self, event: TraceEvent) -> None:
        self.events.append(event)


def test_composite_skips_subscribers_that_exclude_event_channel() -> None:
    filtered = _RuntimeChannelOnly()
    all_rec = _Recorder()
    tracer = CompositeRuntimeTracer(subscribers=[filtered, all_rec])
    tracer.publish(
        TraceEvent(
            event_id="e-log",
            parent_event_id=None,
            span_id=None,
            parent_span_id=None,
            timestamp="2026-04-13T00:00:00+00:00",
            channel="log",
            level="info",
            kind="log.record",
            title="log line",
        )
    )
    tracer.publish(
        TraceEvent(
            event_id="e-run",
            parent_event_id=None,
            span_id="s",
            parent_span_id=None,
            timestamp="2026-04-13T00:00:00+00:00",
            channel="runtime",
            level="info",
            kind="runtime.agent_started",
            title="Agent started",
        )
    )
    assert len(filtered.events) == 1
    assert filtered.events[0].channel == "runtime"
    assert len(all_rec.events) == 2


def test_build_llm_trace_event_maps_provider_request() -> None:
    event = build_llm_trace_event(
        ProviderRequestTrace(
            run_id="r1",
            agent_id="root",
            provider_name="openai",
            model_name="gpt-4o-mini",
            temperature=0.2,
            input_payload={"messages": []},
        ),
        kind="llm.request",
    )
    assert event.channel == "llm"
    assert event.kind == "llm.request"
    assert event.payload["agent_id"] == "root"
    assert event.parent_span_id == "r1"
    assert event.span_id is not None
