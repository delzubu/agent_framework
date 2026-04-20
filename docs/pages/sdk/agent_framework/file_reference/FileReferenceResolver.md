---
title: FileReferenceResolver
layout: default
sdk_page: true
---


# `FileReferenceResolver`

Module: [`agent_framework.file_reference`](../file_reference.html)

## API Summary

```python
class FileReferenceResolver(Protocol)
```

Strategy for turning a resolved file ``Path`` into prompt text.

## Methods

### `resolve`

```python
def resolve(self, path: Path) -> str
```

Return the string to substitute for the ``@ref`` token.

Raise ``OSError`` if the file cannot be read; the token is then left
unchanged in the prompt.
