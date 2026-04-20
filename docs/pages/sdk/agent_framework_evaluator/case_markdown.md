---
title: agent_framework_evaluator.case_markdown
layout: default
sdk_page: true
---


# `agent_framework_evaluator.case_markdown`

## API Summary

Load evaluator test cases from markdown files (``---``-separated frontmatter, prompt, criteria).

Use :class:`MarkdownCaseLoader` from an initializer's ``get_test_cases()`` so cases live in
``*.md`` files next to the initializer module, e.g. ``eval/deck-review-01.md`` with glob
``eval/*.md`` relative to ``deck-review.py``.

## Source

`src/agent_framework_evaluator/case_markdown.py`

## Classes

- [`MarkdownCaseLoader`](case_markdown/MarkdownCaseLoader.html)

## Functions

### `parse_simple_frontmatter`

```python
def parse_simple_frontmatter(text: str) -> dict[str, str]
```

Parse ``key: value`` lines (no nesting). For nested YAML use ``yaml.safe_load`` on the block.

### `parse_case_markdown_file`

```python
def parse_case_markdown_file(*, path: Path, evaluator_registry: Mapping[str, Callable[..., Any]], resolver: FileReferenceResolver | None = None) -> dict[str, Any] | None
```

Parse one case file; return case metadata, prompt, criteria, and evaluator hooks.
