# ADR — Planning Support

**Status:** Accepted (design phase). Implementation tracked under EPIC: Planning support.
**Date:** 2026-04-26
**Branch:** `feature/planning-support`
**Companion research:** `docs/research/planning_agents/` (six reports — read `00_synthesis.md` first).

---

## 1. Motivation

Some agents need to **plan** a sequence of steps, **execute** them (with parallel/sequential dispatch, tool calls, sub-agent calls, skills), **reuse** intermediate results between steps, and **re-plan** mid-execution when results invalidate the plan or a callback resolves with new information.

The reference use case is `agent-adventure/agents/player_controller.md`, which describes the desired behavior in prose: plan → execute step-by-step (with `{{step_id.field}}` references between steps) → after each step, validate; re-plan if needed → emit a final routed result.

This ADR introduces a **planning capability built into `AgentHost`**, opt-in per agent or per call, that does not bloat the existing `Agent` class.

---

## 2. Design principles

These principles are non-negotiable and must guide every implementation choice:

1. **No bloat on `Agent`.** The dispatch handlers (`handle_tool_call`, `handle_subagent_call`, etc.) stay where they are; the per-turn loop body is extracted into a `TurnDriver`. Planning behavior is implemented as a *new driver*, not as new methods on `Agent`.
2. **Opt-in, default off.** Existing agents see zero behavior change. Planning is enabled only when frontmatter declares it (with optional runtime override).
3. **Strict decision contract — no silent repair.** New decision kinds (`submit_plan`, `amend_plan`, `continue_plan`) are validated strictly. Invalid plans raise `ValueError` (per `CLAUDE.md` non-negotiable rule on structured model output). The new kinds are *only* accepted when planning is active on the run.
4. **Lenient `{{ref}}` resolution with WARNING logs.** A missing reference resolves to an empty string and emits a warning; it does not raise. Rationale: the model can adapt to missing fields and we want graceful degradation, not crashes — but we never silently lose the signal.
5. **Comprehensive logging.** DEBUG entry/exit and parameters; INFO at decision points; WARNING for recoverable errors; ERROR for critical failures. Logger: `agent_framework.planning`.
6. **Parallel step execution is a v1 feature**, not a future add-on. Adding parallelism after the fact is hard; designing the runtime to dispatch ready-batches via `asyncio.gather` from day one is much cheaper.
7. **MVP scope, designed for extension.** Short-horizon planning ships first; the data shapes (`PlanState`, decision schema) anticipate the long-horizon features (progress ledger, stall counter, HITL plan review, persistence) without implementing them v1.

---

## 3. Glossary

| Term | Meaning |
|---|---|
| **Turn** | One trip through the agent's per-iteration loop body: `decide → dispatch`. Returns `None` (continue) or a terminal `AgentResult`. |
| **Per-turn loop** | The body of `while self.should_continue(run):` in `Agent.run` (currently `agents/agent.py:430-449`). |
| **Step** | One element of a plan. Has an `id`, a `kind` (call_tool / call_subagent / invoke_skill / callback), parameters with optional `{{ref}}` tokens, and a `depends_on` list. |
| **Ready batch** | The set of plan steps whose `depends_on` are all in `PlanState.completed_steps`. The driver dispatches the batch in parallel. |
| **Reflect** | A model call after a batch (or at end of plan) where the model decides whether to continue, replan, escalate, or finalize. |
| **Plan revision** | Each `submit_plan` (or `amend_plan`) bumps `PlanState.plan_revision`. |
| **User-bound callback** | `kind` of `callback_to_caller`, `request_user_input`, or `request_resolution`. Pauses the plan and surfaces to the caller (user or parent agent). |
| **Model-bound callback** | `kind: callback` with one of the framework intents (`information_request`, `proposal_review`, `execution_recovery`, `delegation_return`, `policy_or_approval`, `guardrail_trip`). Originates from a sub-agent step; the planning model resolves it. |

---

## 4. Default flow (planning NOT enabled — existing behavior, formalized)

This section documents the current behavior so the `StandardTurnDriver` extraction is a faithful refactor with zero functional change.

### 4.1 Entry point

`Agent.run(host, parameters, caller_id, ...) -> AgentResult` in `agents/agent.py`.

### 4.2 Lifecycle (current code, lines 343-465)

1. `_create_run(...)` builds an `AgentRun` (mutable per-invocation state).
2. `host.register_run(...)` registers the run for tracing.
3. `host.normalize_memory_parameters(...)` normalizes any `MemoryRef` parameters.
4. `refresh_parameter_state(run)` resolves seed parameters, marks missing/invalid.
5. `build_context(host, run)` builds the initial `ModelContext` (system prompt + user prompt + conversation messages).
6. `audit_agent_call_started` event emitted.
7. `_run_pre_agent_hooks(...)` may produce an early result (skip the loop).
8. **Per-turn loop:**
    ```python
    while self.should_continue(run):
        self.before_iteration(run)
        decision = self.resolve_runtime_decision(run=run)
        if decision is None:
            context = self.build_context(host=host, run=run)
            decision = self.decide(host=host, run=run, context=context)
        outcome = self.dispatch_decision(host=host, run=run, decision=decision, caller_id=caller_id)
        self.after_iteration(run)
        if outcome is not None:
            post_result, continue_run = self._run_post_agent_hooks(...)
            if continue_run: continue
            return post_result
    ```
9. If the loop exits without an outcome: `complete_without_result(run)` produces a default `AgentResult`.
10. `finally:` records usage, emits `audit_agent_call_finished`.

### 4.3 Per-turn semantics

- **`should_continue(run)`** — currently always `True` (loop exits via outcome or hook).
- **`before_iteration(run)`** — no-op hook.
- **`resolve_runtime_decision(run)`** — returns a pre-supplied decision (e.g., from initial state or `respond_to_callback`); usually `None`.
- **`build_context(host, run)`** — assembles the `ModelContext`: system prompt template, user prompt, conversation messages, available tools, response mode.
- **`decide(host, run, context)`** — calls the model driver, parses the response into an `AgentDecision` via `AgentDecision.from_model_response`. Strict JSON validation per CLAUDE.md.
- **`dispatch_decision(host, run, decision, caller_id)`** — branches on `decision.kind`:
    - `final_message` → `handle_final_message` → return `AgentResult` (terminal)
    - `call_tool` → `handle_tool_call` → append result to messages, return `None`
    - `call_subagent` → `handle_subagent_call` → run subagent synchronously, append result, return `None`
    - `call_subagents` → `handle_subagent_calls` → batch dispatch via `host.call_subagent_batch`, append results
    - `invoke_skill` → `handle_skill_invocation`
    - `callback` / `callback_to_caller` / `request_user_input` / `request_resolution` → `handle_callback` → return terminal `AgentResult` with callback payload
