---
title: Programmatic Workflow Agents
layout: default
---

# Programmatic Workflow Agents

Who this is for: developers building controller agents that need code-defined
workflow control, native tool/subagent parity, and optional same-agent model
phases.

## When to use this pattern

Use a workflow agent when the top-level control flow is code-driven:

- intake first, then specialist reviewers, then consolidation
- approval or escalation chains
- fixed state-machine transitions
- workflows whose next step depends on structured state
- same-agent semantic checkpoints that must share one run context

If the task is open-ended and does not need deterministic workflow control, use
a normal model agent. If the model should generate and revise a plan, use a
planning agent.

## First-class workflow agents

Workflow agents can be loaded as their own runtime type instead of being
started by a `before_run(...)` behavior. The workflow graph is defined in a
Python sidecar module. Markdown remains the agent identity, prompt, parameter,
tool, subagent, and skill contract.

The adjacent JSON sidecar selects the runtime explicitly:

```json
{
  "agent_type": "workflow",
  "workflow": {
    "path": "action_resolver_workflow.py"
  },
  "behaviors": ["guardrails"]
}
```

Rules:

- omitting `agent_type`, or setting it to `"model"`, loads the normal model-loop agent
- `"workflow"` loads a `WorkflowAgent`
- `workflow.path` resolves relative to the agent Markdown file
- the workflow module must export `build_workflow(agent) -> ProgrammaticWorkflow`
- existing `behavior` and `behaviors` keys are unchanged and attach to workflow agents normally
- filenames do not imply runtime type

## Core API

The public workflow surface is:

- `WorkflowAgent`
- `Agent.execute_programmatic_workflow(...)`
- `ProgrammaticWorkflow`
- `ProgrammaticWorkflowState`
- `WorkflowModelStep`
- `WorkflowTransformStep`
- `WorkflowCallToolStep`
- `WorkflowCallSubagentStep`
- `WorkflowCallSubagentsStep`
- `WorkflowBranchStep`
- `WorkflowReturnStep`
- `WorkflowRaiseStep`

`Agent.execute_programmatic_workflow(...)` remains for compatibility with older
behavior-based workflows. New workflow agents should prefer the sidecar
`agent_type: "workflow"` path.

## Basic pattern

Example workflow module:

```python
from agent_framework import (
    AgentResult,
    ProgrammaticWorkflow,
    SubagentCallSpec,
    WorkflowBranchStep,
    WorkflowCallSubagentStep,
    WorkflowCallSubagentsStep,
    WorkflowModelStep,
    WorkflowReturnStep,
)


def build_workflow(agent):
    return ProgrammaticWorkflow(
        entry_step="validity_check",
        steps={
            "validity_check": WorkflowModelStep(
                step_id="validity_check",
                phase_id="validity_check",
                prompt_fragment="Decide whether the routed action is executable.",
                allowed_decision_kinds=frozenset({"final_message", "call_tool"}),
                next_step="review_axes",
            ),
            "review_axes": WorkflowCallSubagentsStep(
                step_id="review_axes",
                calls=lambda state: (
                    SubagentCallSpec("rules_reviewer", state.initial_parameters, "rules"),
                    SubagentCallSpec("world_reviewer", state.initial_parameters, "world"),
                ),
                mode="parallel",
                next_step="finish",
            ),
            "finish": WorkflowReturnStep(
                step_id="finish",
                value=lambda state: AgentResult(
                    status="completed",
                    message="Workflow complete.",
                    response={
                        "validity": state.require_step_result("validity_check").response,
                    },
                ),
            ),
        },
    )
```

## Step model

### `WorkflowModelStep`

Runs a phase-scoped mini model loop in the workflow agent's current `AgentRun`.

- `phase_id`: stable phase identifier for tracing and context
- `prompt_fragment`: optional direct prompt text, `PromptRef`, or callable resolved against state
- `allowed_decision_kinds`: optional set of allowed decisions for the phase
- `final_response_schema`: optional JSON Schema dict for `final_message.response`
- `max_turns`: safety cap for the phase loop
- `include_state_summary`: opt-in legacy context dump; defaults to `False`
- `prompt_fragment_mode`: where to append the phase prompt; defaults to `conversation_only`
- `prompt_history_policy`: phase prompt lifecycle; `durable` by default, or `ephemeral` to remove it from LLM-visible context after phase completion
- `history_projection`: callable or `WorkflowHistoryProjection` for compact chat history
- `next_step`: next step id or callable returning one

A phase-local `final_message` completes the phase and stores an `AgentResult`
under `state.step_results[step_id]`. It does not complete the workflow agent;
use `WorkflowReturnStep` for that.

By default, model phases behave like a normal shared chat:

- the workflow system prompt is stable across phase calls
- the initial rendered user prompt is appended to chat history once
- each phase prompt is appended as a `user` conversation message
- phase results are appended as compact semantic history, not full runtime
  decision envelopes
