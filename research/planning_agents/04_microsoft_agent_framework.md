# Microsoft Agent Framework: Workflows, Magentic, Handoff, and the Semantic-Kernel/AutoGen Heritage

**Source:** Microsoft Agent Framework v1.0 GA (April 3, 2026) — convergence of Semantic Kernel + AutoGen. Docs at `learn.microsoft.com/en-us/agent-framework/`. Supplementary sources for predecessors: Semantic Kernel `concepts/planning`, AutoGen Magentic-One.

---

## Overview — how Microsoft thinks about planning today

Microsoft's current position is that **explicit planner classes are deprecated**; planning is what an LLM does *during* function calling. From the Semantic Kernel docs:

> "The Stepwise and Handlebars planners have been deprecated and removed from the Semantic Kernel package… We recommend using **function calling**, which is both more powerful and easier to use for most scenarios."
> — *Semantic Kernel concepts/planning*

For multi-agent and structurally constrained orchestration, the SK + AutoGen merger introduced **Workflows** — a graph-based execution engine that wraps agents (and arbitrary executors) into deterministic, repeatable processes. Workflows ship with five built-in orchestration patterns:

| Pattern | Purpose |
|---|---|
| **Sequential** | Agents run one after another in a defined order. |
| **Concurrent** | Agents run in parallel; results are gathered. |
| **Handoff** | Agents transfer control to one another via tool-call ("mesh topology, no central orchestrator"). |
| **Group Chat** | Agents collaborate in a shared conversation under a coordinator. |
| **Magentic** | A manager agent dynamically plans + replans + selects the next agent based on task progress (Magentic-One). |

All five share: typed `WorkflowEvent` streaming, `request_info` for HITL, `FileCheckpointStorage` for durable pause/resume, declarative YAML build files, and per-tool `approval_mode="always_require"` gates.

Microsoft's recommendation, paraphrased from the magentic doc:

> "If your scenario requires simpler coordination without complex planning, consider using the Group Chat pattern instead [of Magentic]."

The framework distinguishes two coordination archetypes:

- **Agent-as-Tool**: a primary agent delegates subtasks to other agents *as if they were tools*; the primary agent retains task ownership.
- **Handoff**: control is fully transferred; the receiving agent owns the task and full conversation context. Returning to the original agent requires another handoff.

