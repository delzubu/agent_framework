---
title: AgentCallAuditRecord
layout: default
sdk_page: true
---


# `AgentCallAuditRecord`

Module: [`agent_framework.audit_trace`](../audit_trace.html)

## API Summary

```python
class AgentCallAuditRecord
```

Immutable audit record for a single agent invocation.

## Attributes

- `agent_decision`
- `agent_name`
- `callbacks`
- `caller_id`
- `events`
- `llm_message_received`
- `llm_message_sent`
- `model_response`
- `run_id`
- `skill_invocations`
- `system_prompt`
- `system_prompt_sources`
- `timestamp`
- `user_prompt`
- `user_prompt_sources`

## Methods

### `to_jsonable`

```python
def to_jsonable(self) -> dict[str, Any]
```

No method docstring is available yet.
