---
title: AgentHostProtocol
layout: default
sdk_page: true
---


# `AgentHostProtocol`

Module: [`agent_framework.agents.agent_host_protocol`](../agent_host_protocol.html)

## API Summary

```python
class AgentHostProtocol(Protocol)
```

Minimal host contract required by `Agent.run()`.

## Methods

### `get_model_driver`

```python
def get_model_driver(self, agent: 'Agent') -> ModelDriver
```

No method docstring is available yet.

### `get_agent`

```python
def get_agent(self, agent_id: str, *, base_dir: Path | None = None) -> 'Agent'
```

No method docstring is available yet.

### `request_user_input`

```python
def request_user_input(self, prompt: str) -> str
```

No method docstring is available yet.

### `call_subagent`

```python
def call_subagent(self, *, caller: 'Agent', callee_id: str, parameters: dict[str, Any], parent_run_id: str | None = None, run_id: str | None = None, in_parallel_batch: bool = False, conversation_messages: 'tuple[dict, ...] | None' = None) -> AgentResult
```

No method docstring is available yet.

### `call_subagent_batch`

```python
def call_subagent_batch(self, *, caller: 'Agent', specs: 'tuple[SubagentCallSpec, ...]', mode: str, timeout_seconds: 'float | None', parent_run_id: 'str | None' = None) -> list
```

No method docstring is available yet.

### `save_checkpoint`

```python
def save_checkpoint(self, run_id: str, messages: list) -> None
```

No method docstring is available yet.

### `load_checkpoint`

```python
def load_checkpoint(self, run_id: str) -> 'list | None'
```

No method docstring is available yet.

### `execute_tool`

```python
def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> str
```

No method docstring is available yet.

### `get_tool`

```python
def get_tool(self, tool_name: str)
```

No method docstring is available yet.

### `resolve_callback`

```python
def resolve_callback(self, *, caller_id: str, callee: 'Agent', prompt: str) -> str
```

No method docstring is available yet.

### `open_context`

```python
def open_context(self, *, caller_id: str, callee_id: str, kind: str) -> CallContext
```

No method docstring is available yet.

### `run_pre_model_hooks`

```python
def run_pre_model_hooks(self, event: ModelStartEvent) -> None
```

No method docstring is available yet.

### `run_post_model_hooks`

```python
def run_post_model_hooks(self, event: ModelEndEvent) -> None
```

No method docstring is available yet.

### `get_skill_registry`

```python
def get_skill_registry(self) -> 'Any'
```

No method docstring is available yet.

### `register_tool`

```python
def register_tool(self, tool: 'Any') -> None
```

No method docstring is available yet.
