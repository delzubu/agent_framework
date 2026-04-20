---
title: InMemoryAuditTracer
layout: default
sdk_page: true
---


# `InMemoryAuditTracer`

Module: [`agent_framework.audit_trace`](../audit_trace.html)

## API Summary

```python
class InMemoryAuditTracer
```

Host-owned audit tracer that stays separate from agent runtime state.

## Attributes

- `active_records`
- `output_dir`
- `output_path`

## Methods

### `start_agent_call`

```python
def start_agent_call(self, *, run_id: str, caller_id: str | None, agent_name: str, system_prompt: str, system_prompt_sources: tuple[str, ...], user_prompt: str, user_prompt_sources: tuple[str, ...]) -> None
```

No method docstring is available yet.

### `record_llm_request`

```python
def record_llm_request(self, *, run_id: str, payload: Any) -> None
```

No method docstring is available yet.

### `record_llm_response`

```python
def record_llm_response(self, *, run_id: str, raw_text: str, parsed_payload: dict[str, Any] | None) -> None
```

No method docstring is available yet.

### `record_decision`

```python
def record_decision(self, *, run_id: str, decision: AgentDecision) -> None
```

No method docstring is available yet.

### `record_callback`

```python
def record_callback(self, *, run_id: str, intent: str, prompt: str, target: str, response: str | None = None) -> None
```

No method docstring is available yet.

### `record_skill_invocation`

```python
def record_skill_invocation(self, *, run_id: str, skill_name: str, parameters: dict[str, Any], inventory: list[str]) -> None
```

No method docstring is available yet.

### `record_event`

```python
def record_event(self, *, run_id: str, event: dict[str, Any]) -> None
```

No method docstring is available yet.

### `finish_agent_call`

```python
def finish_agent_call(self, *, run_id: str) -> None
```

No method docstring is available yet.

### `record_user_output`

```python
def record_user_output(self, *, role: str, text: str) -> None
```

Append a host-level user-output record to the session JSONL.

### `record_user_input`

```python
def record_user_input(self, *, prompt: str, response: str) -> None
```

Append a host-level user-input record to the session JSONL.

### `record_permission`

```python
def record_permission(self, *, tool_name: str, action: str, resource: str, summary: str, allowed: bool, remember_for_session: bool) -> None
```

Append a host-level permission-request record to the session JSONL.
