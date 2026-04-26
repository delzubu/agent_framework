---
title: How Agents Plan
layout: default
---

# How Agents Plan

Who this is for: developers already familiar with the agent decision loop who want to understand how planning layers on top of it, grounded in research from across the agentic AI landscape.

---

## From reactions to plans

A standard agent loop is reactive: the model sees the current conversation, emits a decision, the runtime executes it, and the result is appended to the conversation before the next turn. This works well for open-ended, emergent tasks where the model can figure out the next action from context. It breaks down when a task has structure that needs to be respected — a sequence of steps where later steps depend on earlier results, where some steps can run in parallel, and where an invalid intermediate result should trigger replanning rather than a confused next model call.

Planning is the discipline of making that structure explicit. A planning agent does not just react to its last tool result. It reasons about a sequence of actions *before* taking them, tracks which actions have completed and what they produced, and decides — after each batch — whether to continue executing the plan, revise it, or escalate.

---

## The foundational idea: Plan-and-Solve

The conceptual root of modern agent planning is surprisingly simple. A 2023 paper by Wang et al. showed that adding one sentence to a chain-of-thought prompt — *"Let's first understand the problem and devise a plan to solve the problem. Then, let's carry out the plan and solve the problem step by step."* — measurably improved model performance on complex tasks compared to vanilla chain-of-thought. The model stopped jumping to the first plausible action and instead decomposed the problem first.

This "plan first, then execute" split became the template for every major agent planning system that followed. What the different frameworks add on top is mostly about *how the plan is represented*, *how execution feeds back into the plan*, and *what triggers a revision*.

---

## What a plan looks like in practice

Across the research landscape — LangGraph, AutoGPT, CrewAI, Magentic-One, claude-code — plans converge on one of two representations:

**A list of steps** (most common). The model emits a numbered or bulleted list. Each step has an action and, optionally, a dependency on earlier steps. This is what LangGraph's Plan-and-Execute pattern uses (a `list[str]`), what BabyAGI maintains as its task queue, what CrewAI's `AgentPlanner` injects into task descriptions, and what claude-code's `TodoWriteTool` manages as a JSON array.

**Free-form text with a scratchpad**. AutoGPT's original format embedded the plan as a bullet list inside a `"thoughts"` JSON field. Every turn, the model rewrote the list from scratch. Simpler but less structured — useful when the model needs maximum flexibility.

Both are cheaper and more reliable than the alternative: a typed DAG with explicit edges. Tools like LLMCompiler and ReWOO tried declarative DAG plans (using syntax like `#E1` and `${1}` for cross-step references) and found that the parser fragility was a constant source of failures. The research consensus is: start with a list of steps, add structure only when you have a specific reason.

---

## Result reuse between steps

One of the most important design decisions in any planning system is how step N+1 gets the output of step N. Three approaches exist in the wild:

**In-context** (simplest): tool and subagent results stay in the conversation message log. The model can reference them in its next reasoning step implicitly. This is how claude-code, the OpenAI Agents SDK, and most single-agent systems work. Scales well for short-horizon plans where the context window holds everything.

**Symbolic substitution**: the planner writes references like `{{step_id.field}}` or `#E1` into later step parameters. The runtime substitutes actual values before dispatching each step. This is the approach used by ReWOO and LLMCompiler and the one agent_framework's planning feature adopts. It lets parallel steps be declared with clear data dependencies without requiring the model to manage substitution itself.

**Vector retrieval**: results are written to a vector database and retrieved by similarity for subsequent steps. AutoGPT and BabyAGI used this to handle long-horizon autonomous tasks where the context window would overflow. It is the most scalable option and the most fragile — retrieval can return the wrong result, and debugging is harder.

---

## Replanning: reactive vs structural

The most sophisticated planning systems do not execute a plan blindly to completion. They have a mechanism to detect when the plan is no longer valid and revise it. Two patterns:

