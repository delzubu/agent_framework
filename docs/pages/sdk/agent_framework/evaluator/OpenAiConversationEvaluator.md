---
title: OpenAiConversationEvaluator
layout: default
sdk_page: true
---


# `OpenAiConversationEvaluator`

Module: [`agent_framework.evaluator`](../evaluator.html)

## API Summary

```python
class OpenAiConversationEvaluator
```

Evaluate raw agent conversation inputs without parameter mapping.

## Attributes

- `agent_id`
- `format_evaluators`
- `host`
- `judge`

## Methods

### `evaluate_file`

```python
def evaluate_file(self, path: str | Path) -> EvaluationSummary
```

Parse a JSON evaluation file and evaluate all contained scenes.

### `parse_input_file`

```python
def parse_input_file(path: str | Path) -> OpenAiEvaluationInput
```

Parse the JSON evaluator input file.

### `evaluate_input`

```python
def evaluate_input(self, evaluation_input: OpenAiEvaluationInput) -> EvaluationSummary
```

Evaluate all raw-input scenes from a parsed JSON file.
