---
title: agent_framework.file_reference
layout: default
sdk_page: true
---


# `agent_framework.file_reference`

## API Summary

@filename injection — file reference resolution strategy.

## Source

`src/agent_framework/file_reference.py`

## Classes

- [`FileReferenceResolver`](file_reference/FileReferenceResolver.html)
- [`DefaultFileReferenceResolver`](file_reference/DefaultFileReferenceResolver.html)

## Functions

### `expand_file_refs`

```python
def expand_file_refs(text: str, resolver: FileReferenceResolver, base_dir: Path | None = None) -> str
```

Replace every ``@ref`` token in *text* with its resolved content.

Tokens that cannot be resolved (file not found, permission error) are left
unchanged so the caller can decide how to handle them.

Args:
    text: Prompt string possibly containing ``@filename`` or ``@"path"`` tokens.
    resolver: Strategy that converts a resolved :class:`Path` to a string.
    base_dir: Directory used to resolve relative paths. Defaults to ``Path.cwd()``.