- **`after_iteration(run)`** — no-op hook.

### 4.4 Termination

The loop terminates when:
- `dispatch_decision` returns a non-`None` outcome (final message or callback), OR
- A pre-agent hook produces an early result, OR
- (Theoretical) `should_continue` returns `False` — currently never happens in default behavior.

### 4.5 What changes vs. doesn't change with the refactor

**Changes:** the body of the `while` loop (steps in §4.3) moves into `StandardTurnDriver.run_turn(...)`. `Agent.run` calls `driver.run_turn(...)` instead of inlining the body.

**Doesn't change:**
- `Agent.run`'s outer setup, hooks, and finalization (§4.2 steps 1-7, 9-10, plus the `if outcome is not None` post-hook block).
- All `handle_*` dispatch methods.
- `AgentRun` shape (we add one optional field `plan_state` but it stays `None` for non-planning runs).
- `AgentDecision.from_model_response` (extended kind set is gated, see §6.3).
- All existing tests pass without modification.

---

## 5. The `TurnDriver` refactor

### 5.1 Protocol

```python
# src/agent_framework/agents/turn_driver.py

from typing import Protocol, TYPE_CHECKING
from .agent_result import AgentResult
from .agent_run import AgentRun

if TYPE_CHECKING:
    from .agent import Agent
    from agent_framework.host_protocol import AgentHostProtocol


class TurnDriver(Protocol):
    """Drives one or more turns of an agent invocation.

    A driver is invoked once per outer loop iteration in Agent.run.
    It returns AgentResult to terminate the run, or None to continue
    looping. The outer loop also handles post-agent hooks; the driver
    is responsible only for the per-iteration model+dispatch work.
    """

    def run_turn(
        self,
        *,
        agent: "Agent",
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
    ) -> AgentResult | None: ...
```

### 5.2 `StandardTurnDriver`

```python
# src/agent_framework/agents/turn_driver.py (continued)

class StandardTurnDriver:
    """Faithful extraction of the existing per-turn loop body.

    Behavior is identical to the inline loop in Agent.run prior to this
    refactor. Used for all agents that do not enable planning.
    """

    def run_turn(self, *, agent, host, run, caller_id):
        agent.before_iteration(run)
        decision = agent.resolve_runtime_decision(run=run)
        if decision is None:
            context = agent.build_context(host=host, run=run)
            decision = agent.decide(host=host, run=run, context=context)
        outcome = agent.dispatch_decision(
            host=host, run=run, decision=decision, caller_id=caller_id,
        )
        agent.after_iteration(run)
        return outcome
```

### 5.3 Updated `Agent.run`

The body of the `while` loop is replaced by a driver call:

```python
# In Agent.run (replacing lines 430-449):

driver = self._select_turn_driver(planning_override=planning_override)
while self.should_continue(run):
    outcome = driver.run_turn(
        agent=self, host=host, run=run, caller_id=caller_id,
    )
    if outcome is not None:
        post_result, continue_run = self._run_post_agent_hooks(
            host=host, run=run, caller_id=caller_id, result=outcome,
        )
        if continue_run:
            continue
        return post_result
```

### 5.4 Driver selection

```python
# New helper on Agent (lives in agent.py near the other private helpers):

def _select_turn_driver(
    self,
    *,
    planning_override: bool | None,
) -> TurnDriver:
    """Choose a driver for this invocation.

    Resolution order:
        1. planning_override (True/False) wins if not None.
        2. self.planning_config (parsed from frontmatter) decides otherwise.
        3. Default: StandardTurnDriver.

    Logs the selection at INFO if planning is selected.
    """
    if planning_override is False:
        return StandardTurnDriver()
    if planning_override is True or (
        self.planning_config is not None and self.planning_config.enabled
    ):
        from agent_framework.planning.turn_driver import PlanningTurnDriver
        config = self.planning_config or PlanningConfig.default_enabled()
        _LOGGER.info(
            "Selecting PlanningTurnDriver for agent %s (override=%s, config=%r)",
            self.agent_id, planning_override, config,
        )
        return PlanningTurnDriver(config=config)
    return StandardTurnDriver()
```

### 5.5 Public surface change on `Agent.run` and `AgentHost.run_agent`

Both gain a new keyword-only argument:

```python
def run(self, *, host, parameters=None, ..., planning_override: bool | None = None):
    ...

def run_agent(self, agent_id_or_path, *, parameters=None, ..., planning_override: bool | None = None):
    ...
```

`None` (default) means "use frontmatter setting." Test/CLI code can force on or off.

### 5.6 Net code-size impact on `Agent`

- **Removed:** ~17 lines of inline loop body.
- **Added:** one `_select_turn_driver` method (~15 lines), one `planning_config: PlanningConfig | None` field on the class, parameter in `Agent.run`.
- **Net:** approximately neutral in line count; large gain in extensibility and testability.

---

## 6. Planning declaration and configuration

### 6.1 Frontmatter schema

```yaml
---
id: my_planning_agent
role: ...
parameters:
  ...
planning:
  enabled: true
  parallel_execution: true       # default true; ready batches dispatch in parallel
  ref_resolution: lenient        # only "lenient" supported in MVP; "strict" is reserved
  max_steps: 50                  # safety cap on total step executions per run
  max_plan_revisions: 3          # max submit_plan/amend_plan calls per run
  step_timeout_seconds: 60       # per-step deadline (subagent or tool); 0 = no timeout
  reflect_after_each_batch: false # MVP: must be false; ship-config later
---
```

`planning:` block is **optional**. If absent or `enabled: false`, the agent is non-planning and gets `StandardTurnDriver`.

### 6.2 `PlanningConfig` dataclass

