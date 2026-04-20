---
title: CapabilityDefinition
layout: default
sdk_page: true
---


# `CapabilityDefinition`

Module: [`agent_framework.model`](../model.html)

## API Summary

```python
class CapabilityDefinition
```

Structured capability description injected by the runtime.

## Attributes

- `capability_id`
- `description`
- `parameters`
- `priority`

## Methods

### `to_model_payload`

```python
def to_model_payload(self) -> dict[str, object]
```

Return a serializable payload for model-facing capability injection.
