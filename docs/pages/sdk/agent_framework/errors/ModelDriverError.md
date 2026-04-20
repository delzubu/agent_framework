---
title: ModelDriverError
layout: default
sdk_page: true
---


# `ModelDriverError`

Module: [`agent_framework.errors`](../errors.html)

## API Summary

```python
class ModelDriverError(Exception)
```

Error raised by a model driver with optional HTTP context.

Attributes:
    status_code: HTTP status code from the upstream provider, or None for
        transport-level errors.
    upstream_body: Raw upstream response body excerpt for debugging, or
        None if not available.
