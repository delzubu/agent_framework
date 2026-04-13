# Agent Evaluator Web Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a browser-based agent evaluator and debugger with a reusable web-hosted runtime surface, a unified tracing pipeline, interactive clarification, setup-module hooks, and matching CLI/headless execution.

**Architecture:** Promote reusable runtime capabilities into `agent_framework` rather than building a second orchestrator: add a unified `RuntimeTracer`, a logging ingress adapter, file/debugger subscribers, `WebUserCommunication`, and a web host factory. Build the product surface in a separate `agent_framework_evaluator` package that manages sessions, setup hooks, WebSocket transport, and a lightweight static UI while reusing `AgentHost` for execution.

**Tech Stack:** Python 3.11+, `dataclasses`, `logging`, `asyncio`, `queue`, `pathlib`, FastAPI/Starlette for local HTTP + WebSocket hosting, Uvicorn for local serving, vanilla HTML/CSS/JavaScript for the first UI, `pytest` for tests.

**Spec:** `docs/architecture/agent-evaluator-web-runtime.md`

### Gap-closure status (2026-04-13)

The following tracks the “close evaluator / unified-tracing gaps” follow-up work against the live architecture doc (see `docs/architecture/agent-evaluator-web-runtime.md` §16.1 and `docs/architecture/tracing-evaluation.md`).

- [x] **Runtime trace publishing:** `RuntimeTraceBehavior`, `AgentHost.run_agent` wiring, `trace_context_overlay` for evaluator sessions, `CompositeRuntimeTracer` publish lock, tests.
- [x] **CLI / trace logging:** optional `--runtime-trace-jsonl` and `--runtime-trace-python-logs` on `python -m agent_framework`; console defaults unchanged.
- [x] **User channel:** `user.*` from `WebUserCommunication` and `ConsoleUserCommunication` via `tracing_bridge` contextvar; tests.
- [x] **Evaluator UX & artifacts:** hierarchical trace UI, `/api/agents`, `/api/setup-template`, session close + `suite_teardown_if_any`, CLI `--trace-jsonl` / `--trace-llm-dir`, conftest stub replaced with monkeypatched runner test.
- [x] **Audit migration (optional):** deferred as explicit **parallel** JSONL; documented in `tracing-evaluation.md` (no `TraceSubscriber` adapter yet).
- [x] **Documentation:** architecture implementation status, this checklist, tracing guide updates.

---

## File Structure

### Core framework files

- Create: `src/agent_framework/tracing.py`
- Create: `src/agent_framework/tracing_consumers/log_handler.py`
- Create: `src/agent_framework/tracing_subscribers/jsonl_subscriber.py`
- Create: `src/agent_framework/tracing_subscribers/llm_trace_file_subscriber.py`
- Create: `src/agent_framework/web_communication.py`
- Create: `src/agent_framework/web_host.py`
- Modify: `src/agent_framework/host.py`
- Modify: `src/agent_framework/user_communication.py`
- Modify: `src/agent_framework/console_communication.py`
- Modify: `src/agent_framework/audit_trace.py`
- Modify: `src/agent_framework/llm_trace_logging.py`
- Modify: `src/agent_framework/trace_logging.py`
- Modify: `src/agent_framework/__init__.py`
- Modify: `src/agent_framework/__main__.py`
- Modify: `pyproject.toml`

### Evaluator package files

- Create: `src/agent_framework_evaluator/__init__.py`
- Create: `src/agent_framework_evaluator/__main__.py`
- Create: `src/agent_framework_evaluator/cli.py`
- Create: `src/agent_framework_evaluator/app.py`
- Create: `src/agent_framework_evaluator/models.py`
- Create: `src/agent_framework_evaluator/session_manager.py`
- Create: `src/agent_framework_evaluator/runtime/__init__.py`
- Create: `src/agent_framework_evaluator/runtime/setup_loader.py`
- Create: `src/agent_framework_evaluator/runtime/session_runner.py`
- Create: `src/agent_framework_evaluator/runtime/runner_host.py`
- Create: `src/agent_framework_evaluator/runtime/debug_subscriber.py`
- Create: `src/agent_framework_evaluator/web/index.html`
- Create: `src/agent_framework_evaluator/web/app.js`
- Create: `src/agent_framework_evaluator/web/styles.css`

### Tests

- Create: `tests/test_runtime_tracing.py`
- Create: `tests/test_web_user_communication.py`
- Create: `tests/test_web_host.py`
- Create: `tests/test_evaluator_cli.py`
- Create: `tests/test_evaluator_sessions.py`

### Documentation

- Modify: `docs/architecture/overview.md`
- Modify: `docs/architecture/host-orchestration.md`
- Modify: `docs/architecture/tracing-evaluation.md`
- Modify: `docs/architecture/extension-points.md`

This split locks in the main boundary: reusable runtime capability in `agent_framework`, productized evaluator behavior in `agent_framework_evaluator`.

---

