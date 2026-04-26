---
title: Three Kinds of Agents
layout: default
---

# Three Kinds of Agents

Who this is for: developers choosing an agent architecture for a new task, or evaluating whether to refactor an existing one.

---

## The short answer

agent_framework supports three distinct execution patterns for agents:

| Kind | Control flow | Who decides what's next | When to use |
|---|---|---|---|
| **Standalone** | Reactive decision loop | The model, every turn | Open-ended tasks; emergent behavior |
| **Programmatic workflow** | Python-driven state machine | The developer, in code | Deterministic orchestration; fixed topology |
| **Planning** | Model-generated plan + batched execution | The model, up front then at each reflect | Structured multi-step tasks with data dependencies |

None of these is universally best. Each one makes a different tradeoff between flexibility and predictability, and between model-consumed tokens and developer-written code.

---

## Kind 1: Standalone agents

### What it is

A standalone agent runs the default reactive loop: call model → parse `AgentDecision` → execute (call tool, call sub-agent, invoke skill) → append result → repeat. The model decides what to do next at every turn, based on the full conversation history.

This is the default. No configuration is needed. Every agent in agent_framework is a standalone agent unless you add something.

### When to use it

Use a standalone agent when:
- The task is genuinely open-ended and you cannot predict the steps in advance.
- The number of required actions is small and the cost of letting the model decide each turn is acceptable.
- Emergent behavior is desirable — you want the model to explore solutions rather than follow a fixed plan.
- You are building a first prototype and want to validate the task is solvable before adding structure.

### Advantages

- **Minimum code.** An agent is a markdown file. No Python required.
- **Maximum flexibility.** The model can take any action it has tools for.
- **Easy to iterate.** Change the system prompt; the behavior changes.
- **No planning overhead.** Short tasks complete in two or three turns with no extra model calls.

### Disadvantages

- **Unpredictable execution path.** The model may choose different sequences of actions on equivalent inputs.
- **No parallel dispatch by design.** The model decides one action per turn, which is inherently sequential.
- **Context window pressure.** All tool results accumulate in the conversation. Long tasks hit the limit.
- **No native result reuse.** The model must re-read prior tool results from the conversation rather than having them wired by the runtime.

### Example use cases

- Answering questions that require a few lookups.
- Drafting documents with light research.
- Summarizing content.
- Routing and triage — decide where to hand off.
- Single-domain specialists in a larger multi-agent tree.

### Implementation

Write a markdown file with a system prompt and the relevant tools listed. No `planning:` block. No Python behavior class unless you need custom lifecycle hooks.

```markdown
---
id: my_agent
role: assistant
tools:
  - search
  - read_file
---

You are a research assistant. Answer questions by searching and reading relevant documents.
Use search first, then read the most relevant results, then answer.
```

---

## Kind 2: Programmatic workflow executors

### What it is

A programmatic workflow agent uses a Python `AgentBehavior` to build a `ProgrammaticWorkflow` — a code-defined state machine that routes to sub-agents based on structured conditions. The agent's LLM loop is bypassed entirely; the Python code controls which child agents run and in what order.

See [Programmatic Workflow Agents]({{ '/reference/programmatic-workflow-agents/' | relative_url }}) for the full API reference.

### When to use it

Use a programmatic workflow agent when:
- The top-level control flow is deterministic. You know at code-write time which agents will run and in what order (or which conditions determine the branching).
- The decision about "what runs next" depends on structured state — the value of a field, the presence of a result, a boolean flag — not on open-ended model reasoning.
- You need guaranteed topology. Routing must not drift based on model temperature or prompt variation.
- The parent should not spend LLM tokens on orchestration decisions. A model call to decide "should we run the intake agent?" is wasteful if you can express that in a three-line Python branch.

### Advantages

- **Deterministic.** The same input always produces the same execution path (assuming deterministic child agents).
- **Cheap orchestration.** No LLM call for the parent agent on each routing step.
- **Full parity with model-driven orchestration.** `Agent.execute_programmatic_workflow(...)` delegates back into the framework's standard sub-agent machinery — same audit events, same callbacks, same batch semantics.
- **Testable in isolation.** You can unit-test the workflow Python code without running LLM calls.

### Disadvantages

- **Requires Python.** You cannot express this in a markdown file alone; you need an `AgentBehavior` subclass.
- **Brittle when the topology needs to change.** Adding a new branch requires a code change and redeploy.
- **Not for exploratory control flow.** If the routing decision requires model reasoning ("this document is about X, therefore route to Y"), you are fighting the pattern.
- **Child agents still call LLMs.** Only the *orchestrator* is LLM-free; the child agents run their own decision loops.

### Example use cases

- Review pipelines with a fixed set of review axes (run all three axes in parallel, then consolidate).
- Approval chains — intake → specialist → manager, with deterministic escalation rules.
- Preprocessing gates — normalize input, then dispatch to the appropriate specialist.
- ETL workflows where the steps and their order are known.

### Implementation

Write a markdown file for the agent definition (minimal content, since the behavior class overrides the loop), then write a Python `AgentBehavior`:

