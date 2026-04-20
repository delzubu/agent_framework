---
title: CapabilityParameter
layout: default
sdk_page: true
---


# `CapabilityParameter`

Module: [`agent_framework.model`](../model.html)

## API Summary

```python
class CapabilityParameter
```

Structured parameter description for subagents or skills.

## Attributes

- `description`
- `name`
- `required`
- `value_type`

## Methods

### `to_model_payload`

```python
def to_model_payload(self) -> dict[str, object]
```

Convert the parameter description to a serializable payload.
