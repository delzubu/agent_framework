---
title: Developer Documentation
layout: default
---

# Developer Documentation

Who this is for: contributors and advanced users looking for development-oriented docs.

## Areas

- Architecture.
- Runtime internals.
- Evaluation.
- Tracing.
- Drivers.
- Extension points.
- Callback and interaction routing.

## LLM Usage Accounting

Evaluator LLM accounting is trace-driven.

- Drivers normalize provider-specific usage at the model boundary into `input_tokens`, `input_cached_tokens`, `output_tokens`, `output_cached_tokens`, and `total_tokens`.
- `llm.response` events carry both normalized `usage` and provider-native `raw_usage`.
- `runtime.agent_finished` publishes `usage_self` and `usage_inclusive`.
- `runtime.session_finished` publishes `usage_session_totals`.
- Evaluator backend state aggregates from trace once, then reuses that summary for websocket results, API responses, CLI output, and the web UI.

Semantics:

- `self` means tokens spent by that specific agent run only.
- `inclusive` means the agent run plus all descendant sub-agent runs.
- `session` means the full run session and is available immediately after execution, independent of scoring.
- `output_cached_tokens` remains `0` until a provider exposes a real output-cache field.

## Model Validation

Structured model-output validation is extensible at runtime.

- Driver parsing still strictly enforces the one-JSON-object contract for structured modes.
- `AgentHost` owns a model validation chain so error rewriting and extra validation rules live outside `agent.py`.
- Exception validators can rewrite model-call failures into clearer messages while preserving strict failure behavior.
- Response validators can reject parsed `ModelResponse` objects before `AgentDecision` conversion.

The default exception validator improves the common `JSONDecodeError: Extra data` case into a clearer explanation: the model returned more than one JSON value in a single structured response, which usually means it emitted multiple decisions in one turn.

Runtime extension points:

- `host.register_model_exception_validator(...)`
- `host.register_model_response_validator(...)`

These validators are code-level extension points, not config-driven plugins.

## Next Steps

- [Architecture Overview]({{ '/reference/architecture/overview/' | relative_url }})
- [Handling Callbacks]({{ '/build/handling-callbacks/' | relative_url }})
- [Development Setup]({{ '/community/development-setup/' | relative_url }})