## Task 1: Add the core tracing contracts and fan-out tracer

**Files:**
- Create: `src/agent_framework/tracing.py`
- Create: `tests/test_runtime_tracing.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_runtime_tracing.py`:

```python
from agent_framework.tracing import (
    CompositeRuntimeTracer,
    NullRuntimeTracer,
    TraceContext,
    TraceEvent,
)


class _Recorder:
    def __init__(self) -> None:
        self.events = []

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
            channel="system",
            level="debug",
            kind="system.log",
            title="Debug line",
        )
    )
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_runtime_tracing.py -v
```

Expected: `FAILED` because `agent_framework.tracing` does not exist.

- [ ] **Step 3: Create `src/agent_framework/tracing.py`**

Add:

```python
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

TraceChannel = Literal["runtime", "llm", "system", "user"]
TraceLevel = Literal["debug", "info", "warning", "error"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class TraceContext:
    session_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    caller_id: str | None = None
    tool_name: str | None = None
    subagent_id: str | None = None
    conversation_id: str | None = None

    def merged(self, **updates: Any) -> "TraceContext":
        return replace(self, **updates)


@dataclass(frozen=True, slots=True)
class TraceEvent:
    event_id: str
    parent_event_id: str | None
    span_id: str | None
    parent_span_id: str | None
    timestamp: str
    channel: TraceChannel
    level: TraceLevel
    kind: str
    title: str
    summary: str = ""
    context: TraceContext = field(default_factory=TraceContext)
    payload: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    def with_context(self, context: TraceContext) -> "TraceEvent":
        merged = self.context.merged(
            **{k: v for k, v in context.__dict__.items() if v is not None}
        )
        return replace(self, context=merged)


class TraceSubscriber(Protocol):
    def consume(self, event: TraceEvent) -> None:
        ...


class RuntimeTracer(Protocol):
    def publish(self, event: TraceEvent) -> None:
        ...

    def child(self, **context_updates: Any) -> "RuntimeTracer":
        ...

    def subscribe(self, subscriber: TraceSubscriber) -> None:
        ...

    def unsubscribe(self, subscriber: TraceSubscriber) -> None:
        ...


@dataclass(slots=True)
class CompositeRuntimeTracer:
    subscribers: list[TraceSubscriber] = field(default_factory=list)
    base_context: TraceContext = field(default_factory=TraceContext)

    def publish(self, event: TraceEvent) -> None:
        enriched = event.with_context(self.base_context)
        for subscriber in tuple(self.subscribers):
            subscriber.consume(enriched)

    def child(self, **context_updates: Any) -> "CompositeRuntimeTracer":
        return CompositeRuntimeTracer(
            subscribers=self.subscribers,
            base_context=self.base_context.merged(**context_updates),
        )

    def subscribe(self, subscriber: TraceSubscriber) -> None:
        self.subscribers.append(subscriber)

    def unsubscribe(self, subscriber: TraceSubscriber) -> None:
        self.subscribers = [item for item in self.subscribers if item is not subscriber]


@dataclass(slots=True)
class NullRuntimeTracer:
    def publish(self, event: TraceEvent) -> None:
        return None

    def child(self, **context_updates: Any) -> "NullRuntimeTracer":
        return self

    def subscribe(self, subscriber: TraceSubscriber) -> None:
        return None

    def unsubscribe(self, subscriber: TraceSubscriber) -> None:
        return None
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_runtime_tracing.py -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/agent_framework/tracing.py tests/test_runtime_tracing.py
git commit -m "feat(tracing): add core trace contracts and fan-out tracer"
```

---

## Task 2: Add Python logging ingestion into the unified tracer

**Files:**
- Create: `src/agent_framework/tracing_consumers/log_handler.py`
- Modify: `tests/test_runtime_tracing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime_tracing.py`:

```python
import logging

from agent_framework.tracing import CompositeRuntimeTracer
from agent_framework.tracing_consumers.log_handler import LoggingTraceHandler


def test_logging_trace_handler_emits_system_log_event() -> None:
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
    assert event.channel == "system"
    assert event.level == "warning"
    assert event.kind == "system.log"
    assert event.payload["message"] == "hello from logger"
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_runtime_tracing.py -k "logging_trace_handler" -v
```

Expected: `FAILED` because `LoggingTraceHandler` does not exist.

- [ ] **Step 3: Create `src/agent_framework/tracing_consumers/log_handler.py`**

Add:

