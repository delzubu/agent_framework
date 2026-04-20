---
title: agent_framework_evaluator.evaluation
layout: default
sdk_page: true
---


# `agent_framework_evaluator.evaluation`

## API Summary

Post-run LLM evaluation for the agent evaluator web UI.

## Source

`src/agent_framework_evaluator/evaluation.py`

## Functions

### `extract_first_llm_request_prompts`

```python
def extract_first_llm_request_prompts(input_payload: Any) -> dict[str, Any]
```

Extract system and every ``user`` message from the first provider request (in order).

Multiple user turns are common (task text, then skills catalog, etc.).

### `extract_initial_prompts`

```python
def extract_initial_prompts(input_payload: Any) -> dict[str, str]
```

Extract first system and first user message (evaluation / backward compatibility).

### `format_eval_input`

```python
def format_eval_input(system_prompt: str, user_prompt: str, criteria: str, agent_message: str) -> str
```

Build XML-tagged user content for the evaluator model.

### `select_agent_result_field`

```python
def select_agent_result_field(agent_result: Any, field_name: Any) -> str | None
```

Select *field_name* (dot-delimited path) from *agent_result*.

Returns ``None`` when the path does not exist in the result dict, so callers
can distinguish a missing field from an empty value and raise an appropriate
error.  Returns the full stringified payload when *field_name* is ``"."``.

### `failed_evaluator_result`

```python
def failed_evaluator_result(error_message: str) -> dict[str, Any]
```

Return a zero-score result with the error in verdict and criterion reasoning.

### `parse_eval_response`

```python
def parse_eval_response(payload: dict[str, Any]) -> dict[str, Any]
```

Map evaluator LLM JSON to API / UI fields.

### `run_code_evaluation`

```python
def run_code_evaluation(code_evaluator: Callable[..., Any], *, prompt: str, agent_message: str, flags: set[str] | None = None) -> dict[str, Any] | None
```

Run a programmatic evaluator.

Returns None if the evaluator opts out (returns None); otherwise the parsed
result dict. Raises ValueError for non-dict, non-None returns.

### `run_code_evaluations`

```python
def run_code_evaluations(code_evaluators: list[Callable[..., Any]], *, prompt: str, agent_message: str, flags: set[str] | None = None) -> list[dict[str, Any] | None]
```

Run all code evaluators sequentially.

Returns one entry per evaluator. None entries (opted-out evaluators) are
excluded from score averaging by callers.

### `run_evaluation`

```python
def run_evaluation(*, env_path: str | Path, evaluator_prompt: str, agent_message: str, system_prompt: str = '', user_prompt: str = '', model_override: str | tuple[str, ...] | None = None, log_callback: EvaluatorLogCallback | None = None) -> dict[str, Any]
```

Call the evaluator LLM once. Does not run the agent loop.