```python
class ReviewPipelineBehavior(AgentBehavior):
    def before_run(self, agent, host, *, run, caller_id):
        workflow = ProgrammaticWorkflow(
            entry_step="intake",
            steps={
                "intake": WorkflowCallSubagentStep(
                    step_id="intake",
                    subagent_id="intake_agent",
                    parameters=lambda state: {"input": run.parameter_values["input"]},
                    next_step="review",
                ),
                "review": WorkflowCallSubagentsStep(
                    step_id="review",
                    calls=lambda state: (
                        SubagentCallSpec("reviewer_a", {"doc": run.parameter_values["input"]}, "review_a"),
                        SubagentCallSpec("reviewer_b", {"doc": run.parameter_values["input"]}, "review_b"),
                    ),
                    mode="parallel",
                    next_step="finish",
                ),
                "finish": WorkflowReturnStep(
                    step_id="finish",
                    value=lambda state: AgentResult(
                        status="completed",
                        message=str(state.require_step_result("review")),
                    ),
                ),
            },
        )
        result = agent.execute_programmatic_workflow(
            host=host, run=run, caller_id=caller_id, workflow=workflow,
        )
        return AgentHookDecision(final_result=result)
```

Register the behavior in an initializer and attach it to the agent.

---

## Kind 3: Planning agents

### What it is

A planning agent uses `PlanningTurnDriver` — enabled by a `planning:` block in the agent's frontmatter — to separate *plan generation* from *plan execution*. The model emits an explicit plan (a list of steps with data dependencies), the runtime executes ready batches in parallel, resolves `{{ref}}` token substitutions, and after each batch gives the model a *reflect* turn to evaluate results and decide whether to continue, revise, or finalize.

See [How Agents Plan]({{ '/learn/how-agents-plan/' | relative_url }}) for the conceptual background, and [How to Create a Planning Agent]({{ '/build/creating-planning-agents/' | relative_url }}) for the implementation guide.

### When to use it

Use a planning agent when:
- The task has structure — some steps depend on the results of others, and the model needs to declare those dependencies explicitly so the runtime can wire them.
- Some steps can run in parallel, and you want the runtime to dispatch them concurrently rather than waiting for each one in turn.
- The model may need to revise the plan mid-execution based on intermediate results — not just react to the last tool result, but reconsider the whole approach.
- The task is long enough that in-context accumulation of all results would pressure the context window.
- You want explicit, traceable plan artifacts — not just a chain of model turns.

### Advantages

- **Parallel execution** of independent steps without writing parallel dispatch code.
- **Data dependency wiring** via `{{step_id.field}}` tokens — the runtime substitutes values, not the model on each turn.
- **Mid-execution replanning** — a reflect step after each batch gives the model a structured moment to evaluate and revise.
- **Explicit plan artifact** — `PlanState` on `AgentRun` records the plan, step results, and revision history for tracing and debugging.
- **Safety caps** — `max_steps` and `max_plan_revisions` prevent runaway execution.

### Disadvantages

- **More LLM calls** than a programmatic workflow for the same task. Planning, reflect, and (on revision) replanning all cost model calls.
- **More prompt engineering** than a standalone agent. The planning phase and execution phase need separate prompt sections.
- **Plan quality depends on the model.** A weak model may produce malformed plans that fail DAG validation.
- **Not suitable for simple tasks.** Adding a planning layer to a two-step task is wasteful.

### Example use cases

- Game controller: plan player actions (look up player state, evaluate options, choose action, apply result) where later steps use earlier results.
- Research agents: search, then read top results, then synthesize — with parallel search and read steps.
- Code review pipelines: run multiple analysis checks in parallel, then evaluate all findings before emitting a verdict.
- Data collection and transformation: fetch from multiple sources in parallel, validate, merge, store.

### Implementation

Add a `planning:` block to the agent's frontmatter and write a prompt that describes both the planning contract and the step schema:

```yaml
planning:
  enabled: true
  parallel_execution: true
  max_steps: 30
  max_plan_revisions: 3
```

The system prompt should explain how to structure a plan and what decisions to make during the reflect phase. See [How to Create a Planning Agent]({{ '/build/creating-planning-agents/' | relative_url }}) for full prompt templates.

---

## Choosing between the three

The decision tree is short:

**Is the top-level control flow deterministic and code-expressible?**
→ Yes: use a **programmatic workflow executor**. Skip the model on orchestration.
→ No: continue.

**Does the task have structured steps with data dependencies between them, or could it benefit from parallel execution?**
→ Yes: use a **planning agent**.
→ No: use a **standalone agent**.

When in doubt, start with a standalone agent. If you find the model is spending turns figuring out what to do next when you could have told it, switch to a programmatic workflow. If you find the model is executing steps sequentially when many could run in parallel, or if intermediate results are driving the next set of actions in a structured way, switch to a planning agent.

### Mixing the three

The three kinds compose naturally in a multi-agent tree:

- A **programmatic workflow executor** can delegate to **standalone agents** and **planning agents** as child agents.
- A **planning agent**'s steps can invoke any sub-agent — standalone, workflow, or another planning agent.
- A **standalone agent** can use `call_subagents` to fan out to specialists, which is the lightweight version of planned parallel execution.

A common architecture is a programmatic workflow at the top (deterministic routing between phases), planning agents inside each phase (structured execution with data dependencies), and standalone specialist agents at the leaves (domain-specific reasoning).

---

## Next steps

- [How Agents Plan]({{ '/learn/how-agents-plan/' | relative_url }}) — deep dive into planning concepts and agent_framework's implementation
- [How to Create a Planning Agent]({{ '/build/creating-planning-agents/' | relative_url }}) — step-by-step guide
- [Programmatic Workflow Agents]({{ '/reference/programmatic-workflow-agents/' | relative_url }}) — full API reference for programmatic workflows
- [Multi-Agent Orchestration]({{ '/build/multi-agent-orchestration/' | relative_url }}) — sub-agent calls and batch dispatch
- [Authoring Agents]({{ '/build/authoring-agents/' | relative_url }}) — agent markdown format