This is the same distinction we already model in `agent_framework` between `call_subagent(s)` (agent-as-tool, parent owns) and a hypothetical "transfer" semantics (we don't have one, and based on Microsoft's design we probably don't need one for our non-conversational use cases).

---

## 1. Magentic Orchestration — the planner pattern that matters

Magentic is a direct port of the **Magentic-One** research system (Fourney et al., 2024). It is the framework's most sophisticated planning primitive and the one most relevant to our `player_controller` scenario.

### Architecture: outer loop + inner loop, with two ledgers

The Magentic Manager runs **two nested loops**:

- **Outer loop** maintains the **Task Ledger** — facts, guesses, and the current plan. The Task Ledger is what gets revised when the inner loop stalls.
- **Inner loop** maintains the **Progress Ledger** — turn-by-turn JSON tracking which agent should act next, whether progress is being made, and whether the team is in a loop.

### Decision flow inside the inner loop

After each agent turn, the Manager evaluates a **JSON-structured Progress Ledger** and decides:

1. Is the request satisfied? → if yes, exit to Final Answer step.
2. Is the team in a loop?
3. Is forward progress being made?
4. Who should speak next, and what instructions do they get?

If `stall_count` exceeds a threshold (configurable via `max_stall_count`), control returns to the **outer loop**, which:
- Updates the **Facts** sheet with new learnings.
- Generates a **revised plan** that explicitly avoids prior mistakes.
- Resets the inner loop with the new Task Ledger.

If `reset_count` exceeds `max_reset_count` (or `round_count` exceeds `max_round_count`), the workflow terminates and the Manager synthesizes a final answer from the conversation.

### Six prompt templates (from `autogen-agentchat/.../_magentic_one/_prompts.py`)

The standard Magentic Manager uses six prompts taken from the Magentic-One paper:

1. **Facts Survey** — categorizes information into *given facts* / *facts to look up* / *facts to derive* / *educated guesses*. Run once at task start.
2. **Plan** — generates a bullet-point strategy referencing the team composition and the facts sheet.
3. **Progress Ledger** — a JSON-schema-constrained call evaluating (a) request completion, (b) loop detection, (c) forward progress, (d) next-speaker selection with instructions. This is the per-turn decision call.
4. **Facts Update** — when stalled, regenerates the facts sheet with new learnings.
5. **Plan Revision** — analyzes the failure root cause and produces a new plan that avoids repeating mistakes.
6. **Final Answer** — synthesizes the user-facing answer from the gathered conversation.

The orchestrator system message (`ORCHESTRATOR_SYSTEM_MESSAGE`) is **empty** in the standard implementation — every prompt is constructed dynamically per call from these six templates plus the live conversation. (This is a deliberate cache-friendly choice mirroring claude-code's "inject reminders, don't bake into system prompt" pattern.)

### Python API

```python
from agent_framework.orchestrations import MagenticBuilder

workflow = MagenticBuilder(
    participants=[researcher_agent, coder_agent],
    intermediate_outputs=True,
    manager_agent=manager_agent,
    max_round_count=10,
    max_stall_count=3,
    max_reset_count=2,
).build()
```

You override the standard prompts by passing them to `MagenticBuilder`, or implement a fully custom manager by subclassing `MagenticManagerBase`.

### Human-in-the-loop plan review

`enable_plan_review=True` makes the Manager surface the proposed plan to the caller as a `request_info` event with `MagenticPlanReviewRequest` data. The user can `.approve()` or `.revise(feedback)`. A `revise` triggers a replan with the human feedback added to the next plan call.

```python
workflow = MagenticBuilder(
    participants=[...],
    enable_plan_review=True,
    manager_agent=manager_agent,
    max_round_count=10,
    max_stall_count=1,
    max_reset_count=2,
).build()
```

### Key design choices in Magentic

- **Two ledgers, two loops.** Splitting "what we know + what we plan" (slow, expensive Task Ledger) from "what to do next" (fast, JSON Progress Ledger) bounds the per-turn cost while keeping high-level revision possible.
- **Stall detection drives replanning.** Replanning isn't "model decides to replan" — it's "the runtime detects no-progress and forces a replan." The model never has to decide; the structural counter does.
- **Final-answer synthesis is a separate step.** The conversation log is summarized into a final user-facing answer by a dedicated prompt — agents don't directly produce the user's reply.
- **Empty system message.** All planning context is per-turn injection so the manager prompt cache doesn't thrash on plan/ledger updates.

---

## 2. Handoff Orchestration — mesh-of-agents, no orchestrator

Handoff is **mesh-topology, not graph-topology**: every agent can transfer control to any other agent (or a configured subset) via a tool call. There is no central orchestrator.

### How it works

- The runtime auto-injects a "handoff tool" on each agent based on configured handoff rules. When an agent calls that tool, control transfers to the named target agent.
- The full conversation history is broadcast to all participants whenever an agent generates a response. Handoff tool calls themselves are filtered out of the broadcasted history to avoid confusing the next agent.
- If an agent does **not** handoff and does not produce a terminal response, the workflow surfaces a `request_info` event asking the user for input — handoff is inherently interactive. (An optional `with_autonomous_mode()` injects a default "User did not respond, continue" message instead.)

### Triage agent prompt pattern

Microsoft's reference triage agent prompt:

> "You are frontline support triage. Route customer issues to the appropriate specialist agents based on the problem described."

And the .NET sample uses this even more aggressively:

> "You determine which agent to use based on the user's homework question. ALWAYS handoff to another agent."

### API

```python
from agent_framework.orchestrations import HandoffBuilder

workflow = (
    HandoffBuilder(
        name="customer_support_handoff",
        participants=[triage_agent, refund_agent, order_agent, return_agent],
        termination_condition=lambda conv: "welcome" in conv[-1].text.lower(),
    )
    .with_start_agent(triage_agent)
    .add_handoff(triage_agent, [order_agent, return_agent])
    .add_handoff(return_agent, [refund_agent])
    .build()
)
```

### Notable design choices

- **Handoff != agent-as-tool.** From the docs: *"In handoff orchestration, the agent receiving the handoff takes full ownership of the task. In agent-as-tools, the primary agent retains overall responsibility."* The two patterns coexist in the framework.
- **Tool-call-based handoff.** Handoff is implemented as a structured tool call, not a JSON envelope field — same model affordance as ordinary function calling.
- **Per-tool approval gates.** `@tool(approval_mode="always_require")` makes the runtime emit `function_approval_request` events that the workflow caller must `.to_function_approval_response(approved=...)` before execution proceeds. This is the reusable HITL primitive.
- **Checkpointing for durable approvals.** `FileCheckpointStorage` lets a workflow paused on an approval request resume hours/days later in a fresh process.

---

## 3. Sequential / Concurrent / Group Chat — the simpler primitives

- **Sequential**: pre-defined chain of agents. Output of agent N becomes input of agent N+1. No model planning involved.
- **Concurrent**: same input fanned to multiple agents in parallel; outputs collected. The framework ships built-in aggregation and you can add custom executors after the fan-in.
- **Group Chat**: multiple agents in one shared conversation, with a coordinator selecting next-speaker each turn. This is "Magentic without the planning overhead" — appropriate when the team composition handles the task without needing fact-tracking and replanning.

These are all *workflow* primitives, meaning they are nodes in the same `WorkflowBuilder` graph and can be composed: a Sequential workflow can have a Magentic node inside, etc. The graph engine itself supports branching, fan-out/fan-in, and converging back to a single output.

---

## 4. Semantic Kernel planner heritage (deprecated but instructive)

The two final-generation SK planners — both removed in 2024 — are worth noting for the prompt patterns they established:

- **HandlebarsPlanner**: emitted a Handlebars template that `each` over plugin functions. The template was executed deterministically by the SK runtime, not the LLM. This is the same pattern as ReWOO (LangGraph): one big planner call generates the entire executable script.
- **FunctionCallingStepwisePlanner**: ReAct-style. One step per LLM call; the planner sees prior step results in the prompt and decides the next function to call. This is what Microsoft now considers obsolete in favor of native function calling.

Microsoft's lesson — explicitly endorsed in the SK docs — is that **iterative function calling subsumes both patterns** as long as the model is good enough. Where it isn't (long-horizon, recoverable-from-stall tasks) you reach for Magentic, not for a planner class.

---

## 5. AutoGen heritage — Group Chat and Magentic-One

AutoGen v0.4 contributed:

- The **GroupChat** abstraction (now the Group Chat workflow pattern).
- **Magentic-One** itself, including the four reference workers (WebSurfer, FileSurfer, Coder, ComputerTerminal). Microsoft Agent Framework keeps the orchestration logic but lets you bring your own specialized agents.
- The `_prompts.py` file with the six Magentic-One templates above — preserved verbatim as the standard manager defaults.

---

## 6. System prompt design across the framework

Three patterns recur:

1. **Empty / minimal manager system message; per-turn template injection.** Magentic's standard manager has an empty `ORCHESTRATOR_SYSTEM_MESSAGE`; every actual instruction (Facts/Plan/Progress/Revision/Final) is a fresh prompt assembled per-turn. This is the same prompt-cache-friendly idiom claude-code uses for plan-mode reminders.
2. **JSON-schema constrained progress decisions.** The Progress Ledger is structured output: a JSON object with `is_request_satisfied`, `is_in_loop`, `is_progress_being_made`, `next_speaker`. There is no free-text "what should we do next" — the model fills a schema, and the runtime branches on the fields.
3. **Aggressive route-only triage prompts.** Handoff triage agents are instructed to *always* handoff (`"ALWAYS handoff to another agent."`), not to attempt the work themselves. The mode of operation is determined entirely by the agent's instruction string and the available handoff-tool set.

---

## Notable design decisions and trade-offs

- **Workflows, not graphs at the prompt level.** Microsoft puts the graph in code (`WorkflowBuilder`) rather than asking the model to emit a DAG. This avoids the LLMCompiler-style plan-parsing fragility, at the cost of less plan-level adaptability.
- **Replanning via structural counters, not model self-assessment.** Magentic's stall detection is a runtime counter incrementing on "no progress" verdicts from the Progress Ledger. The model never has to decide "should I replan?" — it only decides "are we making progress?".
- **HITL is a first-class request_info event, not a callback.** All HITL points (plan review, tool approval, handoff "ask user") emit a typed `request_info` event the caller can resume by sending a typed response. This is structurally similar to our `callback` decision but typed per use case.
- **Checkpointing is the durability story.** Long-running agents get `FileCheckpointStorage`; the workflow can pause for an approval and resume in a new process days later. We don't have this today — our `ConversationStore` only persists messages, not pending decisions.
- **Declarative YAML workflows.** Microsoft offers loading entire workflows from YAML (instructions, tools, memory, topology). This is a natural extension of our markdown-defined-agents idea — a markdown-defined *workflow* could be the next step beyond markdown-defined agents.

---

## Key takeaways for replication

1. **Adopt Magentic's two-ledger split.** A "Task Ledger" (facts + plan, revised on stall) and "Progress Ledger" (per-turn JSON: is-done? is-stalled? next-action?) is the most field-tested planner architecture for open-ended tasks. The Progress Ledger doubles as our `AgentDecision` envelope already — extend it with `is_request_satisfied`/`is_in_loop`/`is_progress_being_made` fields and the runtime can drive replanning structurally.

2. **Drive replanning from a structural stall counter, not from model self-assessment.** Magentic's `max_stall_count` + `max_reset_count` is a tiny amount of code that turns "agent never gives up" into "agent gives up and asks for help after N flat turns." Pair this with a `forward_progress: bool` field in our progress ledger.

3. **Keep the orchestrator system message minimal; inject per-turn templates.** Magentic's empty `ORCHESTRATOR_SYSTEM_MESSAGE` is the cache-friendly extreme. For our markdown-defined planner agent, the static body should be the *role* and *contract*; the per-turn `<facts>`, `<plan>`, `<progress_ledger>`, `<conversation>` blocks are user-message attachments.

4. **Use structured-output progress decisions, not free-text.** The Progress Ledger is `with_structured_output(ProgressLedgerSchema)`. We already enforce strict JSON for `AgentDecision`; extending it with a discriminated `replan` action carrying either a new plan or a continue-as-is choice fits our existing contract.

5. **Treat plan review as a first-class HITL event, not as ad-hoc callback wiring.** Magentic's `MagenticPlanReviewRequest` with explicit `.approve()` / `.revise(feedback)` responses is cleaner than a generic `callback` intent. Consider a `plan_review_request` callback intent specifically — it makes the player_controller's "ask the user to confirm before executing" pattern declarative.

6. **Distinguish agent-as-tool from handoff in the contract.** Microsoft is explicit that these are different patterns with different ownership semantics. We have `call_subagent(s)` (agent-as-tool); if we ever add a true handoff (where the parent context is replaced rather than merged), it should be a separate `kind`, not a flag on `call_subagent`.

7. **Tool approval as a structural primitive.** `@tool(approval_mode="always_require")` is a clean per-tool gate that emits a typed approval request the caller must respond to. Our built-in tools already do this via `host.user_comm.request_permission`, but lifting it to a tool-decoration concept would let agent authors mark *any* tool as approval-required without runtime changes.

8. **Checkpoint pending decisions, not just messages.** When an agent emits `callback` or hits a `request_info` point, snapshot the *full* decision-loop state (plan, past_steps, in-flight subagent batch, pending tool calls) keyed by `(conversation_id, step_id)`. This is what enables long-paused workflows to resume cleanly — currently we only persist `messages`.

---

## Sources

- [Microsoft Agent Framework — Workflow orchestrations index](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/)
- [Microsoft Agent Framework — Magentic orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic)
- [Microsoft Agent Framework — Handoff orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/handoff)
- [Microsoft Agent Framework v1.0 GA announcement](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/)
- [Semantic Kernel — Planning (planners deprecated, function-calling endorsed)](https://learn.microsoft.com/en-us/semantic-kernel/concepts/planning)
- [Magentic-One research blog](https://www.microsoft.com/en-us/research/blog/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/)
- [AutoGen Magentic-One prompt templates (`_prompts.py`)](https://github.com/microsoft/autogen/tree/main/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_magentic_one)
- [microsoft/agent-framework on GitHub](https://github.com/microsoft/agent-framework)
