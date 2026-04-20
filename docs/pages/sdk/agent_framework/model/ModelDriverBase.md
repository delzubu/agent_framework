---
title: ModelDriverBase
layout: default
sdk_page: true
---


# `ModelDriverBase`

Module: [`agent_framework.model`](../model.html)

## API Summary

```python
class ModelDriverBase
```

Shared agent-agnostic runtime prompt assembly for concrete model drivers.

Holds capability metadata, mode templates, and merge helpers so derived
drivers only implement transport.  Not abstract — subclass or use mixins.

**Conversation store:** loading and persisting history for
:meth:`agent_framework.host.AgentHost.complete` remains on the host
(``conversation_id`` + store).  Optional driver-base hooks for other
persistence shapes are a future extension (see architecture ADR).

**Native tool callbacks:** when a provider executes tools inside its SDK,
the runtime should delegate to the agent loop via an injectable bridge
(planned extension; see ADR) rather than synthetic chat messages.

## Methods

### `decision_instructions`

```python
def decision_instructions(cls, tools: tuple[ToolDefinition, ...], subagents: tuple[CapabilityDefinition, ...], skills: tuple[CapabilityDefinition, ...]) -> str
```

Return the generic decision envelope instructions as text.

### `shared_instructions`

```python
def shared_instructions(cls, tools: tuple[ToolDefinition, ...], subagents: tuple[CapabilityDefinition, ...], skills: tuple[CapabilityDefinition, ...]) -> str
```

Return the shared runtime capability block without a mode suffix.