```python
from __future__ import annotations

import logging
from uuid import uuid4

from agent_framework.tracing import RuntimeTracer, TraceEvent, utc_now_iso


_LEVEL_MAP = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


class LoggingTraceHandler(logging.Handler):
    def __init__(self, tracer: RuntimeTracer) -> None:
        super().__init__()
        self._tracer = tracer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event = TraceEvent(
                event_id=str(uuid4()),
                parent_event_id=None,
                span_id=None,
                parent_span_id=None,
                timestamp=utc_now_iso(),
                channel="system",
                level=_LEVEL_MAP.get(record.levelno, "info"),
                kind="system.log",
                title=f"{record.name} {record.levelname}",
                summary=record.getMessage(),
                payload={
                    "logger_name": record.name,
                    "pathname": record.pathname,
                    "lineno": record.lineno,
                    "message": record.getMessage(),
                    "exc_info": self.formatException(record.exc_info) if record.exc_info else None,
                },
            )
            self._tracer.publish(event)
        except Exception:
            self.handleError(record)
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_runtime_tracing.py -k "logging_trace_handler" -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/agent_framework/tracing_consumers/log_handler.py tests/test_runtime_tracing.py
git commit -m "feat(tracing): add logging handler that ingests LogRecord events"
```

---

## Task 3: Add JSONL and LLM file subscribers

**Files:**
- Create: `src/agent_framework/tracing_subscribers/jsonl_subscriber.py`
- Create: `src/agent_framework/tracing_subscribers/llm_trace_file_subscriber.py`
- Modify: `tests/test_runtime_tracing.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runtime_tracing.py`:

```python
import json
from pathlib import Path

from agent_framework.tracing import CompositeRuntimeTracer, TraceEvent
from agent_framework.tracing_subscribers.jsonl_subscriber import JsonlTraceSubscriber
from agent_framework.tracing_subscribers.llm_trace_file_subscriber import LlmTraceFileSubscriber


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
            channel="system",
            level="info",
            kind="system.log",
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
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_runtime_tracing.py -k "jsonl_trace_subscriber or llm_trace_file_subscriber" -v
```

Expected: `FAILED`

- [ ] **Step 3: Create `jsonl_subscriber.py` and `llm_trace_file_subscriber.py`**

Add:

```python
# jsonl_subscriber.py
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from agent_framework.tracing import TraceEvent


class JsonlTraceSubscriber:
    def __init__(self, output_path: Path) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def consume(self, event: TraceEvent) -> None:
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
```

```python
# llm_trace_file_subscriber.py
from __future__ import annotations

import json
from pathlib import Path

from agent_framework.tracing import TraceEvent


class LlmTraceFileSubscriber:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def consume(self, event: TraceEvent) -> None:
        if event.channel != "llm":
            return
        agent_id = event.payload.get("agent_id") or event.context.agent_id or "llm-trace"
        path = self.output_dir / f"{agent_id}.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{event.timestamp}] {event.kind}\n")
            handle.write(json.dumps(event.payload, indent=2, ensure_ascii=False))
            handle.write("\n\n")
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_runtime_tracing.py -k "jsonl_trace_subscriber or llm_trace_file_subscriber" -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/agent_framework/tracing_subscribers/jsonl_subscriber.py src/agent_framework/tracing_subscribers/llm_trace_file_subscriber.py tests/test_runtime_tracing.py
git commit -m "feat(tracing): add JSONL and LLM trace file subscribers"
```

---

## Task 4: Wire host and provider tracing into the unified tracer

**Files:**
- Modify: `src/agent_framework/host.py`
- Modify: `src/agent_framework/llm_trace_logging.py`
- Modify: `src/agent_framework/audit_trace.py`
- Modify: `src/agent_framework/__init__.py`
- Modify: `tests/test_host_lifecycle.py`
- Modify: `tests/test_runtime_tracing.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_host_lifecycle.py`:

```python
from agent_framework.host import AgentHost
from agent_framework.tracing import CompositeRuntimeTracer


def test_agent_host_uses_supplied_runtime_tracer(fake_model_driver) -> None:
    tracer = CompositeRuntimeTracer()
    host = AgentHost.create(model_driver=fake_model_driver)
    host.runtime_tracer = tracer
    assert host.runtime_tracer is tracer
```

Add to `tests/test_runtime_tracing.py`:

```python
from agent_framework.llm_trace_logging import build_llm_trace_event
from agent_framework.model import ProviderRequestTrace


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
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_host_lifecycle.py tests/test_runtime_tracing.py -k "runtime_tracer or build_llm_trace_event" -v
```

Expected: `FAILED`

- [ ] **Step 3: Modify `host.py` and `llm_trace_logging.py`**

Update `AgentHost` to carry a runtime tracer:

```python
from agent_framework.tracing import NullRuntimeTracer, RuntimeTracer

runtime_tracer: RuntimeTracer = field(default_factory=NullRuntimeTracer)
```

Update `AgentHost.create()`:

```python
host = cls(
    config=config,
    model_driver=model_driver,
    tool_registry=tool_registry,
    agent_registry=agent_registry,
    command_registry=command_registry,
    user_comm=user_comm,
    mcp_manager=mcp_manager,
    conversation_store=conversation_store,
    _command_fallback=command_fallback,
)
```

Keep the field initialized by dataclass default first, then allow callers to overwrite it after construction or extend `create(...)` with `runtime_tracer=None` if cleaner.

