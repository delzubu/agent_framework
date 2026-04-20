---
title: AgentEventPublisher
layout: default
sdk_page: true
---


# `AgentEventPublisher`

Module: [`agent_framework.agent_event_publisher`](../agent_event_publisher.html)

## API Summary

```python
class AgentEventPublisher
```

Typed trace emission for agent runs; uses :func:`get_active_tracer` (no host reference).

## Methods

### `attach_log_sources`

```python
def attach_log_sources(self, logger_names: list[str] | None = None) -> None
```

No method docstring is available yet.

### `detach_log_sources`

```python
def detach_log_sources(self) -> None
```

No method docstring is available yet.

### `audit_agent_call_started`

```python
def audit_agent_call_started(self, *, run_id: str, parent_run_id: str | None = None, caller_id: str | None, agent_name: str, system_prompt: str, system_prompt_sources: tuple[str, ...], user_prompt: str, user_prompt_sources: tuple[str, ...]) -> None
```

Emit audit start. ``parent_run_id`` is the caller agent's run id when this is a subagent (parallel-safe nesting).

### `audit_agent_call_finished`

```python
def audit_agent_call_finished(self, *, run_id: str) -> None
```

No method docstring is available yet.

### `audit_decision`

```python
def audit_decision(self, *, run_id: str, agent_id: str, decision: AgentDecision) -> None
```

No method docstring is available yet.

### `audit_callback`

```python
def audit_callback(self, *, run_id: str, agent_id: str, intent: str, prompt: str, target: str, response: str | None, event_dict: dict[str, Any]) -> None
```

No method docstring is available yet.

### `audit_named_event`

```python
def audit_named_event(self, *, run_id: str, agent_id: str, event: dict[str, Any]) -> None
```

No method docstring is available yet.

### `audit_skill_invocation`

```python
def audit_skill_invocation(self, *, run_id: str, agent_id: str, skill_name: str, parameters: dict[str, Any], inventory: list[str]) -> None
```

No method docstring is available yet.

### `on_context_updated`

```python
def on_context_updated(self, *, run_id: str, agent_id: str, message: dict[str, Any], source: str) -> None
```

No method docstring is available yet.

### `on_model_call_failed`

```python
def on_model_call_failed(self, *, run_id: str, agent_id: str, caller_id: str | None, exc: BaseException, status_code: int | None = None, upstream_body: str | None = None) -> None
```

No method docstring is available yet.

### `on_tool_execution_failed`

```python
def on_tool_execution_failed(self, *, run_id: str, agent_id: str, tool_name: str, exc: BaseException) -> None
```

No method docstring is available yet.

### `on_callback_requested`

```python
def on_callback_requested(self, *, run_id: str, agent_id: str, caller_id: str | None, intent: str, prompt: str, to_caller: bool) -> None
```

No method docstring is available yet.

### `on_callback_answered`

```python
def on_callback_answered(self, *, run_id: str, agent_id: str, caller_id: str | None, intent: str, target: str, answer: str) -> None
```

No method docstring is available yet.
