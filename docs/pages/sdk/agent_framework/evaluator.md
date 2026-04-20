---
title: agent_framework.evaluator
layout: default
sdk_page: true
---


# `agent_framework.evaluator`

## API Summary

Prompt-evaluation support for agent regression testing.

## Source

`src/agent_framework/evaluator.py`

## Classes

- [`EvaluationScene`](evaluator/EvaluationScene.html)
- [`EvaluationInput`](evaluator/EvaluationInput.html)
- [`RecordedInteraction`](evaluator/RecordedInteraction.html)
- [`PromptScore`](evaluator/PromptScore.html)
- [`JudgeResult`](evaluator/JudgeResult.html)
- [`OpenAiEvaluationScene`](evaluator/OpenAiEvaluationScene.html)
- [`OpenAiEvaluationInput`](evaluator/OpenAiEvaluationInput.html)
- [`FormatEvaluationResult`](evaluator/FormatEvaluationResult.html)
- [`EvaluationSummary`](evaluator/EvaluationSummary.html)
- [`ResultJudge`](evaluator/ResultJudge.html)
- [`OpenAiResultJudge`](evaluator/OpenAiResultJudge.html)
- [`RecordingAgentHost`](evaluator/RecordingAgentHost.html)
- [`AgentPromptEvaluator`](evaluator/AgentPromptEvaluator.html)
- [`OpenAiConversationEvaluator`](evaluator/OpenAiConversationEvaluator.html)

## Functions

### `validate_json_object_output`

```python
def validate_json_object_output(result_text: str) -> FormatEvaluationResult
```

Validate that the result is a JSON object and return a normalized string.

### `validate_json_output`

```python
def validate_json_output(result_text: str) -> FormatEvaluationResult
```

Validate that the result is valid JSON and return a normalized string.
