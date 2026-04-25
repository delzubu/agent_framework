---
title: Programmatic Workflow Agents
layout: default
---

# Programmatic Workflow Agents

Who this is for: developers building deterministic controller agents that should orchestrate child agents without spending a parent LLM turn on each routing step.

## When to use this pattern

Use a programmatic workflow agent when the top-level control flow is deterministic and code-driven:

- intake first, then specialist reviewers, then consolidation
- approval or escalation chains
- fixed state-machine transitions
- workflows whose next step depends on structured state, not open-ended reasoning

Do not use this pattern just because Python is available. If the parent agent still needs genuine model reasoning about what to do next, keep the normal decision loop.

## Core API

The public workflow surface is agent-owned:

- `Agent.execute_programmatic_workflow(...)`
- `ProgrammaticWorkflow`
- `ProgrammaticWorkflowState`
- `WorkflowCallSubagentStep`
- `WorkflowCallSubagentsStep`
- `WorkflowBranchStep`
- `WorkflowReturnStep`
- `WorkflowRaiseStep`

The important design choice is parity with native orchestration. Programmatic steps delegate back into framework-owned subagent execution, so the parent run still emits the same kinds of artifacts as model-driven `call_subagent` / `call_subagents`.

## What parity means in practice

When a programmatic workflow step delegates to children, the parent run still gets:

- `runtime.audit.named_event` records such as `subagent_call`, `subagent_result`, `subagent_batch_started`, and `subagent_batch_finished`
- parent hook history like `before_subagent:*` and `after_subagent:*`
- transcript and prompt fragments such as `<subagent_call>`, `<subagent_result>`, and `<subagent_results>`
- native callback routing and batch resume semantics through the existing host machinery

That is the reason to use `Agent.execute_programmatic_workflow(...)` instead of calling `host.call_subagent(...)` directly from behavior code.

## Basic pattern

The supported first-iteration pattern is:

1. Attach a Python `AgentBehavior`.
2. In `before_run(...)`, inspect `run.parameter_values`.
3. Build a `ProgrammaticWorkflow`.
4. Call `agent.execute_programmatic_workflow(...)`.
5. Return `AgentHookDecision(final_result=...)` so the parent skips the normal LLM loop.

Example:

```python
from agent_framework import (
    AgentBehavior,
    AgentHookDecision,
    AgentResult,
    ProgrammaticWorkflow,
    SubagentCallSpec,
    WorkflowBranchStep,
    WorkflowCallSubagentStep,
    WorkflowCallSubagentsStep,
    WorkflowReturnStep,
)


class DeckReviewWorkflowBehavior(AgentBehavior):
    def attach(self, agent):
        return None

    def before_run(self, agent, host, *, run, caller_id):
        workflow = ProgrammaticWorkflow(
            entry_step="maybe_intake",
            steps={
                "maybe_intake": WorkflowBranchStep(
                    step_id="maybe_intake",
                    condition=lambda state: bool(run.parameter_values.get("intake_complete")),
                    then_step="review_axes",
                    else_step="run_intake",
                ),
                "run_intake": WorkflowCallSubagentStep(
                    step_id="run_intake",
                    subagent_id="deck_review_intake",
                    parameters=lambda state: {
                        "deck": run.parameter_values["deck"],
                        "intake": run.parameter_values.get("intake", ""),
                    },
                    next_step="review_axes",
                ),
                "review_axes": WorkflowCallSubagentsStep(
                    step_id="review_axes",
                    calls=lambda state: (
                        SubagentCallSpec("axis_audience", {"deck": run.parameter_values["deck"]}, "audience"),
                        SubagentCallSpec("axis_design", {"deck": run.parameter_values["deck"]}, "design"),
                    ),
                    mode="parallel",
                    next_step="finish",
                ),
                "finish": WorkflowReturnStep(
                    step_id="finish",
                    value=lambda state: AgentResult(
                        status="completed",
                        message=str(state.require_step_result("review_axes")),
                    ),
                ),
            },
        )
        result = agent.execute_programmatic_workflow(
            host=host,
            run=run,
            caller_id=caller_id,
            workflow=workflow,
        )
        return AgentHookDecision(final_result=result)
```

## Step model

### `WorkflowCallSubagentStep`

Use for one child call.

- `subagent_id`: direct string or callable resolved against `ProgrammaticWorkflowState`
- `parameters`: direct dict or callable resolved against `ProgrammaticWorkflowState`
- `next_step`: next step id or callable returning one

### `WorkflowCallSubagentsStep`

Use for a native batch call.

- `calls`: `tuple[SubagentCallSpec, ...]` or callable resolving to one
- `mode`: `"parallel"` or `"sequential"`
- `timeout_seconds`: optional wall-clock timeout
- `next_step`: next step id or callable returning one

### `WorkflowBranchStep`

Use for deterministic branching.

- `condition`: callable returning truthy/falsey
- `then_step`: next step when true
- `else_step`: next step when false

### `WorkflowReturnStep`

Use to finish the workflow.

Allowed return values:

- `AgentResult`
- `str`
- `None`

Strings and `None` are normalized into `AgentResult(status="completed", ...)`.

### `WorkflowRaiseStep`

Use to fail fast with a specific exception or message.

## Using workflow state

`ProgrammaticWorkflowState` stores:

- `initial_parameters`: a snapshot of the starting run parameters
- `step_results`: outputs from prior workflow steps
- `last_step_id`
- `last_value`

Use `state.require_step_result("step_id")` when later steps need earlier outputs.

## Recommended design rules

- Keep routing deterministic. If a step needs model reasoning, let the child agent do it.
- Keep parameter mapping explicit and local to the step.
- Prefer branch callables over hidden prompt conventions.
- Use the workflow runner for orchestration, not for generic business logic unrelated to agent flow.
- Let child agents own their own prompts and decisions. The workflow agent should route, not impersonate them.

## Current limits

The first iteration is intentionally small:

- no declarative `$step` / `$param` mapping language yet
- no explicit workflow-level `on_callback`, `on_error`, or retry policy model yet
- no loops as first-class step types yet
- no persistence or external workflow DSL format yet

The supported pattern today is deterministic Python orchestration with native runtime parity, not a full workflow engine.

## Related docs

- [Developer Documentation]({{ '/reference/developer-documentation/' | relative_url }})
- [Architecture Overview]({{ '/reference/architecture/overview/' | relative_url }})
