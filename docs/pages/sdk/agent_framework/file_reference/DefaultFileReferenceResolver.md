---
title: DefaultFileReferenceResolver
layout: default
sdk_page: true
---


# `DefaultFileReferenceResolver`

Module: [`agent_framework.file_reference`](../file_reference.html)

## API Summary

```python
class DefaultFileReferenceResolver
```

Read text files as UTF-8; fall back to base64 for binary files.

Both variants are wrapped in ``<file>`` XML tags so the model can
identify the source and encoding.

## Methods

### `resolve`

```python
def resolve(self, path: Path) -> str
```

No method docstring is available yet.
