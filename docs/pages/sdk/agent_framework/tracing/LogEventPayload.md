---
title: LogEventPayload
layout: default
sdk_page: true
---


# `LogEventPayload`

Module: [`agent_framework.tracing`](../tracing.html)

## API Summary

```python
class LogEventPayload(TypedDict)
```

Fixed payload schema for ``channel="log"`` events (Python ``logging`` records).

Log events are flat diagnostics: no ``span_id`` / ``parent_span_id`` semantics.

## Attributes

- `exc_text`
- `funcName`
- `lineno`
- `logger_name`
- `message`
- `module`
- `pathname`
