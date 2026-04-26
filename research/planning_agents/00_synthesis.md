# Planning Agents in `agent_framework` — Research Synthesis & Design Report

**Audience:** maintainers of `agent_framework` (markdown-defined Python agent runtime; strict `AgentDecision` JSON contract; `call_subagents` parallel/sequential batch already exists; conversation store, MCP, skills, commands, callback intents already exist).

**Goal:** specify how to give agents the ability to **plan, execute, replan, and reuse step results** — including parallel/sequential sub-agent dispatch, tool/skill use, and human-in-the-loop interruption — driven from agent markdown, with no silent JSON repair.

**Companion reports** (read in order if you want depth):

- [01_claude_code.md](01_claude_code.md) — Anthropic CLI: TodoWriteTool + Plan Mode + AgentTool
- [02_nano_claude_code.md](02_nano_claude_code.md) — persistent Task store with `blocks`/`blocked_by`, threaded sub-agents, inline-vs-fork skills
- [03_langgraph.md](03_langgraph.md) — Plan-and-Execute, ReWOO, LLMCompiler, Send API
- [04_microsoft_agent_framework.md](04_microsoft_agent_framework.md) — Magentic two-ledger pattern, Handoff, Workflow primitives
- [05_other_frameworks.md](05_other_frameworks.md) — OpenAI Agents SDK, CrewAI, AutoGPT, ReAct/Plan-and-Solve/Reflexion/ToT, Anthropic guide, OpenHands

The reference target is `agent-adventure/agents/player_controller.md`, which already specifies the desired behavior in prose. This synthesis turns that into a concrete framework design.

---

## Part 1 — What we already have, mapped to the design space

| Capability | Current state | Closest external analogue |
|---|---|---|
| Decision contract | `AgentDecision` (closed `kind` set, strict JSON) | LangGraph Pydantic `Act`/`JoinOutputs` |
| Sub-agent invocation (single) | `call_subagent` | OpenAI Agents agent-as-tool |
| Sub-agent batch (parallel/sequential) | `call_subagents` w/ `mode`, `output_key`, `timeout` | LangGraph `Send`, MS AF Concurrent workflow |
| Tools | `Tool` registry + permission-gated builtins | claude-code `runTools` |
| Skills | Markdown skill registry + `invoke_skill` decision | claude-code SkillTool, nano-claude `skill/` |
| MCP | `McpManager` + bridged tools | universal |
| Callbacks (HITL & escalation) | `callback`, `request_user_input`, `request_resolution` | Magentic `request_info`, LangGraph `interrupt` |
| Conversation store | `InMemoryConversationStore` (messages only) | LangGraph checkpointer (full state) |
| Audit / tracing | `InMemoryAuditTracer`, JSONL logs | universal |
| Models (per-agent override) | `AGENT_MODELS=` env | LangGraph: separate planner/executor LLMs |

**Gaps** (relative to the planning-agent feature set):

1. **No plan as first-class state.** Plans only exist as messages — there's no way for the runtime to inspect the live plan, persist it, or surface it for HITL review.
2. **No structural replanning trigger.** The agent loop has no concept of "stalled" or "step N completed, re-evaluate." Replanning, if it happens, is purely model-driven.
3. **No symbolic step-result references.** When the model writes `"actor_id": "{{step_1.actor_id}}"` (per the `player_controller.md` spec), there is no resolver — the literal `{{step_1.actor_id}}` would be sent to the tool.
4. **No structured step output keys.** Sub-agent results in a `call_subagents` batch land under `output_key`s in the conversation, but per-tool-call results don't have any name — only the conversation order links them to later use.
5. **No checkpoint-and-resume of in-flight decisions.** A `callback` halts the loop but the *plan in progress* is reconstructable only from the message log; there's no sidecar with `pending_steps`, `completed_steps`, `step_results`.
6. **No "plan review" HITL primitive.** Callbacks are intent-tagged but not typed by use case.

Everything else needed for planning agents is already in place, including the parts that are surprisingly hard in other frameworks (parallel sub-agent dispatch, structured decision JSON, skill catalog injection, file-reference expansion).