- deterministic workflow outputs and action-loop results from transform steps,
  tools, subagents, callbacks, and skills are appended after the existing chat
  history instead of being injected through `<augmentations>`
- `<workflow_state_summary>` is not sent to the model unless explicitly enabled

Set `prompt_history_policy="ephemeral"` when later phases need the semantic
phase result but not the completed phase's full instructions. The active phase
prompt remains visible during its model call; after the phase result projection
is appended, the completed phase prompt is removed from `conversation_messages`
and `prompt_fragments`. Transcript and audit records still retain the prompt
for debugging. The default `durable` policy preserves the older append-only
chat history. `none` records the prompt in the transcript without adding it to
LLM-visible history.

When `prompt_fragment` is omitted, the phase prompt is loaded from a matching
XML tag in the agent markdown system section. For example,
`phase_id="audience_review"` reads `<audience_review>...</audience_review>`.

```markdown
---
id: deck_reviewer
role: Deck reviewer
parameters:
  instruction:
    description: instruction
    required: true
---
<workflow_system>
You are the shared reviewer runtime for this workflow.
</workflow_system>
<audience_review>
Review the deck for audience alignment.
</audience_review>
---
<instruction>{{instruction}}</instruction>
```

When XML prompt partitioning is used, `<workflow_system>` is the only shared
system block. Other top-level tags are phase prompts selected by `phase_id`.

`prompt_fragment` can also reference a workflow projection of another agent's
prompt without copying the source prompt text:

```python
from agent_framework import PromptRef, WorkflowModelStep

WorkflowModelStep(
    step_id="audience",
    phase_id="audience",
    prompt_fragment=PromptRef("agent:axis_audience#workflow"),
    prompt_history_policy="ephemeral",
)
```

The referenced agent sidecar controls the projection with `workflow-compose`:

```json
{
  "workflow-compose": {
    "pre-load-skills": ["presentation-strategist"],
    "include-sections": ["/Agent/Role", "/Agent/Rubric", "/Agent/Output Contract"],
    "exclude-sections": ["/Agent/Memory Access"],
    "append": "In workflow mode, use <deck_json> already present in conversation history."
  }
}
```

Markdown headings are matched by normalized heading path. If
`include-sections` is present, only those section subtrees are included;
`exclude-sections` is applied afterward. Shorthand titles are allowed only when
they match exactly one heading; ambiguous shorthand fails with the candidate
paths. Prompt-reference resolution emits audit metadata with the source agent,
projection, included and excluded sections, preloaded skills, and a token
estimate.

### `WorkflowTransformStep`

Runs deterministic Python transformation or validation.

- `transform`: callable resolved against `ProgrammaticWorkflowState`
- `next_step`: next step id or callable returning one

Raised exceptions fail the workflow run.

### Tool and subagent steps

`WorkflowCallToolStep`, `WorkflowCallSubagentStep`, and
`WorkflowCallSubagentsStep` use the same framework-owned execution helpers as
model-driven tool and subagent decisions. That preserves hooks, callback
routing, memory normalization, transcript updates, trace events, and audit
events.

### Branch, return, and raise

`WorkflowBranchStep` chooses the next step from Python state.
`WorkflowReturnStep` completes the workflow agent with an `AgentResult`, string,
or `None`. `WorkflowRaiseStep` fails fast with an exception or message.

## Shared context and memory

`ProgrammaticWorkflowState` stores:

- `initial_parameters`
- `step_results`
- `context_entries`
- `last_step_id`
- `last_value`

All `WorkflowModelStep` phases in one workflow agent share the same `AgentRun`:
conversation messages, prompt fragments, memory projections, tool/subagent
results, callbacks, and phase outputs remain visible to later phases.

Persistent `ConversationStore` behavior is unchanged; workflow phases share
in-run conversation memory.

## Observability and extension

Workflow agents use the same behavior attachment schema as model agents.
Lifecycle hooks fire for workflow agents and for nested model/tool/subagent,
skill, and callback work inside phases.

Workflow execution emits workflow-level trace/audit events:

- `workflow.step_started`
- `workflow.step_completed`
- `workflow.phase_started`
- `workflow.phase_completed`
- `workflow.phase_failed`

Nested events include `workflow_step_id` and, for model phases, `phase_id`.

## Current limits

The first-class workflow runtime does not include:

- Markdown, YAML, or JSON workflow graph parsing
- durable workflow persistence across process restarts
- automatic semantic compression beyond explicit history projection and prompt lifecycle policies
- GUI workflow authoring
- a full BPMN/statechart engine

Workflow structure is intentionally Python-defined for now.

## Related docs

- [Developer Documentation]({{ '/reference/developer-documentation/' | relative_url }})
- [Architecture Overview]({{ '/reference/architecture/overview/' | relative_url }})
