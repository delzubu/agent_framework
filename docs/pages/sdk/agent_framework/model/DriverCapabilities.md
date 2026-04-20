---
title: DriverCapabilities
layout: default
sdk_page: true
---


# `DriverCapabilities`

Module: [`agent_framework.model`](../model.html)

## API Summary

```python
class DriverCapabilities
```

Declared capabilities of a model driver.

Drivers expose a ``capabilities`` class attribute so callers can inspect
what a driver supports before constructing a ``ModelContext`` or invoking
``decide``.  Use ``get_driver_capabilities()`` to query any driver safely.

Attributes:
    is_async: True if the driver's ``decide`` method is a coroutine.
    supports_multimodal: True if the driver accepts image ``ContentPart``
        objects in ``ModelContext.messages``.
    supports_response_format: True if the driver forwards
        ``ModelContext.response_format`` to the provider.
    supports_tools: True if the driver forwards native tool definitions to
        the provider rather than embedding them in the system prompt.
    supports_streaming: True if the driver supports streaming responses.

## Attributes

- `is_async`
- `supports_multimodal`
- `supports_response_format`
- `supports_streaming`
- `supports_tools`
