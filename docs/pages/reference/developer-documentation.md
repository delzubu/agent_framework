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

## Programmatic Workflow Execution

The framework now supports deterministic, code-driven orchestration without entering the parent agent's LLM decision loop.

The public surface is agent-owned:

- `Agent.execute_programmatic_workflow(...)`
- `ProgrammaticWorkflow`
- `WorkflowCallSubagentStep`
- `WorkflowCallSubagentsStep`
- `WorkflowBranchStep`
- `WorkflowReturnStep`
- `WorkflowRaiseStep`

The important design point is parity with native orchestration.  Programmatic workflow steps reuse the same parent-owned subagent path as `call_subagent` and `call_subagents`, so they still produce:

- parent-side `runtime.audit.named_event` records such as `subagent_call`, `subagent_result`, `subagent_batch_started`, and `subagent_batch_finished`
- parent hook history around single-child calls
- transcript updates like `<subagent_call>`, `<subagent_result>`, and `<subagent_results>`
- the same callback routing and batch resume behavior already implemented in `host.call_subagent(...)` and `host.call_subagent_batch(...)`

The first iteration is intentionally small and extensible:

- branching is Python-driven, using callables against `ProgrammaticWorkflowState`
- workflow return values are normalized into `AgentResult`
- step outputs are stored in `ProgrammaticWorkflowState.step_results`
- there is no separate expression language or persistence format yet

This makes `AgentBehavior.before_run(...)` a supported place to run deterministic controller logic while still preserving the framework's native trace and callback semantics.

For the full developer guide, examples, and authoring guidance, see [Programmatic Workflow Agents]({{ '/reference/programmatic-workflow-agents/' | relative_url }}).

## Evaluator Agent Model Overrides

`agent_framework_evaluator` now supports run-scoped model overrides for the agent under test. This is separate from `DEFAULT_EVAL_MODEL`, which still controls the evaluator/scoring LLM.

Two scopes are supported:

- `root_only` — only the tested/top-level agent uses the selected override model
- `all_agents` — every agent invoked during that run uses the selected override model

The important runtime detail is where the override is applied:

- `root_only` is applied to the root invocation clone in `AgentHost.run_agent(...)`, so cached agent definitions are not mutated for later runs
- `all_agents` is applied at agent-load time through the host/registry path, so it supersedes `.env` `DEFAULT_MODEL`, `.env` `AGENT_MODELS`, and adjacent runtime `.json` `model` declarations for that host instance

Evaluator surfaces:

- Web UI: model dropdown populated from `.env` `DEFAULT_MODEL`, left empty by default
- CLI: `--agent-model-override` and `--agent-model-override-scope {root_only,all_agents}`
- Initializers: `DEFAULT_AGENT_MODEL_OVERRIDE` / `get_default_agent_model_override()` and `DEFAULT_AGENT_MODEL_OVERRIDE_SCOPE` / `get_default_agent_model_override_scope()`

## Next Steps

- [Architecture Overview]({{ '/reference/architecture/overview/' | relative_url }})
- [Programmatic Workflow Agents]({{ '/reference/programmatic-workflow-agents/' | relative_url }})
- [Handling Callbacks]({{ '/build/handling-callbacks/' | relative_url }})
- [Development Setup]({{ '/community/development-setup/' | relative_url }})
