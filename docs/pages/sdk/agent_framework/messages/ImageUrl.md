---
title: ImageUrl
layout: default
sdk_page: true
---


# `ImageUrl`

Module: [`agent_framework.messages`](../messages.html)

## API Summary

```python
class ImageUrl
```

Image URL content with optional detail level.

Attributes:
    url: Either a public https URL or a data URI (``data:image/png;base64,...``).
    detail: Resolution hint — ``"auto"``, ``"low"``, or ``"high"``.

## Attributes

- `detail`
- `url`

## Methods

### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

No method docstring is available yet.

### `from_dict`

```python
def from_dict(cls, data: dict[str, Any]) -> ImageUrl
```

No method docstring is available yet.
