---
title: MarkdownCaseLoader
layout: default
sdk_page: true
---


# `MarkdownCaseLoader`

Module: [`agent_framework_evaluator.case_markdown`](../case_markdown.html)

## API Summary

```python
class MarkdownCaseLoader
```

Discover ``*.md`` cases under ``base_dir`` with a glob; cache invalidates on path/mtime changes.

Pass ``initializer_ref`` to automatically skip cases whose ``initializer`` frontmatter
field is set to a different initializer (stem comparison, so ``foo.py`` matches ``foo``).
Cases with no ``initializer`` frontmatter field always match.

## Methods

### `get_test_cases`

```python
def get_test_cases(self) -> list[dict[str, Any]]
```

No method docstring is available yet.