---

## Part 2 — The recurring patterns from the research

Distilled from all five reports:

### A. Plan representation

| Pattern | Used by | Pros | Cons |
|---|---|---|---|
| **List-of-steps as conversation tool input** | claude-code TodoWrite, AutoGPT `plan` field, Plan-and-Solve | Trivial for model to emit; survives model re-reads; no parser | No structured deps, no step IDs, no references |
| **List-of-steps as side-store dict** | nano-claude `task/`, Magentic Task Ledger | Survives compaction; queryable by tool; `blocks`/`blocked_by` edges | Extra round-trip to consult |
| **Markdown plan file on disk** | claude-code Plan Mode, Devin, Magentic checkpoint | Cross-session, fork-safe, human-readable | Free-form; runtime can't reason over it |
| **Typed Pydantic plan** | LangGraph Plan-and-Execute, MS AF (via structured output) | Validatable, fail-loud, refactor-safe | Requires structured-output binding; less flexible |
| **Streamed DAG with deps** | LLMCompiler | Maximal parallelism; planner+exec overlap | Parser-fragile; complex |

**The clear winner for our codebase is "Typed list-of-steps that **is** the agent decision JSON,"** because we already enforce typed JSON and strictly validate. We'd extend `AgentDecision` rather than adding a parallel plan store.

### B. Plan execution

Three viable models, ordered from simplest to most powerful:

