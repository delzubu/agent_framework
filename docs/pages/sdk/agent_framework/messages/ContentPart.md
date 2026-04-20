---
title: ContentPart
layout: default
sdk_page: true
---


# `ContentPart`

Module: [`agent_framework.messages`](../messages.html)

## API Summary

```python
class ContentPart
```

A single part of a multimodal message content array.

Attributes:
    type: Content type — ``"text"`` or ``"image_url"``.
    text: Text content (when ``type == "text"``).
    image_url: Image URL descriptor (when ``type == "image_url"``).

## Attributes

- `image_url`
- `text`
- `type`

## Methods

### `to_dict`

```python
def to_dict(self) -> dict[str, Any]
```

No method docstring is available yet.

### `from_dict`

```python
def from_dict(cls, data: dict[str, Any]) -> ContentPart
```

No method docstring is available yet.
