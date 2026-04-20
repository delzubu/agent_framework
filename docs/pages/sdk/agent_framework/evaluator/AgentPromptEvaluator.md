---
title: AgentPromptEvaluator
layout: default
sdk_page: true
---


# `AgentPromptEvaluator`

Module: [`agent_framework.evaluator`](../evaluator.html)

## API Summary

```python
class AgentPromptEvaluator
```

Runs agent prompts and aggregates evaluation scores.

## Attributes

- `agent_id`
- `host`
- `judge`

## Methods

### `evaluate_file`

```python
def evaluate_file(self, path: str | Path, *, agent_id: str | None = None) -> EvaluationSummary
```

Parse an XML evaluation file and evaluate all contained prompts.

### `evaluate_input`

```python
def evaluate_input(self, evaluation_input: EvaluationInput, *, agent_id: str | None = None) -> EvaluationSummary
```

Evaluate all prompts from a parsed evaluation input.

### `parse_input_file`

```python
def parse_input_file(path: str | Path) -> EvaluationInput
```

Parse the XML evaluator input file.
