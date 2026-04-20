---
title: agent_framework.llm_trace_logging
layout: default
sdk_page: true
---


# `agent_framework.llm_trace_logging`

## API Summary

Host-level tracing for exact model request and response payloads.

## Source

`src/agent_framework/llm_trace_logging.py`

## Classes

- [`LlmTraceLogger`](llm_trace_logging/LlmTraceLogger.html)

## Functions

### `build_llm_trace_event`

```python
def build_llm_trace_event(trace: Any, *, kind: str, level: str = 'info') -> TraceEvent
```

No function docstring is available yet.

### `wire_llm_traces_to_runtime_tracer`

```python
def wire_llm_traces_to_runtime_tracer(host: Any) -> None
```

Chain driver I/O callbacks so ``llm.request`` / ``llm.response`` / ``llm.error`` reach ``host.runtime_tracer``.

Preserves existing callbacks (e.g. audit trace from ``enable_audit_trace``). Safe to call when
``runtime_tracer`` is null or ``NullRuntimeTracer`` (no-op).
Idempotent per host instance unless ``host._llm_traces_wired`` is cleared (e.g. after replacing
``runtime_tracer``).

### `attach_to_host`

```python
def attach_to_host(host, *, target: str = 'file', output_dir: str | Path = 'logs') -> None
```

Attach LLM I/O tracing callbacks to a host.
