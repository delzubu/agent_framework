# agent_framework — Workflow-Based Agent Development

Use this reference when building a controller agent that should orchestrate child agents, tools, deterministic transforms, or same-agent model phases from Python code.

---

## When to choose a workflow-based agent

Choose this pattern when the parent control flow is code-defined:

- intake first, then specialist reviews, then consolidation
- approval or escalation chains
- fixed state-machine transitions
- branching on structured state already known to Python
- semantic checkpoints that should run as same-agent model phases in one shared run context

Do not choose it just because the logic is complex. If the parent still needs open-ended LLM reasoning about what to do next, keep the normal model-driven decision loop or use a planning agent.

---

## Core pattern

First-class workflow agents use three layers:

1. Agent `.md` still defines the agent identity, parameters, allowed subagents, and normal prompt contract.
2. Adjacent `.json` sets `"agent_type": "workflow"` and points at a Python workflow module.
3. The workflow module exports `build_workflow(agent) -> ProgrammaticWorkflow`.

```json
{
  "agent_type": "workflow",
  "workflow": {"path": "my_agent_workflow.py"},
  "behaviors": ["guardrails"]
}
```

Existing behavior-based workflows that short-circuit from `before_run(...)` and call `agent.execute_programmatic_workflow(...)` remain supported as a compatibility path.

This is important: do not call `host.call_subagent(...)` directly as your main orchestration surface unless you deliberately accept losing native parent-side workflow parity.

The workflow runner exists so deterministic code can still produce the same kinds of parent artifacts as model-driven `call_subagent` / `call_subagents`.

---

## Public API

The public workflow surface is:

- `Agent.execute_programmatic_workflow(...)`
- `WorkflowAgent`
- `ProgrammaticWorkflow`
- `ProgrammaticWorkflowState`
- `WorkflowModelStep`
- `WorkflowHistoryProjection`
- `WorkflowHistoryEvent`
- `WorkflowTransformStep`
- `WorkflowCallToolStep`
- `WorkflowCallSubagentStep`
- `WorkflowCallSubagentsStep`
- `WorkflowBranchStep`
- `WorkflowReturnStep`
- `WorkflowRaiseStep`

Use normal `SubagentCallSpec` values for batch children.

---

## Minimal authoring template

```python
from agent_framework import (
    AgentBehavior,
    AgentHookDecision,
    AgentResult,
    ProgrammaticWorkflow,
    WorkflowCallSubagentStep,
    WorkflowReturnStep,
)


class MyWorkflowBehavior(AgentBehavior):
    def attach(self, agent):
        return None

    def before_run(self, agent, host, *, run, caller_id):
        workflow = ProgrammaticWorkflow(
            entry_step="delegate",
            steps={
                "delegate": WorkflowCallSubagentStep(
                    step_id="delegate",
                    subagent_id="child_agent",
                    parameters={"topic": run.parameter_values["topic"]},
                    next_step="finish",
                ),
                "finish": WorkflowReturnStep(
                    step_id="finish",
                    value=lambda state: AgentResult(
                        status="completed",
                        message=state.require_step_result("delegate").message,
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


def build_behavior() -> AgentBehavior:
    return MyWorkflowBehavior()
```

---

## Describing the workflow

The first iteration is Python-defined, not DSL-defined.

That means:

- step graph lives in `ProgrammaticWorkflow(steps={...})`
- transitions are step ids or callables returning step ids
- parameter mapping is direct Python data or a callable against `ProgrammaticWorkflowState`
- branch conditions are Python callables

This is deliberate. It keeps the first implementation strict and simple while leaving room for a future declarative layer.

---

## Step types

### `WorkflowCallSubagentStep`

Use for one child call.

Fields:

- `step_id`
- `subagent_id`
- `parameters`
- `next_step`

`subagent_id` and `parameters` may be either direct values or callables resolved against `ProgrammaticWorkflowState`.

### `WorkflowCallSubagentsStep`

Use for a native batch child step.

Fields:

- `step_id`
- `calls`
- `mode`
- `timeout_seconds`
- `next_step`

`calls` must resolve to `tuple[SubagentCallSpec, ...]`.

### `WorkflowBranchStep`

Use for deterministic branching.

Fields:

- `step_id`
- `condition`
- `then_step`
- `else_step`

### `WorkflowReturnStep`

Use to finish the workflow.

Supported values:

- `AgentResult`
- `str`
- `None`

**Output contract (mandatory).** The framework routes the returned `AgentResult` to the caller exactly like any other agent result. That means `message` and `response` carry the same semantics as a model-driven `final_message` decision:

- `message` — **human-readable prose only**. Never serialize a dict, list, or any structured data into this field. Callers and the evaluator treat it as a displayable string.
- `response` — structured output as a JSON-serializable dict. Use this for any typed payload the caller needs to extract programmatically.
- Both fields may be set together: prose summary in `message`, full payload in `response`.

Violating this contract breaks callers that read `message` as prose and evaluators that extract `response` fields.

