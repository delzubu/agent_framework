---
title: agent_framework.agent_registry
layout: default
sdk_page: true
---


# `agent_framework.agent_registry`

## API Summary

Formal agent registry for AgentHost.

## Source

`src/agent_framework/agent_registry.py`

## Classes

- [`AgentRegistry`](agent_registry/AgentRegistry.html)

## Functions

### `normalize_agent_id`

```python
def normalize_agent_id(agent_ref: str) -> str
```

Turn ``deck-review-agent.md`` into ``deck-review-agent`` for catalog lookup.
