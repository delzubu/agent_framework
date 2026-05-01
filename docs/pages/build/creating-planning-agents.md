---
title: Creating a Planning Agent
layout: default
---

# Creating a Planning Agent

Who this is for: developers who want to implement a planning agent using agent_framework's planning feature. You should be comfortable writing agent markdown files and understand the basic decision loop.

Prerequisites:
- [Authoring Agents]({{ '/build/authoring-agents/' | relative_url }})
- [How Agents Plan]({{ '/learn/how-agents-plan/' | relative_url }}) — conceptual background

---

## Overview

A planning agent separates *what to do* from *how to do it*. Instead of deciding one action at a time, the model first emits an explicit plan — a list of steps with data dependencies — and the runtime executes those steps (in parallel where possible), resolves cross-step references, and gives the model a reflect turn after each batch. The model can continue executing, revise the plan, or finalize.

You enable planning with a `planning:` block in the agent's frontmatter. Everything else is driven by the system and user prompt you write.

---

## Step 1: Add the `planning:` block

In the agent's YAML frontmatter, add a `planning` section:

```yaml
---
id: my_planning_agent
role: planning_agent
planning:
  enabled: true
  parallel_execution: true
  max_steps: 30
  max_plan_revisions: 3
  reflect_after_each_batch: true
  step_timeout_seconds: 120
---
```

All keys have defaults; only `enabled: true` is required to activate planning mode.

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Activates `PlanningTurnDriver` for this agent. |
| `parallel_execution` | `true` | Dispatch all ready steps concurrently via `asyncio.gather`. |
| `max_steps` | `50` | Total step executions before triggering `execution_recovery` callback. |
| `max_plan_revisions` | `5` | Maximum `submit_plan` / `amend_plan` calls before triggering `execution_recovery`. |
| `reflect_after_each_batch` | `true` | Give the model a reflect turn after every batch, not just at end. |
| `step_timeout_seconds` | `300` | Per-step wall-clock timeout. |

You can also override planning at the call site without changing the frontmatter:

```python
result = await host.run_agent(
    "my_planning_agent",
    parameters={"task": "..."},
    planning_override=True,   # or False to disable
)
```

---

## Step 2: Write the planning-phase prompt

The system prompt has two jobs: explain the planning contract, and explain the execution contract. Keep them separate. The planning section tells the model what a valid plan looks like; the execution section tells the model what to do during reflect turns.

### Planning contract

The model must emit a `submit_plan` decision when it is ready to begin. A plan is a JSON array of step objects. Each step has:

- `id`: unique string identifier used in `depends_on` and `{{ref}}` tokens.
- `kind`: the type of action — `call_tool`, `call_subagent`, or `invoke_skill`.
- `parameters`: action-specific arguments, which may include `{{ref}}` substitution tokens.
- `depends_on`: list of step IDs whose results must be available before this step runs.

Example system prompt section:

```markdown
## Planning

When you have analyzed the task, emit a plan using the `submit_plan` decision.
A plan is a list of steps. Each step is an object with these fields:

- `id`: a short, unique identifier (snake_case)
- `kind`: the action kind — `call_tool`, `call_subagent`, or `invoke_skill`
- `tool_name` (for `call_tool`): the tool to invoke
- `subagent_id` (for `call_subagent`): the child agent to delegate to
- `parameters`: action-specific arguments; use `{{step_id.field}}` to reference a prior step's result
- `depends_on`: list of step IDs that must complete before this step runs

Steps whose `depends_on` list is empty or whose dependencies are all complete
will be dispatched in parallel. Order steps to maximize parallelism.

Example plan:
```json
{
  "kind": "submit_plan",
  "message": "Fetch data and analyze it.",
  "plan": [
    {
      "id": "fetch_data",
      "kind": "call_tool",
      "tool_name": "fetch",
      "parameters": {"url": "{{source_url}}"},
      "depends_on": []
    },
    {
      "id": "analyze",
      "kind": "call_subagent",
      "subagent_id": "data_analyzer",
      "parameters": {"data": "{{fetch_data.content}}"},
      "depends_on": ["fetch_data"]
    }
  ]
}
```
```

### Reflect contract

After each batch of steps completes, the model receives the results and must choose one of:

- `continue_plan` — acknowledge results and proceed to the next ready batch.
- `submit_plan` — revise the remaining steps based on what was learned (replan).
- `final_message` — all objectives are met; emit the final result.

Add this section to the system prompt:

```markdown
## After each batch

After executing a batch of steps, you will receive the results. You must then emit one of:

- `continue_plan` — if the results are satisfactory and there are more steps to execute
- `submit_plan` — if the results reveal that the remaining plan is wrong or insufficient;
  include a revised `plan` array with only the remaining (not yet completed) steps
- `final_message` — if all objectives are met; include the final result

Do not emit `final_message` before all required steps are complete.
Do not emit `submit_plan` (replan) unless the results genuinely require it — unnecessary replanning wastes tokens.
```

