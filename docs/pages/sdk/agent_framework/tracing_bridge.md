---
title: agent_framework.tracing_bridge
layout: default
sdk_page: true
---


# `agent_framework.tracing_bridge`

## API Summary

Context-local bridge so sync code (hooks, user comm) can reach the active runtime tracer.

## Source

`src/agent_framework/tracing_bridge.py`

## Functions

### `get_active_tracer`

```python
def get_active_tracer() -> tuple[Any, Any] | None
```

Return ``(runtime_tracer, trace_context_overlay)`` for the current agent run, or ``None``.

### `active_tracer_scope`

```python
def active_tracer_scope(tracer: Any, overlay: Any | None) -> Iterator[None]
```

Bind ``tracer`` and optional ``trace_context_overlay`` for the duration of the block.

### `try_publish_trace`

```python
def try_publish_trace(*, channel: str, kind: str, title: str, summary: str = '', payload: dict[str, Any] | None = None) -> None
```

Publish a trace event if an active non-null tracer is bound (no-op otherwise).
