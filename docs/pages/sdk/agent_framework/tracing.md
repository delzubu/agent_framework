---
title: agent_framework.tracing
layout: default
sdk_page: true
---


# `agent_framework.tracing`

## API Summary

Unified runtime tracing: structured events, fan-out tracer, no-op implementation.

## Source

`src/agent_framework/tracing.py`

## Classes

- [`LogEventPayload`](tracing/LogEventPayload.html)
- [`TraceContext`](tracing/TraceContext.html)
- [`TraceEvent`](tracing/TraceEvent.html)
- [`TraceSubscriber`](tracing/TraceSubscriber.html)
- [`RuntimeTracer`](tracing/RuntimeTracer.html)
- [`CompositeRuntimeTracer`](tracing/CompositeRuntimeTracer.html)
- [`NullRuntimeTracer`](tracing/NullRuntimeTracer.html)

## Functions

### `utc_now_iso`

```python
def utc_now_iso() -> str
```

No function docstring is available yet.

### `make_trace_event`

```python
def make_trace_event(*, kind: str, title: str, summary: str = '', channel: TraceChannel = 'runtime', level: TraceLevel = 'info', span_id: str | None = None, parent_span_id: str | None = None, parent_event_id: str | None = None, context: TraceContext | None = None, payload: dict[str, Any] | None = None, tags: tuple[str, ...] = ()) -> TraceEvent
```

Build a ``TraceEvent`` with fresh id and timestamp (shared construction path).
