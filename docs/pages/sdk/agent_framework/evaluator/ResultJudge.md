---
title: ResultJudge
layout: default
sdk_page: true
---


# `ResultJudge`

Module: [`agent_framework.evaluator`](../evaluator.html)

## API Summary

```python
class ResultJudge(Protocol)
```

Scores one agent result for quality on a 1-10 scale.

## Methods

### `score`

```python
def score(self, *, evaluator_prompt: str, prompt: str, expected: str, result: str, interactions: tuple[dict[str, Any], ...]) -> JudgeResult
```

Return a structured judge result for one prompt/result pair.