```python
# src/agent_framework/planning/config.py

from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class PlanningConfig:
    enabled: bool = False
    parallel_execution: bool = True
    ref_resolution: str = "lenient"  # "lenient" | "strict" (strict is reserved)
    max_steps: int = 50
    max_plan_revisions: int = 3
    step_timeout_seconds: float = 60.0
    reflect_after_each_batch: bool = False

    @classmethod
    def from_frontmatter(cls, raw: dict | None) -> "PlanningConfig | None":
        """Parse a frontmatter `planning:` block.

        Returns None if `raw` is None or `enabled: false`. Raises
        ValueError on unknown fields, invalid types, or unsupported
        values (e.g., ref_resolution other than "lenient").
        """
        ...

    @classmethod
    def default_enabled(cls) -> "PlanningConfig":
        """Defaults for a planning_override=True with no frontmatter block."""
        return cls(enabled=True)
```

Validation rules:
- Unknown keys → `ValueError`.
- `ref_resolution` ∉ `{"lenient", "strict"}` → `ValueError`. `"strict"` is reserved (not yet implemented; raise with a clear "not yet supported" message).
- `reflect_after_each_batch=True` → `ValueError` ("not yet supported in MVP; reserved for future release").
- `max_steps`, `max_plan_revisions` must be positive integers; `step_timeout_seconds` must be `>= 0` (0 means no timeout).

### 6.3 Decision contract gating

`AgentDecision._ALLOWED_DECISION_KINDS` is **extended** with `submit_plan`, `amend_plan`, `continue_plan` — but `from_model_response` accepts these kinds **only when called in a planning context**. The cleanest mechanism:

- Add a `planning_active: bool = False` parameter to `AgentDecision.from_model_response`.
- `StandardTurnDriver` always passes `planning_active=False` (current behavior preserved).
- `PlanningTurnDriver` passes `planning_active=True`.
- When `planning_active=False` and a decision uses one of the new kinds → raise `ValueError` with a clear message: "decision kind 'submit_plan' is only valid for planning-enabled agents."

This keeps the contract strict for non-planning agents (so a regular agent that emits `submit_plan` by mistake fails loud) without polluting `AgentDecision` with global state.

### 6.4 Programmatic override

`AgentHost.run_agent` and `Agent.run` accept `planning_override: bool | None = None`. Resolution order in `_select_turn_driver`: override > frontmatter > default off.

---

## 7. Decision contract additions

### 7.1 New kinds

| Kind | When | Payload |
|---|---|---|
| `submit_plan` | First turn of a planning run, or any reflect when full replan is needed | `plan: list[PlanStep]`, `message: str` (reasoning) |
| `amend_plan` | Reserved in v1 — parsing accepts it but `PlanningTurnDriver` raises `NotImplementedError` until full implementation lands | (same shape; future: `add: list`, `remove: list[step_id]`) |
| `continue_plan` | Reflect after a batch when the model is satisfied with progress | `message: str` (optional reasoning) |

### 7.2 `PlanStep` schema

```python
# src/agent_framework/planning/plan_state.py

@dataclass(frozen=True, slots=True)
class PlanStep:
    id: str                              # unique within plan; [a-zA-Z][a-zA-Z0-9_]* pattern
    kind: str                            # one of: call_tool, call_subagent, invoke_skill, callback
    parameters: dict[str, Any]           # may contain {{ref}} tokens; resolved at dispatch time
    tool_name: str | None = None         # required iff kind == call_tool
    subagent_id: str | None = None       # required iff kind == call_subagent
    skill_name: str | None = None        # required iff kind == invoke_skill
    callback_intent: str | None = None   # required iff kind == callback
    depends_on: tuple[str, ...] = ()     # references earlier step IDs (no forward refs)
    message: str = ""                    # for callback steps: the message body
```

### 7.3 Plan validation rules (enforced in `AgentDecision.from_model_response` when parsing `submit_plan`)

All raise `ValueError` with descriptive messages:

1. `plan` field missing or not a non-empty list.
2. Step `id` not unique within the plan.
3. Step `id` does not match `^[a-zA-Z][a-zA-Z0-9_]*$`.
4. Step `kind` not in `{call_tool, call_subagent, invoke_skill, callback}` (no nested `submit_plan`).
5. `tool_name`/`subagent_id`/`skill_name`/`callback_intent` not consistent with `kind`.
6. `depends_on` contains a step ID not earlier in the list (no forward refs).
7. `depends_on` contains an unknown step ID.
8. Cycles in the dependency graph (must be a DAG).
9. Both `subagent_id` and `tool_name` set on the same step.