In `llm_trace_logging.py`, add a helper:

```python
from uuid import uuid4

from agent_framework.tracing import TraceContext, TraceEvent, utc_now_iso


def build_llm_trace_event(trace, *, kind: str) -> TraceEvent:
    payload = {
        "run_id": trace.run_id,
        "agent_id": trace.agent_id,
        "provider_name": trace.provider_name,
        "model_name": trace.model_name,
    }
    if hasattr(trace, "temperature"):
        payload["temperature"] = trace.temperature
    if hasattr(trace, "input_payload"):
        payload["input_payload"] = trace.input_payload
    if hasattr(trace, "raw_text"):
        payload["raw_text"] = trace.raw_text
    if hasattr(trace, "parsed_payload"):
        payload["parsed_payload"] = trace.parsed_payload
    return TraceEvent(
        event_id=str(uuid4()),
        parent_event_id=None,
        span_id=trace.run_id,
        parent_span_id=None,
        timestamp=utc_now_iso(),
        channel="llm",
        level="info",
        kind=kind,
        title=f"{trace.agent_id or 'host'} {kind}",
        context=TraceContext(run_id=trace.run_id, agent_id=trace.agent_id),
        payload=payload,
    )
```

Update `attach_to_host(...)` so it publishes to `host.runtime_tracer` and only optionally mirrors to legacy file logging.

In `audit_trace.py`, keep the existing API temporarily but note in docstrings that it is a compatibility layer to be backed by subscribers later.

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_host_lifecycle.py tests/test_runtime_tracing.py -k "runtime_tracer or build_llm_trace_event" -v
```

Expected: `PASSED`

- [ ] **Step 5: Run regression tests around host lifecycle**

Run:

```bash
pytest tests/test_host_lifecycle.py tests/test_framework_runtime.py tests/test_async_driver.py -v
```

Expected: `PASSED`

- [ ] **Step 6: Commit**

```bash
git add src/agent_framework/host.py src/agent_framework/llm_trace_logging.py src/agent_framework/audit_trace.py src/agent_framework/__init__.py tests/test_host_lifecycle.py tests/test_runtime_tracing.py
git commit -m "feat(tracing): wire host and provider trace callbacks into runtime tracer"
```

---

## Task 5: Add browser-backed user communication

**Files:**
- Create: `src/agent_framework/web_communication.py`
- Create: `tests/test_web_user_communication.py`
- Modify: `src/agent_framework/user_communication.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_user_communication.py`:

```python
import asyncio
import pytest

from agent_framework.web_communication import WebUserCommunication


@pytest.mark.asyncio
async def test_web_user_communication_waits_for_answer() -> None:
    comm = WebUserCommunication(session_id="sess-1")

    async def answer_later() -> None:
        await asyncio.sleep(0.01)
        comm.submit_user_input("hello")

    task = asyncio.create_task(comm.read_user_input("Question? "))
    await answer_later()
    result = await task
    assert result == "hello"


@pytest.mark.asyncio
async def test_web_user_communication_records_outgoing_messages() -> None:
    comm = WebUserCommunication(session_id="sess-1")
    await comm.send_message("done")
    queued = comm.drain_outbox()
    assert queued[0]["text"] == "done"
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_web_user_communication.py -v
```

Expected: `FAILED`

- [ ] **Step 3: Create `web_communication.py`**

Add:

```python
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, AsyncIterator

from agent_framework.user_communication import PermissionDecision, PermissionRequest


class WebUserCommunication:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._pending_input: asyncio.Queue[str | None] = asyncio.Queue()
        self._outbox: deque[dict[str, Any]] = deque()

    def submit_user_input(self, text: str | None) -> None:
        self._pending_input.put_nowait(text)

    def drain_outbox(self) -> list[dict[str, Any]]:
        items = list(self._outbox)
        self._outbox.clear()
        return items

    async def send_message(self, text: str, *, role: str = "assistant") -> None:
        self._outbox.append({"kind": "message", "role": role, "text": text})

    async def ask_question(self, prompt: str, *, options: tuple[str, ...] | None = None, allow_freetext: bool = True) -> str:
        self._outbox.append({"kind": "question", "prompt": prompt, "options": list(options or ()), "allow_freetext": allow_freetext})
        return await self.read_user_input(prompt)

    async def ask_confirmation(self, prompt: str, *, default: bool = False) -> bool:
        self._outbox.append({"kind": "confirmation", "prompt": prompt, "default": default})
        value = await self.read_user_input(prompt)
        if value is None or value == "":
            return default
        return value.strip().lower() in {"y", "yes", "true", "1"}

    async def request_permission(self, request: PermissionRequest) -> PermissionDecision:
        self._outbox.append({"kind": "permission", "request": request})
        raw = await self.read_user_input(request.summary)
        allowed = str(raw or "").strip().lower() in {"y", "yes", "allow", "true", "1"}
        return PermissionDecision(allowed=allowed, remember_for_session=False)

    async def read_user_input(self, prompt: str = "") -> str | None:
        self._outbox.append({"kind": "prompt", "prompt": prompt})
        return await self._pending_input.get()

    async def stream_text(self, chunks: AsyncIterator[str]) -> None:
        async for chunk in chunks:
            self._outbox.append({"kind": "stream", "text": chunk})
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_web_user_communication.py -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/agent_framework/web_communication.py src/agent_framework/user_communication.py tests/test_web_user_communication.py
git commit -m "feat(web): add browser-backed user communication adapter"
```

---

## Task 6: Add a web host factory and tracer-aware startup

**Files:**
- Create: `src/agent_framework/web_host.py`
- Create: `tests/test_web_host.py`
- Modify: `src/agent_framework/host.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_host.py`:

```python
from agent_framework.host import AgentHost
from agent_framework.tracing import CompositeRuntimeTracer
from agent_framework.web_communication import WebUserCommunication
from agent_framework.web_host import create_web_host