```python
# correct
WorkflowReturnStep(
    step_id="finish",
    value=lambda state: AgentResult(
        status="completed",
        message="Analysis complete.",          # prose
        response={"score": state.require_step_result("score_step").response},
    ),
)

# wrong — never do this
WorkflowReturnStep(
    step_id="finish",
    value=lambda state: AgentResult(
        status="completed",
        message=json.dumps(state.require_step_result("score_step").response),  # JSON in message
    ),
)
```

When `value` is a plain `str`, it becomes `message` directly — the same prose-only rule applies.

### `WorkflowRaiseStep`

Use to abort with a specific exception or error message.

### `WorkflowModelStep`

Use when a workflow phase should make a local LLM call inside the same workflow
agent context. Model phases default to chat-history semantics:

- the workflow system prompt is stable
- the initial rendered user prompt is appended to history once
- phase prompts are appended as `user` messages
- final phase results are projected as compact `assistant` messages
- deterministic transform/tool/subagent/batch/callback/skill results are
  appended after the existing conversation history rather than reinserted into
  an early `<augmentations>` block
- runtime decision JSON stays available for parsing/tracing, but is not the
  default LLM-visible history artifact
- `<workflow_state_summary>` is not included unless
  `include_state_summary=True`

Phase prompts are durable by default. Use
`prompt_history_policy="ephemeral"` when later phases should see the compact
semantic result projection, but not the completed phase's full prompt. The
active phase prompt remains visible during the phase call, then is removed from
LLM-visible history after the phase result projection is appended; transcripts
and audit logs still retain it. `prompt_history_policy="none"` records the
prompt in the transcript without adding it to LLM-visible history.

If `prompt_fragment` is omitted, the phase prompt is selected from the
workflow agent markdown's second section by `phase_id`.

```markdown
---
id: controller
role: controller
parameters:
  instruction:
    description: instruction
    required: true
---
<workflow_system>
Shared workflow-level system instruction.
</workflow_system>
<intake>
Ask only necessary clarification questions.
</intake>
<review>
Review using the prior chat history.
</review>
---
<instruction>{{instruction}}</instruction>
```

Then:

```python
WorkflowModelStep(
    step_id="run_intake",
    phase_id="intake",
    allowed_decision_kinds=frozenset({"final_message", "request_user_input"}),
)
```

Use `PromptRef("agent:<agent_id>#workflow")` when a phase should reuse a
workflow projection of a standalone agent prompt instead of copying prompt
text into the workflow agent:

```python
WorkflowModelStep(
    step_id="review_audience",
    phase_id="audience_review",
    prompt_fragment=PromptRef("agent:axis_audience#workflow"),
    prompt_history_policy="ephemeral",
)
```

The referenced agent sidecar must define `workflow-compose`. Include/exclude
sections use normalized markdown heading paths, and shorthand heading titles
are allowed only when unambiguous:

```json
{
  "workflow-compose": {
    "pre-load-skills": ["presentation-strategist"],
    "include-sections": ["/Agent/Role", "/Agent/Rubric", "/Agent/Output Contract"],
    "exclude-sections": ["/Agent/Memory Access"],
    "append": "Use <deck_json> already present in conversation history."
  }
}
```

Use `WorkflowHistoryProjection` when the semantic result should come from
`response`, both `message` and `response`, or a custom callable:

```python
WorkflowModelStep(
    step_id="review_audience",
    phase_id="audience_review",
    prompt_history_policy="ephemeral",
    history_projection=WorkflowHistoryProjection(
        final_message="response",
        wrapper_tag="audience_review",
    ),
)
```

---

## Using workflow state

`ProgrammaticWorkflowState` gives you:

- `initial_parameters`
- `step_results`
- `last_step_id`
- `last_value`

Use `state.require_step_result("step_id")` when later steps depend on earlier outputs.

Example:

```python
WorkflowReturnStep(
    step_id="finish",
    value=lambda state: AgentResult(
        status="completed",
        message=str(state.require_step_result("review_axes")),
    ),
)
```

---

## What runtime parity you get

Programmatic workflow steps still reuse framework-owned subagent orchestration, so the parent run still gets:

- `runtime.audit.named_event` entries for `subagent_call`, `subagent_result`, `subagent_batch_started`, `subagent_batch_finished`
- parent hook history for single-child calls
- transcript/prompt fragments such as `<subagent_call>`, `<subagent_result>`, `<subagent_results>`
- native callback routing and blocked batch resume behavior

That is the main reason to use the workflow runner instead of hand-assembling host calls and trace events yourself.

---

## Recommended design rules

- Keep the parent deterministic. Put reasoning inside child agents.
- Keep parameter mapping explicit and near the step.
- Keep workflow state small and typed by convention.
- Prefer one controller behavior per controller agent.
- Let the `.md` file still declare `subagents:` accurately. The workflow runner does not bypass allowlists.

---

## Current limits

The first iteration does not include:

- declarative `$step` / `$param` references
- explicit workflow-level `on_callback`, `on_error`, or retry policy
- loop step types
- external persisted workflow DSL files

If you need those, note the gap explicitly rather than inventing a private mini-engine in application code.