**Model-driven replanning**: after each step or batch, the model is asked to evaluate progress and decide whether to continue or amend the plan. LangGraph's Plan-and-Execute replanner does this — the replanner sees the original objective, the original plan, and the completed steps so far, then emits either a new plan or a final response. This is reactive: replanning happens because the model decided it was needed.

**Structural replanning** (Magentic-One's key innovation): the runtime counts "no-progress" verdicts from a per-turn Progress Ledger. When the stall counter crosses a threshold, the outer loop forces a replan — updates the Facts sheet with new learnings, generates a revised plan that explicitly avoids prior mistakes, and resets the inner loop. The model never has to decide to replan; the runtime decides for it. This turns "agent never gives up" into "agent gives up gracefully after N flat turns."

The most robust designs combine both: model-driven by default (the model can choose to replan), with a structural fallback (the runtime forces replanning when no progress is detected).

---

## The reflection step

Reflexion (Shinn et al., 2023) introduced a pattern that is now standard in long-horizon agents: after a trajectory, the agent generates a textual self-reflection stored in a memory buffer. On the next attempt, the buffer is prepended to the prompt. The reflection prompt is deliberately compact:

> "You were unsuccessful in completing the task. Diagnose a possible reason for failure and devise a new, concise, high level plan that aims to mitigate the same failure."

This is the conceptual root of what modern frameworks call a *reflect* step — a model call after executing a batch of plan steps where the model decides whether the results look correct, whether to continue, and whether any revision is needed. The reflect step is the bridge between plan execution and replanning. Without it, the agent has no structured moment to evaluate its own progress.

---

## Two ledgers (Magentic-One)

The most field-tested planner architecture for open-ended tasks runs two nested loops with two separate data structures — a design called the "two-ledger" pattern:

**Task Ledger** (slow, expensive, updated on stall): contains the *facts* known about the task — given facts, facts to look up, facts to derive, and educated guesses — plus the *current plan*. Updated only when the inner loop stalls.

**Progress Ledger** (fast, per-turn, JSON): a small structured object evaluated after each agent turn with fields like `is_request_satisfied`, `is_in_loop`, `is_progress_being_made`, and `next_speaker`. The runtime branches on these fields directly — no free-text interpretation needed.

This split bounds the per-turn cost (the Progress Ledger is a cheap call) while keeping high-level revision possible when things go wrong (the Task Ledger is updated after a stall).

---

## Parallel execution

Most production agents execute plan steps sequentially. But many plans contain steps that are genuinely independent — steps whose `depends_on` list is empty, or whose dependencies are already complete. Executing them in parallel is a straightforward optimization.

The right primitive is a ready-batch: at each execution point, identify all plan steps whose `depends_on` are in the completed set, and dispatch them concurrently. This is simpler than a planning-time DAG — the parallelism emerges from the dependency structure at runtime, not from the model having to declare it in some graph syntax. The model declares *data dependencies* (step B needs `{{step_a.result}}`); the runtime derives the execution order.

---

## Callbacks during plan execution

Plan execution is not always linear. A step might trigger a callback — the agent inside a step needs information, approval, or escalation that the planning model cannot resolve on its own. Two fundamentally different callback types exist:

**Model-bound callbacks**: emitted by a sub-agent step; the planning model can resolve them by reasoning over available context, tools, or other sub-agents. Execution pauses until the planning model provides a resolution, then either continues the current step or aborts it and replans.

**User-bound callbacks**: require a human (or parent agent) to answer before execution can continue. These pause the plan and surface to the caller — the planning model cannot resolve them alone. The calling system must resume execution with the answer.

Mixing these up is a common source of bugs in planning systems: a callback that can only be answered by a human should not be routed to the planning model, and a callback the planning model can resolve should not interrupt the user.

---

## Prompt design for planning agents

Two prompt-design lessons recur across the research:

**Separate planning from execution in the prompt.** The planning instructions (what to analyze, how to decompose, what fields to include in a step) should be distinct from the execution instructions (how to call tools, how to write step results). Mixing them produces a model that does both poorly.

**Inject planning context per-turn, not in the static system prompt.** Magentic-One's orchestrator system message is empty — every actual planning instruction is assembled per call from a template that includes the current facts, current plan, and current conversation. This is cache-friendly (the static system prompt rarely changes, so it stays in the LLM provider's prompt cache) and keeps planning data close to the planning decision.