`{{ref}}` tokens inside `parameters` are **NOT** validated at parse time (they're resolved at dispatch time, lenient). This matches the no-pre-validation principle: the model can use refs the resolver can't yet resolve, and the warning fires when the actual dispatch happens.

### 7.4 JSON examples

**submit_plan:**
```json
{
  "kind": "submit_plan",
  "message": "I need state, then parse intents, then route them.",
  "plan": [
    {
      "id": "step_state",
      "kind": "call_tool",
      "tool_name": "get_state_slice",
      "parameters": {"actor_id": "{{player_actor_id}}"}
    },
    {
      "id": "step_parse",
      "kind": "call_subagent",
      "subagent_id": "player_intent_parser",
      "parameters": {
        "actor_id": "{{player_actor_id}}",
        "state_slice": "{{step_state}}",
        "prompt": "{{player_prompt}}"
      },
      "depends_on": ["step_state"]
    },
    {
      "id": "step_route",
      "kind": "call_subagent",
      "subagent_id": "rules_router",
      "parameters": {"intents": "{{step_parse.declared_intents}}"},
      "depends_on": ["step_parse"]
    }
  ]
}
```

**continue_plan:**
```json
{
  "kind": "continue_plan",
  "message": "step_state returned a non-empty slice; continuing."
}
```

**final_message** (terminal in a planning run):
```json
{
  "kind": "final_message",
  "message": "{...serialized routed intents...}",
  "parameters": {
    "status": "ready",
    "player_actor_id": "4",
    "routed_intents": [...],
    "reasoning": [...],
    "clarifications": []
  }
}
```

---

## 8. `PlanState` and `AgentRun` changes

### 8.1 New types

```python
# src/agent_framework/planning/plan_state.py

@dataclass(slots=True)
class CompletedStep:
    step_id: str
    step: PlanStep
    result: Any                             # raw result payload
    started_at: float                       # epoch seconds
    finished_at: float
    plan_revision_at_start: int
    error: str | None = None                # set when step raised; result is then a stub

@dataclass(slots=True)
class PlanState:
    plan: tuple[PlanStep, ...] = ()
    step_results: dict[str, Any] = field(default_factory=dict)
    completed_steps: list[CompletedStep] = field(default_factory=list)
    plan_revision: int = 0
    total_steps_executed: int = 0
    pending_callback_step_id: str | None = None
    awaiting_caller_callback: bool = False  # set when plan paused for user-bound callback
```

### 8.2 `AgentRun` change

Add one field:

```python
@dataclass(slots=True)
class AgentRun:
    ...existing fields...
    plan_state: PlanState | None = None  # None for non-planning runs
```

Backwards compatible — every existing constructor call uses defaults.

### 8.3 Lifetime

`PlanState` is created by `PlanningTurnDriver` on first use within the run. It is in-memory only (V1) and discarded when the run ends. It is **not** persisted to `ConversationStore` in v1; that is a future feature (see §15).

---

## 9. `PlanningTurnDriver` — full state machine

### 9.1 High-level algorithm

```
on each call to run_turn:
    if run.plan_state is None:
        run.plan_state = PlanState()           # initialize lazily

    state = run.plan_state

    # If we're paused waiting for a caller-bound callback resolution, the
    # outer loop won't have called us back; this branch is defensive.
    if state.awaiting_caller_callback:
        return _resume_after_caller_callback(...)

    # Phase 1: PLAN
    if not state.plan:
        return _plan_phase(...)                # decide → expect submit_plan

    # Phase 2: EXECUTE
    ready_batch = _select_ready_batch(state)
    if ready_batch:
        return _execute_batch(ready_batch, ...)

    # Phase 3: REFLECT (after each batch OR end of plan)
    return _reflect_phase(...)
```

### 9.2 Plan phase

```
_plan_phase:
    LOG.debug("entering plan phase, run_id=%s", run.run_id)
    context = build_planning_context(...)       # uses system.plan_execute.md
    decision = agent.decide(host, run, context, planning_active=True)
    if decision.kind == "submit_plan":
        validate_plan_against_run(decision.plan, run)  # see §9.7
        state.plan = tuple(decision.plan)
        state.plan_revision += 1
        LOG.info(
            "plan submitted: revision=%d, steps=%d, ids=%s",
            state.plan_revision, len(state.plan), [s.id for s in state.plan],
        )
        return None  # continue: next call will start executing
    if decision.kind == "final_message":
        return agent.handle_final_message(host=host, run=run, decision=decision)
    if decision.kind in {"callback_to_caller", "request_user_input", "request_resolution"}:
        return agent.handle_callback(host=host, run=run, decision=decision, caller_id=caller_id)
    raise ValueError(
        f"plan phase: expected submit_plan|final_message|callback_to_caller, got {decision.kind!r}"
    )
```

### 9.3 Batch selection

```
_select_ready_batch(state) -> tuple[PlanStep, ...]:
    completed_ids = {cs.step_id for cs in state.completed_steps}
    ready = []
    for step in state.plan:
        if step.id in completed_ids:
            continue
        if step.id == state.pending_callback_step_id:
            continue  # blocked on callback resolution
        if all(dep in completed_ids for dep in step.depends_on):
            ready.append(step)
    return tuple(ready)
```

If `parallel_execution: false`, return at most one step (the first ready in plan order).

### 9.4 Batch execution

```
_execute_batch(batch, ...):
    LOG.debug("executing batch: ids=%s", [s.id for s in batch])

    # Safety cap
    if state.total_steps_executed + len(batch) > config.max_steps:
        return _emit_safety_cap_callback("max_steps", caller_id)

    # Resolve refs and dispatch
    coros = []
    for step in batch:
        resolved_params = step_reference.resolve(
            step.parameters,
            invocation_parameters=run.parameter_values,
            step_results=state.step_results,
            run_id=run.run_id, agent_id=agent.agent_id, step_id=step.id,
        )
        resolved_step = replace(step, parameters=resolved_params)
        coros.append(_dispatch_step(resolved_step, ...))

    if config.step_timeout_seconds > 0:
        coros = [asyncio.wait_for(c, config.step_timeout_seconds) for c in coros]

    results = await asyncio.gather(*coros, return_exceptions=True)

    # Record results; detect callbacks
    for step, result in zip(batch, results):
        completed = _record_step(step, result, ...)
        state.completed_steps.append(completed)
        state.total_steps_executed += 1

        # Step-level callback detection (model-bound)
        if isinstance(result, AgentDecision) and result.kind == "callback":
            state.pending_callback_step_id = step.id
            LOG.info(
                "step %s emitted callback intent=%s; entering reflect to resolve",
                step.id, result.callback_intent,
            )

    # If any step in the batch triggered a user-bound callback outcome, surface it
    for step, result in zip(batch, results):
        if _is_caller_bound_callback(result):
            state.awaiting_caller_callback = True
            return result  # AgentResult; outer loop returns it to caller

    return None  # continue: next call will reflect or pick next ready batch
```

`_dispatch_step` per `step.kind`:
- `call_tool` → `agent.handle_tool_call(host, run, decision_synth)` where `decision_synth` is a synthetic `AgentDecision(kind="call_tool", ...)`.
- `call_subagent` → `agent.handle_subagent_call(...)` similarly.
- `invoke_skill` → `agent.handle_skill_invocation(...)`.
- `callback` (a step that planned to ask) → return a synthetic `AgentDecision(kind="callback", ...)` for the reflect to pick up.

The `result` stored in `step_results[step.id]` is:
- For `call_tool`: the parsed tool result payload (JSON object if the tool returned JSON, else a string).
- For `call_subagent`: the sub-agent's `AgentResult.parameters` (or `.message` if no parameters).
- For `invoke_skill`: the skill's terminal output.
- For `callback`: the callback resolution payload (set after reflect resolves it).
- For an exception: `{"error": "<exception message>", "exception_type": "<class>"}` and `CompletedStep.error` is set.

### 9.5 Reflect phase

```
_reflect_phase(...):
    LOG.debug("entering reflect phase, run_id=%s", run.run_id)
    context = build_reflect_context(...)        # injects <plan_state>, <step_results>, <pending_callback> if any
    decision = agent.decide(host, run, context, planning_active=True)

    if decision.kind == "continue_plan":
        if state.pending_callback_step_id:
            _resolve_pending_callback(decision, ...)
        LOG.debug("reflect: continue_plan")
        return None

    if decision.kind == "submit_plan":
        if state.plan_revision >= config.max_plan_revisions:
            return _emit_safety_cap_callback("max_plan_revisions", caller_id)
        validate_plan_against_run(decision.plan, run)
        # On replan: keep step_results for steps that survive (by id),
        # but drop those whose step is no longer in the plan
        new_ids = {s.id for s in decision.plan}
        for old_id in list(state.step_results):
            if old_id not in new_ids:
                LOG.info("plan revision drops step %s; removing its result", old_id)
                del state.step_results[old_id]
                state.completed_steps = [
                    cs for cs in state.completed_steps if cs.step_id != old_id
                ]
        state.plan = tuple(decision.plan)
        state.plan_revision += 1
        state.pending_callback_step_id = None
        LOG.info("plan revised: revision=%d, steps=%d", state.plan_revision, len(state.plan))
        return None

    if decision.kind == "amend_plan":
        raise NotImplementedError("amend_plan reserved for a future release")

    if decision.kind == "final_message":
        return agent.handle_final_message(host=host, run=run, decision=decision)

    if decision.kind in {"callback_to_caller", "request_user_input", "request_resolution"}:
        state.awaiting_caller_callback = True
        return agent.handle_callback(host=host, run=run, decision=decision, caller_id=caller_id)

    raise ValueError(
        f"reflect phase: expected continue_plan|submit_plan|final_message|callback_to_caller, "
        f"got {decision.kind!r}"
    )
```

### 9.6 End-of-plan reflect

When `_select_ready_batch` returns empty AND no `pending_callback_step_id`, the driver enters `_reflect_phase`. The reflect context includes a `<end_of_plan>true</end_of_plan>` marker so the prompt template can instruct the model: "no more steps remain; emit `final_message` or `submit_plan`/`callback_to_caller` if the result is unsatisfactory."

This is **mandatory** — even a clean plan completion goes through one final model call before terminating. Rationale: gives the planner a chance to compose the final answer from the accumulated step_results.

### 9.7 Plan validation against run

Beyond the parse-time validation in §7.3, the driver validates each plan against the *current run*:

- Every `tool_name` resolves in `host.tool_registry`.
- Every `subagent_id` resolves in `host.agent_registry` and is permitted by `agent._validate_subagent_permission(...)`.
- Every `skill_name` resolves in `host.skill_registry`.

Failures raise `ValueError` and the offending plan is rejected. The model gets the error text in the next reflect's context (the same way other validation errors flow back).

---

## 10. Reference resolver

```python
# src/agent_framework/planning/step_reference.py

from typing import Any, Protocol
import logging
import re

_LOGGER = logging.getLogger("agent_framework.planning.step_reference")
_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\s*\}\}")


class StepReferenceResolver(Protocol):
    def resolve(
        self,
        value: Any,
        *,
        invocation_parameters: dict[str, Any],
        step_results: dict[str, Any],
        run_id: str,
        agent_id: str,
        step_id: str,
    ) -> Any: ...


def resolve(
    value: Any,
    *,
    invocation_parameters: dict[str, Any],
    step_results: dict[str, Any],
    run_id: str,
    agent_id: str,
    step_id: str,
) -> Any:
    """Recursively substitute {{token}} references in a JSON-shaped value.

    Lenient resolution:
        - Unknown tokens emit a WARNING log and resolve to "" (empty string).
        - Tokens inside strings are substituted via re.sub.
        - When the entire string IS a single token, the resolved value
          replaces the string entirely (preserving JSON type).
        - Lists and dicts are walked recursively.

    Token forms:
        {{name}}             -> invocation_parameters[name] OR step_results[name]
        {{name.path.to.x}}   -> dot-path lookup into the resolved root
    """
    ...
```

Resolution rules:

- **Whole-string-is-token** (`"{{step_state}}"`) → replaced with the resolved value (could be a dict, list, int, etc.). Preserves type.
- **String-contains-tokens** (`"prefix {{x}} suffix"`) → tokens are stringified (`str(value)`) and substituted in-place.
- **Dot-path lookup**: `step_results[root]` must be a `dict`; subsequent path segments index into nested dicts. Missing keys at any level → empty string + WARNING.
- **Lookup precedence**: `step_results` is checked first, then `invocation_parameters`. (Step refs override invocation params if names collide — a deliberate choice so a planner can override an invocation param mid-execution by emitting a step that names it.)

The resolver is **pluggable** via `host.step_ref_resolver: StepReferenceResolver | None`; if set, `PlanningTurnDriver` uses it instead of the default. This mirrors `host.file_ref_resolver`.

### 10.1 Logging

- DEBUG: every resolution call with the token, lookup result type, and a result-preview (truncated).
- WARNING: missing references with the token, run_id, agent_id, step_id, and the parameters dict path where it appeared.

---

## 11. Callback handling rules

### 11.1 Decision tree

```
A step in a batch produces an outcome:
├── outcome is None (normal tool/subagent result)
│       → record in completed_steps, store in step_results, continue
│
├── outcome is AgentResult with kind == callback (model-bound)
│       │  Examples: sub-agent emitted callback(intent=information_request)
│       → set pending_callback_step_id; do NOT remove step from plan
│       → enter reflect; model resolves
│       → on continue_plan: store resolution payload as step result, clear pending, continue
│       → on submit_plan / amend_plan: replace plan; old step (and its callback) discarded
│       → on callback_to_caller: re-emit (becomes user-bound), surface to caller
│
└── outcome is AgentResult with kind in {callback_to_caller, request_user_input, request_resolution}
        (user-bound)
    → set awaiting_caller_callback = true
    → return outcome from run_turn → outer loop returns to caller
    → on next run_turn (after caller resolves):
       resolve_runtime_decision(run) yields the resolution as a synthetic decision
       OR the planner picks up via Agent.respond_to_callback (existing mechanism)
       → mark the source step as completed with the resolution payload, continue
```

### 11.2 Resolution payload format

When a callback is resolved, the driver constructs a "result" for the step that triggered it:

```python
{
    "_callback_resolved": True,
    "intent": "<original intent>",
    "resolution": <whatever the resolver returned>,
}
```

Future `{{step_id.resolution}}` references work as expected; `{{step_id._callback_resolved}}` is an explicit signal.

### 11.3 Aborted steps

If the planner replans (`submit_plan`) and the new plan does not include a previously-completed step, the driver:

- Removes the step from `state.completed_steps`.
- Deletes `state.step_results[step_id]`.
- Logs INFO ("plan revision drops step X; removing its result").

Future `{{step_X.field}}` references in the new plan miss → WARNING + empty string. The planner should not reference dropped steps; if it does, it gets graceful degradation.

---

## 12. Error handling and safety caps

### 12.1 Step exceptions

A step's dispatch may raise. Exceptions are caught in `_execute_batch`:

```python
except asyncio.TimeoutError:
    result = {"error": f"step {step.id} timed out after {config.step_timeout_seconds}s",
              "error_type": "timeout"}
    LOG.warning("step %s timed out", step.id)
except Exception as exc:
    result = {"error": str(exc), "error_type": type(exc).__name__}
    LOG.error("step %s raised %s: %s", step.id, type(exc).__name__, exc, exc_info=True)
```

The error becomes the step's `result`; `CompletedStep.error` is set; the next phase is reflect (the model decides whether to recover, replan, or escalate).

### 12.2 Safety caps

Both caps emit a synthetic `callback_to_caller` instead of crashing:

```python
def _emit_safety_cap_callback(cap_name: str, caller_id):
    LOG.error(
        "planning safety cap %s exceeded for run %s; escalating to caller",
        cap_name, run.run_id,
    )
    decision = AgentDecision(
        kind="callback_to_caller",
        callback_intent="execution_recovery",
        message=(
            f"Planning safety cap '{cap_name}' was exceeded "
            f"(plan_revision={state.plan_revision}, total_steps={state.total_steps_executed}). "
            f"The planner could not complete the request and is escalating for guidance."
        ),
        parameters={
            "cap": cap_name,
            "plan_revision": state.plan_revision,
            "total_steps_executed": state.total_steps_executed,
            "current_plan": [step.id for step in state.plan],
        },
    )
    return agent.handle_callback(host=host, run=run, decision=decision, caller_id=caller_id)
```

### 12.3 Validation errors

If a `submit_plan` decision fails validation (cycles, unknown tools, etc.), the driver does NOT raise to the outer loop. It logs ERROR, then synthesizes a "validation error" message into the next reflect's context:

```
<plan_validation_error>
Your previous submit_plan failed validation: <error message>.
Emit a corrected submit_plan, callback_to_caller, or final_message.
</plan_validation_error>
```

This gives the model one chance to recover. If validation fails twice in a row (counter on `state`), the driver emits a safety-cap callback.

---

## 13. Logging contract

Logger hierarchy:
- `agent_framework.planning` — top-level driver events.
- `agent_framework.planning.step_reference` — ref resolution.
- `agent_framework.planning.config` — frontmatter parsing.

### 13.1 Required events

| Level | Event | Fields |
|---|---|---|
| DEBUG | `run_turn` entry | run_id, agent_id, phase (plan/execute/reflect) |
| DEBUG | `run_turn` exit | run_id, outcome_kind (None|"continue"|<terminal kind>) |
| DEBUG | step ref resolution | run_id, step_id, token, resolved_type, value_preview (≤80 chars) |
| DEBUG | step dispatch start | run_id, step_id, step_kind, target (tool_name/subagent_id/skill_name) |
| DEBUG | step dispatch end | run_id, step_id, duration_ms, result_type |
| INFO | planning driver selected | agent_id, run_id, override flag, config snapshot |
| INFO | plan submitted | run_id, plan_revision, step_count, step_ids |
| INFO | plan revised | run_id, new_revision, dropped_step_ids, added_step_ids |
| INFO | callback resolved internally (model) | run_id, step_id, intent |
| INFO | callback escalated to caller | run_id, intent |
| INFO | end-of-plan reflect | run_id |
| WARNING | missing ref | run_id, step_id, token, parameters_path |
| WARNING | step timeout | run_id, step_id, timeout_seconds |
| WARNING | safety cap approaching (>80%) | run_id, cap_name, current, limit |
| ERROR | step raised | run_id, step_id, exception_type, message (with exc_info) |
| ERROR | plan validation failure | run_id, plan_revision, validation_error |
| ERROR | safety cap exceeded | run_id, cap_name, current, limit |
| ERROR | NotImplementedError on amend_plan | run_id |

### 13.2 Test assertions

Tests should assert on log records via `caplog` for at least: plan submitted (INFO), missing ref (WARNING), step timeout (WARNING), safety cap exceeded (ERROR).

---

## 14. System prompt template

New file: `src/agent_framework/agents/system.plan_execute.md`.

This template is selected automatically when `planning.enabled: true`. The selector is the existing response-mode mechanism (see how `system.decision.md` / `system.text.md` / `system.json_object.md` are chosen today).

### 14.1 Required content

The prompt MUST:

1. Describe the **decision schema** including `submit_plan`, `continue_plan`, `final_message`, the existing `call_*` and `callback*` kinds. (Do not describe `amend_plan`; reserved.)
2. Describe the **plan step format** (id, kind, parameters, depends_on, target field per kind).
3. Describe **`{{token}}` syntax** — supported tokens (invocation params, step IDs, dot paths) and the lenient semantics (missing → empty string).
4. Describe the **three-phase workflow**: plan → execute (driven by runtime) → reflect/finalize.
5. Describe **callback handling** following the player_controller convention: try to resolve from available knowledge first; only escalate (`callback_to_caller`) when needed.
6. Specify the JSON output contract (no markdown wrapping; strict object shape).

### 14.2 Per-turn reminders (constructed by `PlanningTurnDriver`)

Wrapped in `<system_reminder>` user-message attachments (cache-friendly). Examples:

For the **plan phase** (turn 1):
```xml
<system_reminder>
<phase>plan</phase>
<instructions>Submit your initial plan as a `submit_plan` decision.</instructions>
</system_reminder>
```

For the **reflect phase** (after a batch):
```xml
<system_reminder>
<phase>reflect</phase>
<plan_state revision="1">
  <step id="step_state" status="completed" kind="call_tool" tool_name="get_state_slice"/>
  <step id="step_parse" status="completed" kind="call_subagent" subagent_id="player_intent_parser"/>
  <step id="step_route" status="pending" kind="call_subagent" subagent_id="rules_router" depends_on="step_parse"/>
</plan_state>
<step_results>
  <result step="step_state">{...JSON...}</result>
  <result step="step_parse">{...JSON...}</result>
</step_results>
<instructions>
Decide one of: continue_plan, submit_plan (full replan), final_message, callback_to_caller.
</instructions>
</system_reminder>
```

For **end-of-plan reflect**:
```xml
<system_reminder>
<phase>reflect</phase>
<end_of_plan>true</end_of_plan>
<plan_state ...>...</plan_state>
<step_results>...</step_results>
<instructions>
All planned steps are complete. Emit `final_message` with the agreed output shape, OR
`submit_plan` if the results require additional steps, OR `callback_to_caller` if you cannot proceed.
</instructions>
</system_reminder>
```

For a **pending callback**:
```xml
<system_reminder>
<phase>reflect</phase>
<pending_callback step_id="step_parse" intent="information_request">
  <message>The intent parser asks: which actor did the player mean by "him"?</message>
</pending_callback>
<plan_state ...>...</plan_state>
<step_results>...</step_results>
<instructions>
Resolve the pending callback from available knowledge if possible; emit `continue_plan`
with a `parameters.resolution` field. If you cannot resolve, emit `callback_to_caller`.
</instructions>
</system_reminder>
```

---

## 15. Persistence (out of scope for v1; design hooks present)

`PlanState` is in-memory only in v1. The shape is **serialization-ready** so future work can:

1. Add `PlanState.to_dict()` / `from_dict()`.
2. Extend `ConversationStore` with `save_plan_state(conversation_id, run_id, state)` / `load_plan_state(...)`.
3. Persist after every batch and on every callback escalation.

This enables long-paused workflows (e.g., a callback waiting hours for user input) to resume cleanly. v1 punts because callbacks in agent_framework today are typically resolved within the same process.

---

## 16. Test strategy

### 16.1 Unit tests

| Module | Required coverage |
|---|---|
| `planning/config.py` | All field validation rules; `from_frontmatter` returning `None` for absent/disabled blocks; round-trip with `default_enabled`. |
| `planning/step_reference.py` | Whole-string-is-token (preserves type); embedded tokens (string substitution); dot-path success; missing token → "" + WARNING (assert via caplog); nested dict/list traversal; precedence (step_results > invocation_parameters); dependency on `host.step_ref_resolver` override path. |
| `planning/plan_state.py` | `PlanState` mutation semantics; `CompletedStep` with and without error. |
| `agents/turn_driver.py` | `StandardTurnDriver` produces identical behavior to the pre-refactor inline loop (snapshot test or behavior-equivalence test against several scripted runs). |
| `agents/agent_decision.py` | `submit_plan` parsing: all validation rules in §7.3; gating via `planning_active`; `continue_plan` parsing; `amend_plan` accepted-but-reserved (raises `NotImplementedError` only at driver level). |

### 16.2 Driver integration tests

With a mock model driver that returns scripted decisions:

1. **Happy path:** plan → 3 sequential steps → final_message. Assert step_results, completed_steps, plan_revision==1.
2. **Parallel batch:** plan with two independent steps → batch executes both via `asyncio.gather` → reflect → 3rd step → final.
3. **Replan after batch:** step result triggers reflect → `submit_plan` with new steps → new batch executes → final. Assert old step_results dropped if not in new plan.
4. **Model-bound callback:** sub-agent step returns callback → reflect with `<pending_callback>` → model emits `continue_plan(parameters.resolution=...)` → step marked complete → continue.
5. **User-bound callback:** step emits `callback_to_caller` → driver returns outcome to outer loop → caller resumes via `Agent.respond_to_callback` → next batch executes.
6. **Step timeout:** sub-agent that sleeps past `step_timeout_seconds` → result is `{"error": ..., "error_type": "timeout"}` → reflect → model emits `submit_plan` to recover.
7. **Safety cap (max_steps):** plan that would exceed cap → driver emits `callback_to_caller(intent=execution_recovery)`.
8. **Safety cap (max_plan_revisions):** model emits 4 consecutive `submit_plan` decisions → driver escalates after the 3rd.
9. **Plan validation failure:** model emits plan with cycle → driver injects error reminder → model corrects → continues.
10. **End-of-plan reflect:** clean plan completes → driver enters reflect with `<end_of_plan>` → model emits final_message.

### 16.3 Backwards compatibility tests

- Run the full existing test suite — must pass unchanged.
- For each existing agent in `agents/`, instantiate and verify `_select_turn_driver` returns `StandardTurnDriver`.
- An agent without `planning:` frontmatter that emits a `submit_plan` decision must fail with `ValueError` ("decision kind ... is only valid for planning-enabled agents").

### 16.4 Logging assertions

Use pytest's `caplog` to assert at least:
- INFO log on plan submitted.
- WARNING log on missing ref.
- WARNING log on step timeout.
- ERROR log on safety cap exceeded.

### 16.5 End-to-end with `player_controller.md`

Once the MVP is stable:
1. Update `agent-adventure/agents/player_controller.md` to add `planning: enabled: true` to its frontmatter.
2. Run the existing evaluator suite for player_controller cases.
3. Compare results to baseline (must pass at least the same cases as the manual implementation).

---

## 17. Migration & compatibility

- **No existing agent file changes.** Agents without a `planning:` frontmatter block are unaffected.
- **No existing test changes.** All existing tests must pass unchanged after the `TurnDriver` refactor.
- **Public API additions:** `planning_override` parameter on `Agent.run` and `AgentHost.run_agent` (keyword-only, defaults to `None`).
- **Public API extension:** `AgentDecision.from_model_response` gains a `planning_active: bool = False` parameter.
- **Logger names:** `agent_framework.planning.*`. No conflict with existing loggers.
- **Frontmatter parsing:** the `planning:` key is added to the recognized set; unknown keys outside `planning:` continue to error as today (no broader frontmatter changes).

---

## 18. Future work (deferred from v1)

These are explicit non-goals for v1 but the architecture must accommodate them:

| Feature | Rationale to defer | When to revisit |
|---|---|---|
| **Strict ref resolution mode** | MVP ships lenient only. | When a project explicitly demands fail-loud refs. |
| **`amend_plan` full implementation** | `submit_plan` covers all v1 use cases; `amend_plan` is reserved in the kind set so the contract is forward-compatible. | When telemetry shows >30% of replans only change one step. |
| **Progress ledger / stall counter** | Magentic-style structural replan. Not needed for short-horizon `player_controller`-style use cases. | When long-horizon autonomous agents become a use case. |
| **HITL plan review primitive** | A typed `plan_review_request` callback intent for "show the user the plan before executing." | When user-facing planning workflows need explicit approval gates. |
| **`PlanState` persistence** in `ConversationStore` | Required only when callbacks pause for hours/days across processes. | When durable workflows ship. |
| **`reflect_after_each_batch: true`** | Adds a model call per batch; expensive. v1 reflects only on callback/error/end-of-plan. | When evaluator data shows the planner missing opportunistic mid-plan corrections. |
| **Streaming plan dispatch** (LLMCompiler-style) | Start executing independent steps before the planner finishes generating. Real perf win, real complexity. | When latency profiling shows planner-tail-latency dominating. |
| **Vector-store result memory** for cross-run reuse | Long-horizon autonomous loops. Not relevant to short-horizon planners. | When autonomous agents become a use case. |
| **Planner-vs-executor model split** via `AGENT_MODELS` | The framework already supports per-agent models; split is achievable today by using `call_subagent` to a cheap-model executor. v1 doesn't bake it in. | When cost analysis shows planning calls dominating spend. |

---

## 19. Open questions resolved (decision log)

| Question | Decision |
|---|---|
| Extension shape: AgentBehavior vs PlanningOrchestrator vs PlanningRunner | **`TurnDriver` protocol with `StandardTurnDriver` and `PlanningTurnDriver`.** Cleaner separation, single host entry point, future drivers drop in. |
| Scope: short-horizon vs long-horizon | **Short-horizon now, hooks for long-horizon later.** |
| Ref resolution: strict vs lenient | **Lenient with WARNING log.** Strict is reserved (config rejects). |
| Plan storage location | **`PlanState` on `AgentRun`** (in-memory v1; persistence is future work). |
| Decision contract: extend or reuse | **Extend `AgentDecision`** with new kinds, gated by `planning_active`. |
| Parallel step execution in v1 | **Yes** — too hard to retrofit later. |
| Frontmatter shape | **Nested `planning:` block** with explicit `enabled` flag. |
| Reflect cadence | **B + mandatory end-of-plan reflect.** Per-batch reflect is opt-in future config. |
| Callback handling | **A + C combined.** User-bound → pause + caller. Model-bound → pause + model resolves; on resolution, continue / replan / abort+replan. |
| `amend_plan` in v1 | **Reserved** (parsing accepts; driver raises `NotImplementedError`). |
| End-of-plan reflect mandatory | **Yes** — even clean plans get one reflect call to compose the final answer. |
| Step abort semantics | **`step_results[id]` deleted; future refs warn-and-empty-string.** |
| Safety cap breach | **Synthetic `callback_to_caller(intent=execution_recovery)`** — never crash. |
| Logger naming | **`agent_framework.planning.*`**. |
| Refactor scope | **Two-driver split only.** `handle_*` dispatchers stay on `Agent`. |

---

## 20. References

- `docs/research/planning_agents/00_synthesis.md` — full design synthesis with citations.
- `docs/research/planning_agents/01_claude_code.md` — TodoWriteTool, Plan Mode, AgentTool patterns.
- `docs/research/planning_agents/02_nano_claude_code.md` — persistent Task store, threaded sub-agents.
- `docs/research/planning_agents/03_langgraph.md` — Plan-and-Execute, ReWOO, LLMCompiler.
- `docs/research/planning_agents/04_microsoft_agent_framework.md` — Magentic, Handoff, Workflow.
- `docs/research/planning_agents/05_other_frameworks.md` — OpenAI Agents SDK, CrewAI, AutoGPT, ReAct/Plan-and-Solve/Reflexion/ToT, Anthropic guide, OpenHands.
- `agent-adventure/agents/player_controller.md` — reference use case (this is the spec the design serves).
- `CLAUDE.md` — non-negotiable rules on structured model output.

---

## 21. File-creation checklist (for implementers)

The implementation creates these new files:

- `src/agent_framework/agents/turn_driver.py` — `TurnDriver` protocol + `StandardTurnDriver`.
- `src/agent_framework/agents/system.plan_execute.md` — system prompt for planning agents.
- `src/agent_framework/planning/__init__.py`
- `src/agent_framework/planning/config.py` — `PlanningConfig`.
- `src/agent_framework/planning/plan_state.py` — `PlanState`, `PlanStep`, `CompletedStep`.
- `src/agent_framework/planning/step_reference.py` — `{{token}}` resolver.
- `src/agent_framework/planning/turn_driver.py` — `PlanningTurnDriver`.
- `tests/planning/test_config.py`
- `tests/planning/test_step_reference.py`
- `tests/planning/test_plan_state.py`
- `tests/planning/test_decisions.py`
- `tests/planning/test_planning_turn_driver.py`
- `tests/agents/test_standard_turn_driver.py`
- `tests/agents/test_turn_driver_selection.py`
- `docs/architecture/adr-planning.md` (this file).

Modified files:

- `src/agent_framework/agents/agent.py` — extract loop body to driver; add `planning_config` field; add `_select_turn_driver`; add `planning_override` to `run`.
- `src/agent_framework/agents/agent_run.py` — add `plan_state: PlanState | None = None` field.
- `src/agent_framework/agents/agent_decision.py` — extend `_ALLOWED_DECISION_KINDS`; add `planning_active` parameter to `from_model_response`; add plan validation logic.
- `src/agent_framework/host.py` — add `planning_override` to `run_agent`; thread through to `Agent.run`.
- `src/agent_framework/agents/agent_loader.py` (or wherever frontmatter is parsed) — parse `planning:` block into `PlanningConfig`.
- `CLAUDE.md` — add planning section under "Architecture / Decision Loop."

---

## 22. Out of scope (explicit non-goals)

- Changing the dispatch handlers (`handle_*`) on `Agent`.
- Refactoring `AgentRun` beyond adding one optional field.
- Changing `ConversationStore` (planning state is in-memory v1).
- Changing the existing system prompt templates.
- Adding any planning-specific behavior to `AgentBehavior` or `SequentialHook`.
- Adding new MCP / skill / tool primitives.
- Changing how `call_subagents` works (the planning driver dispatches via the same primitives).