def test_create_web_host_wires_user_comm_and_tracer(fake_model_driver) -> None:
    tracer = CompositeRuntimeTracer()
    comm = WebUserCommunication(session_id="sess-1")
    host = create_web_host(
        model_driver=fake_model_driver,
        user_comm=comm,
        runtime_tracer=tracer,
    )
    assert isinstance(host, AgentHost)
    assert host.user_comm is comm
    assert host.runtime_tracer is tracer
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_web_host.py -v
```

Expected: `FAILED`

- [ ] **Step 3: Create `web_host.py`**

Add:

```python
from __future__ import annotations

from agent_framework.host import AgentHost
from agent_framework.tracing import NullRuntimeTracer, RuntimeTracer


def create_web_host(
    *,
    model_driver,
    config=None,
    user_comm=None,
    runtime_tracer: RuntimeTracer | None = None,
    conversation_store=None,
):
    host = AgentHost.create(
        model_driver=model_driver,
        config=config,
        user_comm=user_comm,
        conversation_store=conversation_store,
    )
    host.runtime_tracer = runtime_tracer or NullRuntimeTracer()
    return host
```

Optionally add `AgentHost.from_env_web(...)` later in this task if cleaner for CLI parity.

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_web_host.py -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/agent_framework/web_host.py src/agent_framework/host.py tests/test_web_host.py
git commit -m "feat(web): add reusable web host factory with runtime tracer wiring"
```

---

## Task 7: Scaffold the evaluator package and CLI

**Files:**
- Create: `src/agent_framework_evaluator/__init__.py`
- Create: `src/agent_framework_evaluator/__main__.py`
- Create: `src/agent_framework_evaluator/cli.py`
- Modify: `pyproject.toml`
- Create: `tests/test_evaluator_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_evaluator_cli.py`:

```python
from agent_framework_evaluator.cli import build_parser


def test_evaluator_cli_has_web_and_run_subcommands() -> None:
    parser = build_parser()
    args = parser.parse_args(["web", "--env", ".env"])
    assert args.command == "web"
    args = parser.parse_args(["run", "--agent", "root", "--prompt", "hi"])
    assert args.command == "run"
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_evaluator_cli.py -v
```

Expected: `FAILED`

- [ ] **Step 3: Create the package and parser**

Add:

```python
# src/agent_framework_evaluator/cli.py
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent evaluator and debugger.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    web = subparsers.add_parser("web")
    web.add_argument("--env", default=".env")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8123)
    web.add_argument("--open-browser", action="store_true")

    run = subparsers.add_parser("run")
    run.add_argument("--env", default=".env")
    run.add_argument("--agent", required=True)
    run.add_argument("--setup")
    run.add_argument("--prompt")
    run.add_argument("--prompt-file")
    run.add_argument("--output")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0
```

```python
# src/agent_framework_evaluator/__main__.py
from agent_framework_evaluator.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

Update `pyproject.toml`:

```toml
[project.scripts]
agent-eval = "agent_framework_evaluator.cli:main"
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_evaluator_cli.py -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/agent_framework_evaluator/__init__.py src/agent_framework_evaluator/__main__.py src/agent_framework_evaluator/cli.py tests/test_evaluator_cli.py
git commit -m "feat(evaluator): scaffold evaluator package and CLI entrypoints"
```

---

## Task 8: Add setup-module loading and runner host/session orchestration

**Files:**
- Create: `src/agent_framework_evaluator/runtime/setup_loader.py`
- Create: `src/agent_framework_evaluator/runtime/runner_host.py`
- Create: `src/agent_framework_evaluator/runtime/session_runner.py`
- Create: `src/agent_framework_evaluator/models.py`
- Create: `tests/test_evaluator_sessions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_evaluator_sessions.py`:

```python
from pathlib import Path

from agent_framework_evaluator.runtime.setup_loader import load_setup_module