---

## How agent_framework implements planning

agent_framework adds planning as an opt-in capability on the existing agent runtime. The design follows the Plan-and-Execute + reflect pattern, with parallel batch dispatch and lenient `{{ref}}` resolution.

### Enabling planning

Planning is declared in the agent's frontmatter `planning:` block:

```yaml
planning:
  enabled: true
  parallel_execution: true
  max_steps: 50
  max_plan_revisions: 5
  reflect_after_each_batch: true
```

Or forced on/off at the call site via `planning_override`:

```python
result = await host.run_agent("my_agent", parameters=..., planning_override=True)
```

### The TurnDriver abstraction

The per-turn loop body — previously inlined in `Agent.run` — is extracted into a `TurnDriver` protocol. `StandardTurnDriver` preserves existing behavior exactly. `PlanningTurnDriver` replaces the single-step loop with a plan lifecycle:

1. **Plan phase**: model emits `submit_plan` with a list of `PlanStep` objects.
2. **Execute phase**: the driver identifies the ready batch (steps with all dependencies complete), dispatches them in parallel via `asyncio.gather`, resolves `{{ref}}` tokens before dispatch.
3. **Reflect phase**: the model evaluates the batch results and emits `continue_plan` (next batch), `amend_plan` (revise), or `final_message` (done).

This loop repeats until the plan is complete or a safety cap is hit.

### Plan steps and `{{ref}}` tokens

A plan step carries an `id`, a `kind` (matching the existing `AgentDecision` kinds — `call_tool`, `call_subagent`, `invoke_skill`, etc.), `parameters`, and a `depends_on` list:

```json
{
  "id": "get_player_info",
  "kind": "call_subagent",
  "parameters": {
    "subagent_id": "player_lookup",
    "player_id": "{{player_id}}"
  },
  "depends_on": []
}
```

Later steps reference earlier results with `{{step_id.field}}` tokens:

```json
{
  "id": "choose_action",
  "kind": "call_subagent",
  "parameters": {
    "subagent_id": "action_planner",
    "player_name": "{{get_player_info.name}}",
    "current_location": "{{get_player_info.location}}"
  },
  "depends_on": ["get_player_info"]
}
```

The resolver is lenient: missing references resolve to an empty string and emit a warning rather than crashing. This allows the model to adapt to missing fields gracefully.

### PlanState on AgentRun

`AgentRun` gains an optional `plan_state: PlanState | None` field, which is `None` for non-planning runs. `PlanState` tracks:

- `plan`: the current list of `PlanStep` objects.
- `step_results`: a dict mapping `step_id` to result payload.
- `completed_steps`: the set of step IDs that have finished.
- `plan_revision`: counter incremented on each `submit_plan` or `amend_plan`.

When `max_steps` or `max_plan_revisions` is exceeded, the driver emits a `callback(intent=execution_recovery)` rather than crashing.

### Logging

The planning subsystem logs under `agent_framework.planning.*`:
- **DEBUG**: entering/exiting each phase, `{{ref}}` resolution, batch construction.
- **INFO**: plan submitted, batch dispatched, reflect decision, plan revised.
- **WARNING**: missing `{{ref}}` resolved to empty string, step timed out.
- **ERROR**: plan validation failure, unrecoverable step error.

---

## Next steps

- [Three Kinds of Agents]({{ '/learn/three-kinds-of-agents/' | relative_url }}) — when to use planning vs simpler patterns
- [How to Create a Planning Agent]({{ '/build/creating-planning-agents/' | relative_url }}) — step-by-step implementation guide
- [Agent Runtime Patterns]({{ '/learn/agent-runtime-patterns/' | relative_url }}) — the foundational loop
- [Decision JSON Contract]({{ '/reference/decision-json-contract/' | relative_url }}) — full schema for plan decisions
