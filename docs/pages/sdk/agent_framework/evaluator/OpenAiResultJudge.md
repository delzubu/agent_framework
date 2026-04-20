---
title: OpenAiResultJudge
layout: default
sdk_page: true
---


# `OpenAiResultJudge`

Module: [`agent_framework.evaluator`](../evaluator.html)

## API Summary

```python
class OpenAiResultJudge
```

OpenAI-backed evaluator for prompt-result quality.

## Attributes

- `api_key`
- `model_name`

## Methods

### `score`

```python
def score(self, *, evaluator_prompt: str, prompt: str, expected: str, result: str, interactions: tuple[dict[str, Any], ...]) -> JudgeResult
```

Evaluate one prompt/result pair and return a normalized 1-10 score.