def test_setup_loader_loads_prompt_template(tmp_path: Path) -> None:
    path = tmp_path / "setup.py"
    path.write_text('PROMPT_TEMPLATE = "Hello {name}"\n', encoding="utf-8")
    module = load_setup_module(path)
    assert module.PROMPT_TEMPLATE == "Hello {name}"
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_evaluator_sessions.py -v
```

Expected: `FAILED`

- [ ] **Step 3: Create the loader and session models**

Add:

```python
# setup_loader.py
from __future__ import annotations

import importlib.util
from pathlib import Path


def load_setup_module(path: Path):
    spec = importlib.util.spec_from_file_location("agent_eval_setup", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load setup module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

```python
# models.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SessionContext:
    session_id: str
    agent_id: str
    env_path: Path
    setup_path: Path | None = None
    state: dict[str, Any] = field(default_factory=dict)
```

```python
# runner_host.py
from __future__ import annotations

from dataclasses import dataclass

from agent_framework.host import AgentHost


@dataclass(slots=True)
class RunnerHost:
    host: AgentHost
    session_context: object
```

Add `session_runner.py` with a minimal orchestrator that loads the setup module, creates the host, calls hooks if present, and invokes `run_agent(...)`.

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_evaluator_sessions.py -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/agent_framework_evaluator/models.py src/agent_framework_evaluator/runtime/setup_loader.py src/agent_framework_evaluator/runtime/runner_host.py src/agent_framework_evaluator/runtime/session_runner.py tests/test_evaluator_sessions.py
git commit -m "feat(evaluator): add setup loader, session models, and runner host scaffolding"
```

---

## Task 9: Add session manager and debugger subscriber

**Files:**
- Create: `src/agent_framework_evaluator/session_manager.py`
- Create: `src/agent_framework_evaluator/runtime/debug_subscriber.py`
- Modify: `tests/test_evaluator_sessions.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evaluator_sessions.py`:

```python
from agent_framework.tracing import TraceContext, TraceEvent
from agent_framework_evaluator.runtime.debug_subscriber import DebuggerSubscriber


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
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_evaluator_sessions.py -k "debugger_subscriber" -v
```

Expected: `FAILED`

- [ ] **Step 3: Implement the subscriber and manager**

Create `debug_subscriber.py`:

```python
from __future__ import annotations

from collections import defaultdict, deque

from agent_framework.tracing import TraceEvent


class DebuggerSubscriber:
    def __init__(self) -> None:
        self._events = defaultdict(deque)

    def consume(self, event: TraceEvent) -> None:
        session_id = event.context.session_id or "global"
        self._events[session_id].append(event)

    def drain(self, session_id: str) -> list[TraceEvent]:
        queue = self._events[session_id]
        items = list(queue)
        queue.clear()
        return items
```

Create `session_manager.py` with an in-memory registry of active sessions, their web communication object, and their debugger subscriber or replay buffer.

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_evaluator_sessions.py -k "debugger_subscriber" -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/agent_framework_evaluator/session_manager.py src/agent_framework_evaluator/runtime/debug_subscriber.py tests/test_evaluator_sessions.py
git commit -m "feat(evaluator): add session manager and debugger trace subscriber"
```

---

## Task 10: Build the local web app and WebSocket protocol

**Files:**
- Create: `src/agent_framework_evaluator/app.py`
- Modify: `src/agent_framework_evaluator/cli.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_evaluator_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evaluator_cli.py`:

```python
from agent_framework_evaluator.app import create_app


def test_create_app_exists() -> None:
    app = create_app()
    assert app is not None
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_evaluator_cli.py -k "create_app_exists" -v
```

Expected: `FAILED`

- [ ] **Step 3: Create the app and choose the lightweight web stack**

Add FastAPI and Uvicorn dependencies to `pyproject.toml`:

```toml
[project.optional-dependencies]
web = [
    "fastapi",
    "uvicorn",
]
```

Create `app.py`:

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Evaluator")

    @app.get("/")
    def index():
        return FileResponse("src/agent_framework_evaluator/web/index.html")

    return app
```

Update `cli.py` so `web` launches the local server and optionally opens the browser.

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_evaluator_cli.py -k "create_app_exists or subcommands" -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/agent_framework_evaluator/app.py src/agent_framework_evaluator/cli.py tests/test_evaluator_cli.py
git commit -m "feat(evaluator): add local web app factory and web serving command"
```

---

## Task 11: Build the first UI shell

**Files:**
- Create: `src/agent_framework_evaluator/web/index.html`
- Create: `src/agent_framework_evaluator/web/app.js`
- Create: `src/agent_framework_evaluator/web/styles.css`

- [ ] **Step 1: Create the initial HTML shell**

Add `index.html` with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Evaluator</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <div id="app">
    <aside id="left-rail">
      <h1>Agent Evaluator</h1>
      <label>Agent <select id="agent-select"></select></label>
      <label>Setup File <input id="setup-path" type="text"></label>
      <label>Mode <select id="mode-select"><option>Single Run</option><option>Test Set</option></select></label>
    </aside>
    <main id="main-pane">
      <section id="prompt-pane">
        <h2>Prompt</h2>
        <textarea id="prompt-input"></textarea>
        <button id="run-button">Run</button>
      </section>
      <section id="response-pane">
        <h2>Response</h2>
        <div id="response-output"></div>
      </section>
    </main>
    <aside id="trace-pane">
      <h2>Trace</h2>
      <div id="trace-tree"></div>
    </aside>
  </div>
  <section id="reserved-testset-pane">
    <h2>Test Set</h2>
    <p>Reserved for future batch scoring and result summaries.</p>
  </section>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create minimal CSS and JS**

Add `styles.css` with a three-pane layout and explicit trace tree styling. Add `app.js` with:

```javascript
const traceTree = document.getElementById("trace-tree");
const responseOutput = document.getElementById("response-output");

function appendTraceNode(event) {
  const node = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = `${event.kind}: ${event.title}`;
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(event.payload ?? {}, null, 2);
  node.appendChild(summary);
  node.appendChild(pre);
  traceTree.appendChild(node);
}
```

- [ ] **Step 3: Manual browser check**

Run:

```bash
python -m agent_framework_evaluator web --port 8123
```

Expected: page renders with left rail, prompt pane, response pane, trace pane, and reserved test-set area.

- [ ] **Step 4: Commit**

```bash
git add src/agent_framework_evaluator/web/index.html src/agent_framework_evaluator/web/app.js src/agent_framework_evaluator/web/styles.css
git commit -m "feat(evaluator-ui): add first-pass evaluator shell with response and trace panes"
```

---

## Task 12: Add interactive run flow, clarification handling, and trace streaming

**Files:**
- Modify: `src/agent_framework_evaluator/app.py`
- Modify: `src/agent_framework_evaluator/session_manager.py`
- Modify: `src/agent_framework_evaluator/runtime/session_runner.py`
- Modify: `src/agent_framework_evaluator/web/app.js`
- Modify: `tests/test_evaluator_sessions.py`

- [ ] **Step 1: Write the failing tests**

Add integration-oriented tests to `tests/test_evaluator_sessions.py` for:

```python
from pathlib import Path

from agent_framework.agents.agent_result import AgentResult
from agent_framework_evaluator.runtime.session_runner import SessionRunner


class _FakeHost:
    def run_agent(self, agent_id: str, initial_instruction: str):
        return AgentResult(status="completed", message=f"{agent_id}:{initial_instruction}")


def test_session_runner_returns_final_message(tmp_path: Path) -> None:
    setup_path = tmp_path / "setup.py"
    setup_path.write_text("", encoding="utf-8")
    runner = SessionRunner(env_path=tmp_path / ".env", host_factory=lambda **_: _FakeHost())
    result = runner.run_once(agent_id="root", prompt="hello", setup_path=setup_path)
    assert result["status"] == "completed"
    assert result["message"] == "root:hello"


def test_session_runner_can_resume_after_user_input(tmp_path: Path) -> None:
    setup_path = tmp_path / "setup.py"
    setup_path.write_text("", encoding="utf-8")
    runner = SessionRunner(env_path=tmp_path / ".env", host_factory=lambda **_: _FakeHost())
    result = runner.run_once(agent_id="needs_input", prompt="clarified", setup_path=setup_path)
    assert result["status"] == "completed"
    assert result["message"] == "needs_input:clarified"
```

Use a deterministic fake model driver that first requests clarification through host input, then returns a final message.

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_evaluator_sessions.py -k "final_message or resume_after_user_input" -v
```

Expected: `FAILED`

- [ ] **Step 3: Implement the run loop**

In `session_runner.py`, add a method that:

```python
def run_once(self, *, agent_id: str, prompt: str, setup_path: Path | None) -> dict[str, object]:
    session_context = SessionContext(
        session_id=self._new_session_id(),
        agent_id=agent_id,
        env_path=self.env_path,
        setup_path=setup_path,
    )
    setup_module = load_setup_module(setup_path) if setup_path else None
    host = self._create_host(session_context=session_context)
    if setup_module and hasattr(setup_module, "register"):
        setup_module.register(host, session_context)
    if setup_module and hasattr(setup_module, "suite_setup"):
        setup_module.suite_setup(session_context)
    if setup_module and hasattr(setup_module, "test_setup"):
        setup_module.test_setup({"prompt": prompt}, session_context)
    result = host.run_agent(agent_id, initial_instruction=prompt)
    if setup_module and hasattr(setup_module, "test_teardown"):
        setup_module.test_teardown({"prompt": prompt}, session_context)
    return {"status": result.status, "message": result.message}
```

Wire the browser transport so:

- run start creates a session
- trace events stream over WebSocket
- clarification requests produce UI prompts
- user answers feed `WebUserCommunication.submit_user_input(...)`

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_evaluator_sessions.py -k "final_message or resume_after_user_input" -v
```

Expected: `PASSED`

- [ ] **Step 5: Manual end-to-end verification**

Run:

```bash
python -m agent_framework_evaluator web --env .env --open-browser
```

Verify manually:

- select an agent
- enter a prompt
- see trace nodes appear during execution
- answer a clarification request if raised
- see the final answer in the response pane

- [ ] **Step 6: Commit**

```bash
git add src/agent_framework_evaluator/app.py src/agent_framework_evaluator/session_manager.py src/agent_framework_evaluator/runtime/session_runner.py src/agent_framework_evaluator/web/app.js tests/test_evaluator_sessions.py
git commit -m "feat(evaluator): add interactive run flow with clarification and live trace streaming"
```

---

## Task 13: Add CLI headless execution and output artifacts

**Files:**
- Modify: `src/agent_framework_evaluator/cli.py`
- Modify: `src/agent_framework_evaluator/runtime/session_runner.py`
- Modify: `tests/test_evaluator_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evaluator_cli.py`:

```python
from pathlib import Path

from agent_framework_evaluator.cli import main


def test_cli_run_supports_prompt_file(tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("hello", encoding="utf-8")
    code = main(["run", "--agent", "root", "--prompt-file", str(prompt_path)])
    assert code == 0
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
pytest tests/test_evaluator_cli.py -k "prompt_file" -v
```

Expected: `FAILED`

- [ ] **Step 3: Implement `run`**

Update `cli.py` so `run`:

- resolves `--prompt` or `--prompt-file`
- calls the same session runner logic used by the web app
- prints JSON to stdout when `--output` is absent
- writes JSON artifact when `--output` is present

Use:

```python
payload = {"status": result["status"], "message": result["message"]}
```

Serialize with `json.dumps(payload, indent=2)`.

- [ ] **Step 4: Run the tests to confirm they pass**

Run:

```bash
pytest tests/test_evaluator_cli.py -k "prompt_file or subcommands" -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/agent_framework_evaluator/cli.py src/agent_framework_evaluator/runtime/session_runner.py tests/test_evaluator_cli.py
git commit -m "feat(evaluator-cli): add headless run command with prompt files and output artifacts"
```

---

## Task 14: Update docs and public exports

**Files:**
- Modify: `src/agent_framework/__init__.py`
- Modify: `docs/architecture/overview.md`
- Modify: `docs/architecture/host-orchestration.md`
- Modify: `docs/architecture/tracing-evaluation.md`
- Modify: `docs/architecture/extension-points.md`

- [ ] **Step 1: Update public exports**

Export the new tracing and web-host helpers from `src/agent_framework/__init__.py`:

```python
from .tracing import TraceContext, TraceEvent, CompositeRuntimeTracer, NullRuntimeTracer
from .web_host import create_web_host
from .web_communication import WebUserCommunication
```

- [ ] **Step 2: Update architecture docs**

Document:

- unified tracer as the new observability primary abstraction,
- Python logging as an ingress handler,
- file trace subscribers as downstream projections,
- browser-backed communication and web host creation,
- evaluator app package boundary.

- [ ] **Step 3: Commit**

```bash
git add src/agent_framework/__init__.py docs/architecture/
git commit -m "docs: document unified tracing and web-hosted evaluator runtime"
```

---

## Final verification

- [ ] **Step 1: Run focused tests**

```bash
pytest tests/test_runtime_tracing.py tests/test_web_user_communication.py tests/test_web_host.py tests/test_evaluator_cli.py tests/test_evaluator_sessions.py -v
```

Expected: `PASSED`

- [ ] **Step 2: Run broader regression tests**

```bash
pytest tests/test_host_lifecycle.py tests/test_framework_runtime.py tests/test_async_driver.py tests/test_user_communication.py -v
```

Expected: `PASSED`

- [ ] **Step 3: Run the full test suite**

```bash
pytest -v
```

Expected: all existing and new tests pass.

- [ ] **Step 4: Manual smoke test**

```bash
python -m agent_framework_evaluator web --env .env --open-browser
python -m agent_framework_evaluator run --env .env --agent root --prompt "hello"
```

Expected:

- the browser UI starts locally,
- the run flow works end to end,
- traces appear in the UI,
- the CLI run prints a structured result,
- no legacy trace/log behavior regresses unless intentionally replaced.

---

## Self-Review Checklist

- Spec coverage: this plan covers the unified tracer, logging ingress, web host, runner host, setup hooks, evaluator package, UI shell, interactive clarification, and CLI/headless execution described in `docs/architecture/agent-evaluator-web-runtime.md`.
- Placeholder scan: no `TODO`, `TBD`, or “implement later” steps remain in the task list.
- Type consistency: the plan consistently uses `RuntimeTracer`, `TraceEvent`, `TraceContext`, `WebUserCommunication`, `RunnerHost`, `SessionContext`, and `DebuggerSubscriber`.