1. **Implicit:** the plan is just "what the model decides to do next." No runtime bookkeeping. (Anthropic's "autonomous agent" pattern.)
2. **Plan-as-tool-state, model-driven execution:** the plan lives in TodoWrite/Task list; the model picks the next step each turn. (claude-code, nano-claude, BabyAGI.)
3. **Plan-as-state, runtime-driven execution:** the runtime owns the plan dict, dequeues `plan[0]` each turn into the executor prompt. (LangGraph Plan-and-Execute.)

Our framework should support **(2) by default** with an optional escalation to **(3)** for the player_controller-style agents that explicitly want to plan-then-execute.

### C. Re-planning trigger

| Trigger | Pattern source |
|---|---|
| **Model decides** ("I should rewrite my todo list now") | claude-code, nano-claude |
| **Per-step replanner call** ("after every step, ask the model: continue or revise?") | LangGraph Plan-and-Execute |
| **Stall counter** ("after N flat turns, force a Task Ledger revision") | Magentic, Reflexion |
| **Callback resolution** ("a sub-agent escalated; given the new info, re-evaluate") | implicit in our `callback` already |
| **Human edit** ("user revised the plan; re-execute from there") | Magentic plan-review, LangGraph time-travel |

All five are useful. The framework should make at least the first three accessible from agent markdown.

### D. Result storage and reuse

| Mechanism | Pattern source | When to use |
|---|---|---|
| **In-context (message log)** | claude-code, nano-claude | Default; works until context fills |
| **Symbolic substitution** (`{{step_1.field}}`, `${1}`, `#E1`) | ReWOO, LLMCompiler, our `player_controller.md` | When one planner call drives many tool calls |
| **`output_key` dict** in batch result | our `call_subagents`, LangGraph reducers | Parallel sub-agents whose results converge |
| **Side store keyed by step ID** | LLMCompiler observations dict, Magentic ledgers | Long-horizon plans |
| **Vector retrieval** | AutoGPT, BabyAGI | Long-horizon autonomous loops |

Our framework should adopt **in-context + symbolic substitution + `output_key` dict** as the "starter set," and only consider vector retrieval for long-running agents.

### E. Parallel / sequential dispatch

The two viable primitives are:
- **Batch dispatch from one decision** (we already have this with `call_subagents`).
- **Per-tool concurrency-safety flag, runtime-batched** (claude-code's `isConcurrencySafe()`).

The first is more declarative; the second is more opportunistic. For planning agents, batch-from-decision is the right primary primitive — the planner *knows* which steps are independent.

### F. Prompt design — three idioms that all winning systems share

1. **Empty/minimal system prompt; per-turn template injection** (Magentic, claude-code Plan Mode, LangGraph node prompts). Cache-friendly; locality of intent.
2. **Structured-output for "what next" decisions** (Pydantic union, JSON schema). Eliminates "agent decided to stop but didn't say so" and "agent invented a plan kind."
3. **Explicit verification / criticism / progress evaluation step** (Magentic Progress Ledger, Reflexion, AutoGPT `criticism`). Prevents long-horizon drift.

---

## Part 3 — Proposed design for `agent_framework`

The design extends the existing `AgentDecision` contract and `host` orchestration without breaking anything. It is layered: **L1** is "no new feature, just a prompt template"; **L2** adds first-class plan state; **L3** adds structural replanning. Each layer is independently useful.

### L1 — `system.plan_execute.md` system prompt template (zero code changes)

Add a fourth response-mode template alongside `system.decision.md` / `system.text.md` / `system.json_object.md`:

**`agents/system.plan_execute.md`** — instructs the agent to:

1. **First turn:** emit `kind: "final_message"` with `parameters.plan: [step, step, ...]`. Plan steps are themselves `AgentDecision`-shaped objects with optional `id` and `parameters` containing literal values *or* `{{step_id.path}}` substitution tokens.
2. **Subsequent turns:** look at the plan in the conversation, pick the next step whose dependencies are satisfied, and emit it directly as a decision. Reuse `{{step_id.path}}` resolution against prior step results in the message log.
3. **After each step:** evaluate progress. If the result invalidates the plan, emit a new plan as another `final_message` — **OR** continue with the existing plan.
4. **Termination:** when all plan steps are complete, emit the agreed final-output schema (e.g., `routed_intents`, `status`).

This is the pure-prompt version — works today with a competent model, no code changes, no broken contracts. **It's what `player_controller.md` is already trying to do.** The only wrinkle is `{{step_id.path}}`: without runtime resolution the model has to substitute values itself when emitting the next step. This works but is wasteful (the model re-parses prior tool results to extract values).

**This should be the first deliverable.** It validates the design against real player_controller traffic before touching code.

### L2 — First-class plan state and `{{step_id.path}}` resolver

Add three things:

#### 2a. `plan` and `step_results` as host-managed decision-loop state

Promote them out of the conversation message log:

```python
@dataclass
class PlanState:
    plan: list[AgentDecision] = field(default_factory=list)        # live plan, overwritable
    completed_steps: list[CompletedStep] = field(default_factory=list)  # accumulator (audit log)
    step_results: dict[str, Any] = field(default_factory=dict)    # step_id -> result payload
    plan_revision: int = 0                                        # incremented on each replan

@dataclass(frozen=True)
class CompletedStep:
    step_id: str
    decision: AgentDecision
    result: Any           # tool result, sub-agent message, etc.
    started_at: float
    finished_at: float
    revision_at_start: int
```

This mirrors LangGraph's `plan: list[str]` (overwritable) + `past_steps: Annotated[list, operator.add]` (accumulator) split. The reducer-style append is the canonical "audit log accumulates, live plan overwrites" idiom.

`PlanState` lives on `AgentInvocation` (per-call) and is accessible to behaviors. It's serialized into the conversation store alongside messages so callbacks can resume the loop with the plan intact.

#### 2b. New `AgentDecision.kind = "submit_plan"`

Don't reuse `final_message` — keep the contract crisp:

```python
{
    "kind": "submit_plan",
    "plan": [
        {
            "id": "step_1",
            "kind": "call_tool",
            "tool_name": "get_state_slice",
            "parameters": {"actor_id": "{{player_actor_id}}"}
        },
        {
            "id": "step_2",
            "kind": "call_subagent",
            "subagent_id": "player_intent_parser",
            "parameters": {
                "actor_id": "{{player_actor_id}}",
                "state_slice": "{{step_1}}"
            },
            "depends_on": ["step_1"]
        },
        {
            "id": "step_3",
            "kind": "call_subagent",
            "subagent_id": "rules_router",
            "parameters": {"intents": "{{step_2.declared_intents}}"},
            "depends_on": ["step_2"]
        }
    ],
    "reasoning": "..."
}
```

Validation rules (strict, no silent repair — per CLAUDE.md):

- Each step is a valid `AgentDecision` for `kind ∈ {call_tool, call_subagent, invoke_skill, callback}`. Nested `submit_plan` is forbidden.
- `id` strings are unique within the plan and match `[a-zA-Z][a-zA-Z0-9_]*`.
- `depends_on` references resolve to step IDs earlier in the list (no cycles, topologically sortable).
- `{{token}}` references in `parameters` resolve to either an invocation parameter or a `depends_on`-declared step ID. Unknown references → **`ValueError`** at decision parse time.
- `submit_plan` decisions can only be the first decision of a turn or follow `replan_signal` (see L3).

Add a parallel `kind = "amend_plan"` for edits-not-rewrites: append/insert/cancel by step ID, no full replacement.

#### 2c. `{{step_id.path}}` resolver

A new `step_reference.py` module modeled on `file_reference.py`:

```python
def resolve_step_references(
    value: Any,
    invocation_parameters: dict[str, Any],
    step_results: dict[str, Any],
) -> Any:
    """Recursively resolve {{token}} substitutions in JSON-shaped values.

    Tokens:
        {{param_name}}                  -> invocation parameter value
        {{step_id}}                     -> full step result (any JSON value)
        {{step_id.dot.separated.path}}  -> dot-path lookup into step result
    Unknown tokens raise ValueError.
    """
```

Resolution happens **immediately before** dispatching each `plan` step — i.e., the runtime walks `step.parameters`, substitutes refs, and only then calls the tool/sub-agent. The model never sees resolved values; it always emits symbolic refs. This is the ReWOO/LLMCompiler trick that saves the planner from re-prompting.

Like `file_reference.py`, the resolver is pluggable on `host.step_ref_resolver` so projects can override semantics (e.g., extract one field from a complex sub-agent result).

#### 2d. New driver: `PlanExecutor` behavior

A subclass of `AgentBehavior` that:

1. Detects `submit_plan` and stores the plan in `PlanState.plan` (also appending a stable summary to the message log so the model can see what it committed to).
2. Each subsequent turn, **before calling the model**, the executor checks if the next step is "trivially executable" (all deps satisfied, no model judgment needed). If so, it executes the step directly without a model call, stores the result under `step_id`, and loops. If the step requires model judgment (e.g. `kind: "callback"`), it hands control back to the model with the resolved step injected as a synthetic user message.
3. After every executed step, asks the model to either continue, replan (`amend_plan` or `submit_plan`), or finalize (`final_message`).

This is the LangGraph Plan-and-Execute loop, but reusing our existing decision JSON instead of a separate Pydantic graph. The behavior is opt-in: agents declare `behavior: plan_executor` in frontmatter, identical to how response modes are selected today.

### L3 — Structural replanning and progress ledger

Borrow Magentic's two-ledger split for agents that need open-ended exploration (most of `agent-adventure` does *not* need this; `player_controller` is more a one-shot planner). Make it opt-in via frontmatter:

```yaml
---
id: my_open_ended_agent
behavior: plan_executor
plan_executor:
  progress_ledger: true        # adds per-step progress check
  max_stall_count: 3           # consecutive no-progress steps before replan
  max_replan_count: 2          # before escalating to user
  emit_plan_review: true       # surface plan review to caller (HITL)
---
```

Progress ledger turns into a **per-step structured-output subprompt** that the runtime appends after each result, asking the model:

```json
{
  "is_request_satisfied": false,
  "is_in_loop": false,
  "is_progress_being_made": true,
  "next_action": "continue" | "amend_plan" | "submit_plan" | "callback"
}
```

Stall counter increments when `is_progress_being_made: false`. On `>= max_stall_count`, the runtime forces a `submit_plan` call (Magentic-style "outer loop" replan). On `>= max_replan_count` it escalates as a `callback` to the caller with intent `execution_recovery`.

`emit_plan_review: true` adds a HITL gate: after `submit_plan` the runtime emits a callback (intent `proposal_review`) with the plan; the caller can `.approve()` or `.revise(feedback)` and the loop continues. This is Magentic's `MagenticPlanReviewRequest` ported to our callback model.

### L4 — Persistence (small but high-value)

Extend the conversation store so that a snapshot **includes `PlanState`**, not just messages. Fields:

```python
class ConversationSnapshot:
    messages: tuple[ChatMessage, ...]
    plan_state: PlanState | None
    pending_callback: CallbackPayload | None
```

Effects:

- A `callback` that pauses for human input can be resumed cleanly: the resumed loop sees the same `plan` and `step_results` as before. (Currently you'd have to reconstruct from messages, which is fragile if compaction kicked in.)
- Plans survive process restarts.
- Audit traces can correlate plan revisions to causes (sub-agent return, callback resolution, stall trigger).

This is small (one dataclass, two store methods, behavior-aware serialization) and pays for itself the first time a long-running player_controller turn needs to resume after a user clarification.

---

## Part 4 — System prompt design

Two new system-prompt templates, both following the **Magentic/claude-code idiom** of static role + per-turn template injection.

### `agents/system.plan_execute.md` (the body)

Static body (identical across turns, prompt-cache-friendly):

```markdown
You are a planning agent. You decompose the user's request into a sequence of
discrete steps and execute them, reusing intermediate results.

# Decision schema

You always emit one of:
- `submit_plan`   — produce or replace the full plan (list of step decisions)
- `amend_plan`    — append/insert/cancel steps by ID without full replacement
- `call_tool` / `call_subagent` / `call_subagents` / `invoke_skill` — execute one step
- `callback`      — escalate when a step's result invalidates the plan AND you cannot
                    proceed without input
- `final_message` — produce the final agreed output

# Plan steps

Each plan step is itself a decision (`call_tool`, `call_subagent`, `invoke_skill`,
or `callback`). Each step must have a unique `id` matching `[a-zA-Z][a-zA-Z0-9_]*`.
Steps with `depends_on: [other_id]` execute strictly after their dependencies.
Steps without `depends_on` are independent and may execute in parallel.

# Step parameter references

In any step's `parameters`, you may use:
  {{invocation_param_name}}     — to reference an invocation parameter
  {{step_id}}                   — to reference the full result of an earlier step
  {{step_id.field.subfield}}    — to dot-path into the result

Do not pre-resolve references — emit them symbolically and the runtime will
substitute at execute time. Unknown references will raise.

# Workflow (each turn)

1. If you have not yet submitted a plan, do so now (`submit_plan`).
2. Otherwise, look at the live plan and the most recent step result.
   - If the result invalidates the plan, emit `submit_plan` (full replace) or
     `amend_plan` (targeted edit).
   - If a callback was just resolved, validate the answer against your other
     knowledge before incorporating it.
   - If the next executable step is straightforward, emit it.
   - If all steps are complete, emit `final_message`.
3. Never invent a `kind` not listed above. Never wrap JSON in markdown.

# What "available knowledge" means

The original invocation parameters, all tool results in the conversation, all
step results in `<step_results>`, and any resolved callbacks. NOT the contents
of files you haven't read or sub-agent calls you haven't made.
```

Per-turn user-message attachment (assembled by `PlanExecutor.before_run` / per-iteration hook), wrapped in `<system_reminder>` tags so it doesn't bust the prompt cache:

```xml
<system_reminder>
<plan_state revision="3">
  <step id="step_1" status="completed" kind="call_tool" tool_name="get_state_slice"/>
  <step id="step_2" status="completed" kind="call_subagent" subagent_id="player_intent_parser"/>
  <step id="step_3" status="pending" kind="call_subagent" subagent_id="rules_router" depends_on="step_2"/>
</plan_state>
<step_results>
  <result step="step_1">{...}</result>
  <result step="step_2">{...}</result>
</step_results>
<progress_check>
  Did the previous step make forward progress toward the plan goal?
  If not, consider amend_plan or submit_plan before continuing.
</progress_check>
</system_reminder>
```

### `agents/system.progress_ledger.md` (optional addendum for L3)

A short addendum appended only when `progress_ledger: true`:

```markdown
# Progress evaluation

After each step result, before deciding the next action, internally consider:
- Is the request fully satisfied? → emit `final_message`
- Are you in a loop (repeating the same kind of action with the same target)?
  → emit `amend_plan` or `submit_plan`
- Is forward progress being made?
  → if yes, continue with the next step
  → if no for the third consecutive step, escalate via `callback` with intent
    `execution_recovery`
```

---

## Part 5 — Implementation roadmap

Sized so each step ships value standalone.

### Step 1 — L1: prompt-only plan-execute mode (1–2 days)

- Add `agents/system.plan_execute.md` template.
- Wire it into the response-mode selector in `agents/agent.py` (same mechanism as `system.decision.md` etc.).
- Update `player_controller.md` to use it.
- Run an evaluator suite on player_controller cases. Confirm the model can plan-then-execute by re-emitting steps directly without runtime help.
- **Deliverable:** working planning behavior with zero code changes to the loop. Validates the prompt design before investing in L2.

### Step 2 — L2a + L2c: step references (2–3 days)

- Add `step_reference.py` (sibling of `file_reference.py`), with `resolve_step_references()` and a `StepReferenceResolver` Protocol.
- Add `host.step_ref_resolver` slot.
- Tests covering: invocation params, full-step refs, dot-path refs, missing ref → `ValueError`, nested objects/arrays.
- Don't yet integrate into the agent loop — this is a primitive.

### Step 3 — L2b + L2d: `submit_plan`/`amend_plan` and `PlanExecutor` behavior (3–5 days)

- Extend `AgentDecision`:
  - Add `kind ∈ {"submit_plan", "amend_plan"}` to allowed set.
  - Add `plan: tuple[PlanStep, ...]` field; `PlanStep` is a frozen dataclass mirroring `AgentDecision` plus `id` and `depends_on`.
  - Validate cycles, missing deps, unknown refs.
- Add `PlanState` and `CompletedStep` dataclasses.
- Add `behaviors/plan_executor.py` extending `AgentBehavior`:
  - `before_run`: initialize `PlanState`.
  - Override the per-turn loop: detect `submit_plan` / `amend_plan`, store; for ordinary `call_*` decisions, resolve refs against `PlanState.step_results`, dispatch, store result under step ID; inject per-turn `<plan_state>` reminder.
- Wire to frontmatter: `behavior: plan_executor`.
- Tests: end-to-end plan-execute round-trip; symbolic ref resolution; replan via `submit_plan`; unknown ref fails loud.

### Step 4 — L4: persistence (1–2 days)

- `ConversationSnapshot` dataclass.
- `ConversationStore.save_snapshot(conversation_id, snapshot)` / `load_snapshot(...)` methods on the protocol.
- Update `InMemoryConversationStore`.
- Persist on every `callback` and after every executed plan step.
- Tests: callback → resume reproduces full plan state.

### Step 5 — L3: progress ledger + structural replan (3–5 days)

- Extend `plan_executor` frontmatter with `progress_ledger`, `max_stall_count`, `max_replan_count`, `emit_plan_review`.
- Append progress-ledger reminder when enabled.
- Track stall / replan counters in `PlanState`.
- On stall threshold: force-inject "you must submit a new plan" reminder; on replan-count threshold: emit `callback(intent=execution_recovery)`.
- HITL plan review: after `submit_plan` (when `emit_plan_review: true`), emit `callback(intent=proposal_review)` with the plan; resume on resolution.
- Tests: stall→replan; replan-count→callback; plan-review approve/revise round-trip.

### Step 6 — Documentation (1 day)

- New `docs/architecture/adr-planning-agents.md`.
- Update `CLAUDE.md` with the new decision kinds and behavior contract.
- Update `agents/system.plan_execute.md` and `system.progress_ledger.md` with worked examples.
- Add a sample agent (e.g. `examples/planning_agent.md`) so contributors have a reference.

**Total estimate:** ~2 weeks for the full L1–L4 path; ~1 week if we stop after L2 (which already covers `player_controller.md`'s spec).

---

## Part 6 — Design choices we're explicitly NOT making

- **No DAG-as-data plan format** (LLMCompiler-style). Every framework that tried it pays in parser fragility. Our `depends_on` lists give us topological ordering and parallelism without a graph type.
- **No streaming planner that overlaps with execution.** LLMCompiler does this; the gain is real but the complexity is high. Defer until profiling shows we need it.
- **No vector memory for results.** Scope is short-horizon planning (one user turn). If we ever build long-horizon autonomous agents, revisit.
- **No new "planner agent" concept.** Per Plan-and-Solve and Magentic, the same agent that plans can execute. Splitting into planner+executor agents is achievable today via `call_subagent` + `AGENT_MODELS` if a project wants different models for different roles.
- **No silent step-result coercion.** If the model emits `{{step_2.field_that_doesnt_exist}}` we raise `ValueError` (per CLAUDE.md non-negotiable). The model gets the error back as a tool/decision-error and can recover or replan.
- **No retries inside the loop.** A failed tool call surfaces to the model; the model decides whether to amend the plan, replan, or escalate.
- **No "agent transfer / handoff" semantics.** Microsoft draws a useful distinction between agent-as-tool (we have) and full handoff (we don't). For our use case (short-horizon, parent retains ownership), agent-as-tool covers the cases. Revisit only if a use case requires full context transfer.

---

## Part 7 — Open questions for the maintainer

1. **Should `submit_plan`'s `plan` field be on `AgentDecision.parameters` or a top-level `plan` field?** Top-level is cleaner contractually but requires a contract bump. `parameters.plan` requires no schema change but is "JSON-inside-JSON" stringly-shaped. Recommendation: top-level `plan` field, since the `kind` is new anyway.

2. **Should plan steps with `kind: callback` be allowed?** `player_controller.md` implies yes (the planner can plan to ask the user). It complicates the executor (callback escalation mid-plan). Recommendation: yes, but the callback short-circuits subsequent steps until resolved.

3. **What's the right default `max_stall_count`?** Magentic uses 3, MS Agent Framework's reference uses 1–3. Recommendation: 3 for defaults, configurable per agent.

4. **Should `amend_plan` exist, or do we always do `submit_plan` (full replace)?** `amend_plan` is cleaner for the model when only one step changes; `submit_plan` is simpler runtime-wise. LangGraph Plan-and-Execute does full replace. Recommendation: ship `submit_plan` first; add `amend_plan` only if eval data shows the model wastes tokens re-emitting unchanged steps.

5. **Should the `<plan_state>` reminder be JSON or XML?** XML is the prompt-engineering convention; JSON aligns with our decision contract. Recommendation: XML for the reminder envelope, JSON for the step bodies inside (matches Anthropic's conventions and claude-code's `<system-reminder>` pattern).

6. **Do we need a `plan_resume` callback intent in addition to `proposal_review`?** When a callback resolves mid-plan, we currently route through generic `callback` handling. A dedicated intent would let the runtime distinguish "user reviewed the plan" from "sub-agent escalated mid-step." Recommendation: keep it under `callback_intent` for now; promote only if we add HITL plan review (L3).

---

## Appendix — Mapping to `player_controller.md`

The reference file describes exactly the design above, in prose. Key quotes:

> "Plan the execution workflow using the tools and skills available."
> "Add an `id` parameter, that identifies a step uniquely, so it can be referenced in parameters {{step_1}} resolves to the json in the response from step `id: step_1`. {{step_1.decision}} resolves to the decision field in the json (or empty string if the field does not exist or it step 1 did not return a json object)"
> "Before each step, validate that the parameters are available."
> "After each step, validate the status. Check if the plan is still valid given the results of the step. If not, re-plan execution."

This synthesis turns those instructions into a runtime contract. The empty-string-on-missing-field semantics in the spec is **inconsistent with our no-silent-repair rule** — we should raise instead and surface the error to the model. (This is one of the open questions to confirm with the agent-adventure team before implementation.)
