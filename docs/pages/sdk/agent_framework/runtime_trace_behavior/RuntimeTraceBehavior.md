---
title: RuntimeTraceBehavior
layout: default
sdk_page: true
---


# `RuntimeTraceBehavior`

Module: [`agent_framework.runtime_trace_behavior`](../runtime_trace_behavior.html)

## API Summary

```python
class RuntimeTraceBehavior(AgentBehavior)
```

Emits runtime-channel TraceEvents via ``host.publish_trace_event`` when tracing is active.

## Methods

### `attach`

```python
def attach(self, agent: Agent) -> None
```

No method docstring is available yet.

### `before_run`

```python
def before_run(self, agent: Agent, host: AgentHostProtocol, *, run: AgentRun, caller_id: str | None) -> AgentHookDecision | None
```

No method docstring is available yet.

### `after_run`

```python
def after_run(self, agent: Agent, host: AgentHostProtocol, *, run: AgentRun, caller_id: str | None, result: AgentResult) -> AgentEndHookDecision | AgentResult | None
```

No method docstring is available yet.
