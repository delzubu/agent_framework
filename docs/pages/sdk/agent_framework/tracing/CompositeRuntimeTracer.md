---
title: CompositeRuntimeTracer
layout: default
sdk_page: true
---


# `CompositeRuntimeTracer`

Module: [`agent_framework.tracing`](../tracing.html)

## API Summary

```python
class CompositeRuntimeTracer
```

No class docstring is available yet.

## Attributes

- `base_context`
- `subscribers`

## Methods

### `publish`

```python
def publish(self, event: TraceEvent) -> None
```

No method docstring is available yet.

### `child`

```python
def child(self, **context_updates: Any) -> CompositeRuntimeTracer
```

No method docstring is available yet.

### `subscribe`

```python
def subscribe(self, subscriber: TraceSubscriber) -> None
```

No method docstring is available yet.

### `unsubscribe`

```python
def unsubscribe(self, subscriber: TraceSubscriber) -> None
```

No method docstring is available yet.
