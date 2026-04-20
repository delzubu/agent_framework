---
title: RecordingAgentHost
layout: default
sdk_page: true
---


# `RecordingAgentHost`

Module: [`agent_framework.evaluator`](../evaluator.html)

## API Summary

```python
class RecordingAgentHost(AgentHost)
```

Agent host variant that records runtime interactions during evaluation.

## Attributes

- `auto_input_response`
- `recorded_interactions`

## Methods

### `from_host`

```python
def from_host(cls, host: AgentHost) -> 'RecordingAgentHost'
```

Create a recording wrapper host that shares config and registries with a base host.

### `snapshot_interactions`

```python
def snapshot_interactions(self) -> tuple[dict[str, Any], ...]
```

Return the recorded interactions in JSON-serializable form.

### `call_subagent`

```python
def call_subagent(self, *, caller, callee_id: str, parameters: dict[str, Any], parent_run_id: str | None = None)
```

Record a subagent call and its result.

### `execute_tool`

```python
def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> str
```

Record a tool call and its result.

### `resolve_callback`

```python
def resolve_callback(self, *, caller_id: str, callee, prompt: str) -> str
```

Record a callback request and avoid interactive roundtrips at the host boundary.

### `request_user_input`

```python
def request_user_input(self, prompt: str) -> str
```

Record direct user-input requests without interactive prompting.