---

## Step 3: Write the user prompt template

The user prompt template is rendered with the invocation parameters when the agent starts. For a planning agent, this should clearly state the objective and provide any input data the model needs to produce a good plan.

```markdown
---
user_prompt_template: |
  ## Objective
  {{task}}

  ## Available inputs
  {{#if player_id}}Player ID: {{player_id}}{{/if}}
  {{#if source_url}}Source URL: {{source_url}}{{/if}}

  Analyze the objective, then produce a plan. Execute the plan step by step,
  reflecting after each batch. Emit the final result when all objectives are met.
---
```

If your parameters are complex, consider passing them as structured JSON and having the model parse them in the first turn:

```markdown
## Task
```json
{{task_json}}
```
```

---

## Step 4: Define step parameters and `{{ref}}` tokens

`{{ref}}` tokens in step parameters are resolved by the runtime before each step runs. The resolver supports:

- `{{parameter_name}}` — invocation parameter (top-level).
- `{{step_id.field}}` — dot-path into a prior step's result object.
- `{{step_id.nested.field}}` — nested dot-path traversal.

The resolver is **lenient**: a missing reference resolves to an empty string and emits a `WARNING` log. The step still runs. This allows the model to handle partial results gracefully.

When a step parameter is *entirely* a `{{ref}}` token and the referenced value is non-string (e.g., a number, boolean, or object), the runtime preserves the original type. Embedded tokens are always stringified.

### Example: chaining subagent results

```json
{
  "id": "get_player_state",
  "kind": "call_subagent",
  "subagent_id": "player_lookup",
  "parameters": {"player_id": "{{player_id}}"},
  "depends_on": []
},
{
  "id": "evaluate_options",
  "kind": "call_subagent",
  "subagent_id": "option_evaluator",
  "parameters": {
    "player_name": "{{get_player_state.name}}",
    "current_location": "{{get_player_state.location}}",
    "inventory": "{{get_player_state.inventory}}"
  },
  "depends_on": ["get_player_state"]
}
```

### Example: parallel independent steps

Steps with no unmet dependencies run in parallel:

```json
[
  {
    "id": "search_wiki",
    "kind": "call_tool",
    "tool_name": "search",
    "parameters": {"query": "{{topic}}"},
    "depends_on": []
  },
  {
    "id": "search_news",
    "kind": "call_tool",
    "tool_name": "search_news",
    "parameters": {"query": "{{topic}}"},
    "depends_on": []
  },
  {
    "id": "synthesize",
    "kind": "call_subagent",
    "subagent_id": "synthesizer",
    "parameters": {
      "wiki_results": "{{search_wiki.results}}",
      "news_results": "{{search_news.results}}"
    },
    "depends_on": ["search_wiki", "search_news"]
  }
]
```

`search_wiki` and `search_news` start immediately and run in parallel. `synthesize` waits for both.

---

## Step 5: Handle callbacks during plan execution

Plan steps can emit callbacks. The planning driver routes them based on type:

**Model-bound callbacks** (`kind: callback` with intents like `information_request`, `proposal_review`, `execution_recovery`): The planning model is asked to resolve them. Execution pauses until the model provides a resolution, then either continues the step (with the resolution appended to context) or marks the step failed and triggers a reflect turn for replanning.

**User-bound callbacks** (`kind: callback_to_caller`, `request_user_input`, `request_resolution`): The plan pauses and the callback is surfaced to the caller. The caller must resume execution with the answer.

In your agent prompt, tell sub-agents how to escalate when they cannot proceed:

```markdown
## Sub-agent callback policy

If a step sub-agent cannot proceed without information, it should emit:
- `callback_to_caller` if the planning model may have context to resolve it
- `request_user_input` if only the user can answer

Do not block on unresolvable state; emit a callback and let the planner decide.
```

---

## Step 6: Test the agent

### Validate the plan schema

The runtime validates every `submit_plan` decision. A plan that fails DAG validation (cycle, forward reference, unknown dependency) raises a `ValueError` before any steps run. Enable DEBUG logging to see the raw plan before validation:

```
PYTHONPATH=src python -m agent_framework --agent my_planning_agent \
  --instruction "..." --llm-trace console
```

### Check `{{ref}}` resolution

Look for `WARNING` log lines from `agent_framework.planning.resolver`. Each warning tells you which token was unresolved and which step it was in. If a reference is unexpectedly empty, either the prior step failed silently or the field name is wrong.

### Inspect PlanState in traces

