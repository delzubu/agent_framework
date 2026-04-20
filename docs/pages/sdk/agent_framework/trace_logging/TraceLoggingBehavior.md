---
title: TraceLoggingBehavior
layout: default
sdk_page: true
---


# `TraceLoggingBehavior`

Module: [`agent_framework.trace_logging`](../trace_logging.html)

## API Summary

```python
class TraceLoggingBehavior(AgentBehavior)
```

Write lifecycle traces for agent, tool, and subagent activity.

## Methods

### `attach`

```python
def attach(self, agent) -> None
```

Attach tracing hooks to the supplied agent instance.
