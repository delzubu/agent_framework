---
title: TraceEvent
layout: default
sdk_page: true
---


# `TraceEvent`

Module: [`agent_framework.tracing`](../tracing.html)

## API Summary

```python
class TraceEvent
```

A single trace row on the unified bus.

Semantics by channel:

- ``runtime``, ``llm``, ``user``: agent / model / user-interaction spans. Typically carry
  ``span_id``, ``parent_span_id``, and rich ``TraceContext`` (``run_id``, ``agent_id``, …).
- ``log``: Python ``logging`` output. Flat record shape; ``span_id`` and ``parent_span_id``
  stay ``None``; ``context`` should only carry ``session_id`` when needed for routing.

## Attributes

- `channel`
- `context`
- `event_id`
- `kind`
- `level`
- `parent_event_id`
- `parent_span_id`
- `payload`
- `span_id`
- `summary`
- `tags`
- `timestamp`
- `title`

## Methods

### `with_context`

```python
def with_context(self, overlay: TraceContext) -> TraceEvent
```

No method docstring is available yet.
