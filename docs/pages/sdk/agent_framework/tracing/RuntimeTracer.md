---
title: RuntimeTracer
layout: default
sdk_page: true
---


# `RuntimeTracer`

Module: [`agent_framework.tracing`](../tracing.html)

## API Summary

```python
class RuntimeTracer(Protocol)
```

No class docstring is available yet.

## Methods

### `publish`

```python
def publish(self, event: TraceEvent) -> None
```

No method docstring is available yet.

### `child`

```python
def child(self, **context_updates: Any) -> RuntimeTracer
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