The evaluator UI shows `plan_state` on each `AgentRun` in the trace panel. You can see the full plan, which steps completed, their results, and how many revisions occurred. Use this to debug plans that stall or revise unexpectedly.

### Use the evaluator for regression testing

Write evaluation cases that verify:
- The plan was submitted (check `plan_state.plan` is non-empty).
- Key steps completed (check `plan_state.completed_steps`).
- The final result matches expected output.
- No unexpected revisions occurred (check `plan_state.plan_revision` count).

```python
# In an evaluator case:
result_field: plan_state.completed_steps
expected_contains: ["get_player_state", "evaluate_options", "apply_action"]
```

---

## Output contract for `final_message`

Planning agents return results to their callers via `final_message`. There are two forms:

**Text result** — a prose answer the caller reads as a string:
```json
{"kind": "final_message", "message": "Research complete. The answer is X."}
```

**Structured result** — a typed JSON payload the caller or evaluator consumes programmatically:
```json
{
  "kind": "final_message",
  "message": "",
  "response": {
    "status": "ready",
    "items": [...]
  }
}
```

Use `"response"` (a JSON object) whenever downstream code needs to read specific fields from the result. **Do not use `"parameters"` on `final_message`** — `parameters` is reserved for `call_tool`, `call_subagent`, and `callback` decisions. Setting it on `final_message` raises a `ValueError` at runtime.

Your system prompt's Output Shape section should show the exact JSON the model must emit, using `"response"` for structured output:

```markdown
## Output Shape

When all plan steps are complete, emit:

```json
{
  "kind": "final_message",
  "message": "",
  "response": {
    "status": "ready | blocked",
    "results": ["..."],
    "reasoning": ["..."]
  }
}
```
```

---

## Common pitfalls

**Model emits `call_tool` instead of `submit_plan` on the first turn.** The prompt did not make the planning contract clear. Add explicit instruction: "Your first decision must be `submit_plan` with a complete plan. Do not call tools directly."

**Steps with `depends_on` populated but references not used.** A step that declares `depends_on: ["step_a"]` but does not reference `{{step_a.*}}` in its parameters is not wrong, but it may signal that the dependency is unnecessary — remove it to unlock parallelism.

**Plan revision loop.** If the model keeps emitting `submit_plan` (replan) without making progress, check whether the sub-agents are producing results in the format the model expects. The `max_plan_revisions` cap prevents infinite revision loops.

**`final_message` too early.** If the model emits `final_message` before all steps complete, the reflect prompt is not enforcing the "all steps complete" check. Add the check explicitly to the prompt.

**Step timeout hit on large parallel batches.** Lower `max_plan_revisions` or increase `step_timeout_seconds`. For large batches, consider whether all steps truly need to run before the reflect turn, or whether you can split them into smaller sequential batches.

---

## Full example: research agent

A complete planning agent that searches two sources in parallel, reads the top result, and synthesizes an answer:

**`agents/research_agent.md`**:

```markdown
---
id: research_agent
role: planning_agent
planning:
  enabled: true
  parallel_execution: true
  max_steps: 20
  max_plan_revisions: 2
tools:
  - search
  - read_url
---

You are a research assistant. Given a research question, plan and execute a search
across multiple sources, read the most relevant results, and synthesize an answer.

## Planning contract

Submit a plan as your first decision using `submit_plan`. Structure each step as:
- `id`: snake_case identifier
- `kind`: `call_tool`
- `parameters`: tool arguments, may include `{{step_id.field}}` references
- `depends_on`: step IDs whose results you need

Maximize parallelism: steps that do not depend on each other should run at the same time.

## After each batch

After steps complete, emit:
- `continue_plan` to proceed to the next batch
- `submit_plan` with a revised `plan` array if results change what needs to happen
- `final_message` with the synthesized answer when the research is complete

## Plan

A typical plan for this task:
1. Search multiple sources in parallel (no dependencies)
2. Read the top result from each search (depends on search steps)
3. Synthesize an answer (depends on all read steps)
```

**User prompt template**:

```
Research question: {{question}}

Plan and execute a thorough search. Read at least two sources before synthesizing.
```

---

## Next steps

- [How Agents Plan]({{ '/learn/how-agents-plan/' | relative_url }}) — conceptual background
- [Three Kinds of Agents]({{ '/learn/three-kinds-of-agents/' | relative_url }}) — when to use planning vs other patterns
- [Decision JSON Contract]({{ '/reference/decision-json-contract/' | relative_url }}) — full schema for `submit_plan`, `continue_plan`, `final_message`
- [Evaluation and Debugging]({{ '/build/evaluation-and-debugging/' | relative_url }}) — evaluating planning agents
- [Tracing and Observability]({{ '/build/tracing-and-observability/' | relative_url }}) — reading PlanState in traces
