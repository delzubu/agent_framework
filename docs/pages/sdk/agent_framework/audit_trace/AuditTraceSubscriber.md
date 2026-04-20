---
title: AuditTraceSubscriber
layout: default
sdk_page: true
---


# `AuditTraceSubscriber`

Module: [`agent_framework.audit_trace`](../audit_trace.html)

## API Summary

```python
class AuditTraceSubscriber
```

Maps unified ``TraceEvent`` stream to :class:`InMemoryAuditTracer` JSONL records.

Subscribes only to ``runtime`` and ``llm`` channels (ignores ``log`` and ``user``).

## Attributes

- `trace_channels`

## Methods

### `consume`

```python
def consume(self, event: 'TraceEvent') -> None
```

No method docstring is available yet.
