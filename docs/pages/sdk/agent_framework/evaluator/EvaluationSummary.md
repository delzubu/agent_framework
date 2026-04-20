---
title: EvaluationSummary
layout: default
sdk_page: true
---


# `EvaluationSummary`

Module: [`agent_framework.evaluator`](../evaluator.html)

## API Summary

```python
class EvaluationSummary
```

Aggregate evaluation output.

## Attributes

- `overall_score`
- `prompt_scores`

## Methods

### `to_json`

```python
def to_json(self) -> str
```

Serialize the evaluation summary for CLI output.

### `to_markdown_table`

```python
def to_markdown_table(self) -> str
```

Render a compact markdown summary table for CLI output.
