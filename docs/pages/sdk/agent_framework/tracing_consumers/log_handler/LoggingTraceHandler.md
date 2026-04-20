---
title: LoggingTraceHandler
layout: default
sdk_page: true
---


# `LoggingTraceHandler`

Module: [`agent_framework.tracing_consumers.log_handler`](../log_handler.html)

## API Summary

```python
class LoggingTraceHandler(logging.Handler)
```

Publish :class:`logging.LogRecord` as ``channel="log"`` events to one tracer (tests, ad-hoc wiring).

## Methods

### `emit`

```python
def emit(self, record: logging.LogRecord) -> None
```

No method docstring is available yet.
