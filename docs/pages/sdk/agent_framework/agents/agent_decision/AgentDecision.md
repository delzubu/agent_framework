---
title: AgentDecision
layout: default
sdk_page: true
---


# `AgentDecision`

Module: [`agent_framework.agents.agent_decision`](../agent_decision.html)

## API Summary

```python
class AgentDecision
```

Normalized decision emitted by the model for one loop iteration.

## Attributes

- `batch_mode`
- `batch_timeout_seconds`
- `callback_intent`
- `kind`
- `message`
- `parameters`
- `skill_name`
- `subagent_calls`
- `subagent_id`
- `tool_name`

## Methods

### `from_model_response`

```python
def from_model_response(cls, response: ModelResponse) -> 'AgentDecision'
```

Create an `AgentDecision` from a normalized model response.
