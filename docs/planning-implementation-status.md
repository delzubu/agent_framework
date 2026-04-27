# Planning Support — Implementation Status

**Branch:** `feature/planning-support`
**Last updated:** 2026-04-27
**EPIC:** Planning support (EPIC #57)
**ADR:** `docs/architecture/adr-planning.md`
**Research:** `docs/research/planning_agents/` (read `00_synthesis.md` first)

---

## What is this?

This document captures the exact state of implementation so an incoming agent can continue without needing any prior conversation context. It covers what is done, what is not done, where everything lives, and the key design choices that must not be undone.

---

## Implementation status by FEAT

### Done — merged into `feature/planning-support`

| FEAT | Description | Key files |
|------|-------------|-----------|
| #58 | `TurnDriver` protocol + `StandardTurnDriver` extraction | `src/agent_framework/agents/turn_driver.py`, `tests/test_turn_driver.py` |
| #59 | `PlanningConfig` — frontmatter parsing | `src/agent_framework/planning/config.py`, `tests/planning/test_config.py` |
| #60 | `PlanStep`, `CompletedStep`, `PlanState` dataclasses | `src/agent_framework/planning/plan_state.py`, `tests/planning/test_plan_state.py` |
| #61 | `{{token}}` step-reference resolver | `src/agent_framework/planning/step_reference.py`, `tests/planning/test_step_reference.py` |
| #62 | `AgentDecision` extensions — `submit_plan`, `amend_plan`, `continue_plan` kinds; plan validation; `planning_active` gate | `src/agent_framework/agents/agent_decision.py`, `tests/planning/test_decisions.py` |
| #63 | `PlanningTurnDriver` — plan/execute/reflect state machine; `_select_ready_batch`; parallel dispatch via `ThreadPoolExecutor`; `Agent._select_turn_driver` wired | `src/agent_framework/planning/turn_driver.py`, `tests/planning/test_planning_turn_driver.py` |
| #64 | Model-bound callback steps — `kind: callback` in plan pauses execution; `continue_plan(parameters.resolution=…)` in REFLECT resolves it; `{{step_id.resolution}}` ref works | `src/agent_framework/planning/turn_driver.py`, `tests/planning/test_planning_turn_driver_callbacks.py` |
| #65 | Safety caps and plan validation error recovery — `max_steps`, `max_plan_revisions` caps with 80% warnings; step exception handling; consecutive validation failure recovery | `src/agent_framework/planning/turn_driver.py`, `src/agent_framework/planning/plan_state.py`, `tests/planning/test_planning_turn_driver_safety.py` |
| #66 | `system.plan_execute.md` — planning system prompt template; `response_mode: plan_execute`; `build_context` wiring | `src/agent_framework/agents/system.plan_execute.md`, `src/agent_framework/model.py`, `src/agent_framework/agents/agent.py`, `tests/planning/test_system_plan_execute.md` |

### Done — merged into `feature/planning-support`

| FEAT | Description | Key files |
|------|-------------|-----------|
| #67 | `player_controller` planning integration — rewrote agent with `planning: enabled: true`; two-phase plan (parallel retrieval + intent parsing → object details + routing); committed to agent-adventure `main` | `agent-adventure/agents/player_controller.md` |

### Not done

| FEAT / Issue | Description | Notes |
|---|---|---|
| #74 | Review planning documentation — TOC presence and reading narrative | Review `docs/architecture/adr-planning.md` for TOC and narrative completeness. |

---

## Architecture summary

### How planning is activated

A Markdown agent file opts in via frontmatter:

```yaml
planning:
  enabled: true
  max_steps: 50           # default; safety cap terminates run when exceeded
  max_plan_revisions: 3   # default; cap on how many replans are allowed
  parallel_execution: true  # default; set false for sequential-only
  step_timeout_seconds: 60  # per-step timeout for parallel batches
```

`PlanningConfig.from_frontmatter()` parses this. An agent with `enabled: true` gets a `PlanningTurnDriver` instead of `StandardTurnDriver`. Can also be forced at call time via `host.run_agent(…, planning_override=True)`.

### Three-phase state machine (`PlanningTurnDriver.run_turn`)

Each call to `run_turn` advances one phase; `None` means continue the loop, `AgentResult` means stop.

```
PLAN     — plan_state is None → call model → expect submit_plan
EXECUTE  — _select_ready_batch returns steps → dispatch them
REFLECT  — no ready batch → call model → continue_plan / submit_plan (replan) / final_message
```

### Key types

- **`PlanState`** (on `AgentRun.plan_state`) — mutable per-run: `plan`, `step_results`, `completed_steps`, `plan_revision`, `total_steps_executed`, `pending_callback_step_id`, `consecutive_validation_failures`
- **`PlanStep`** — frozen: `id`, `kind`, `tool_name`/`subagent_id`/`skill_name`/`callback_intent`, `parameters`, `depends_on`, `message`
- **`CompletedStep`** — record of a finished step with timing, result, and optional error

### Decision kinds (planning-only, gated by `planning_active=True`)

| Kind | Phase | Meaning |
|---|---|---|
| `submit_plan` | PLAN or REFLECT | Submit or replace the plan. Validated strictly (no forward refs, no duplicate IDs, kind-target consistency). |
| `continue_plan` | REFLECT | Continue execution (possibly resolving a pending callback step via `parameters.resolution`). |
| `amend_plan` | — | Reserved; raises `NotImplementedError` in v1. |

### `_select_ready_batch` logic

Uses `step_results` (not `completed_steps`) as the "already dispatched" guard. This means pending callback steps (in `step_results` but not `completed_steps`) are skipped without being re-dispatched, and their dependents remain blocked.

### Model-bound callback steps

A step with `kind: callback` pauses plan execution. The runtime:
1. Stores a `{"_callback_pending": True, …}` sentinel in `step_results[step_id]` (prevents re-dispatch).
2. Does NOT add to `completed_steps` (so dependents stay blocked and `_all_steps_done` stays False).
3. Sets `plan_state.pending_callback_step_id`.
4. In the next REFLECT, injects a `<pending_callback>` tag into the reminder.
5. When model emits `continue_plan(parameters.resolution=…)`, the callback step is resolved and added to `completed_steps`. Dependents can now run.
6. If model replans instead, `pending_callback_step_id` is cleared.

### Safety caps

Both caps check at the beginning of the relevant phase, before dispatching:

- **`max_steps`**: checked in `_execute_phase`. When `total_steps_executed >= max_steps`, calls `_emit_safety_cap_callback`.
- **`max_plan_revisions`**: checked in `_apply_replan`. When `plan_revision >= max_plan_revisions`, calls `_emit_safety_cap_callback`.
- Both log WARNING at ≥ 80% of the cap value.

`_emit_safety_cap_callback` returns `AgentResult(status="failed")` directly when there is no real parent caller (`caller_id` is `None` or `"host"`). When there IS a real parent, it escalates via `agent.handle_callback` with `intent=execution_recovery`.

### Plan validation error recovery

`ValueError` from `AgentDecision.from_model_response` (e.g. forward reference, missing `tool_name`) is caught in both `_plan_phase` and `_reflect_phase`:
- First consecutive failure: injects `<plan_validation_error>` reminder and returns `None` (retry).
- Second consecutive failure: triggers safety cap.
- `_plan_phase` uses `PlanningTurnDriver._plan_phase_failures: dict[str, int]` (keyed by `run.run_id`) because `PlanState` doesn't exist yet.
- `_reflect_phase` uses `plan_state.consecutive_validation_failures`.

### `{{token}}` reference resolution

`src/agent_framework/planning/step_reference.py` resolves `{{step_id}}`, `{{step_id.field}}`, `{{param_name}}` tokens in step parameters. Resolution order: `step_results` wins over `invocation_parameters`. Missing references resolve to `""` and log a WARNING (lenient mode). Pluggable via `host.step_ref_resolver` (any object with a `resolve(value, **kwargs)` method).

### System prompt

`src/agent_framework/agents/system.plan_execute.md` is injected when `response_mode == "plan_execute"`. It covers the three phases, all decision kinds, step format, `{{token}}` syntax, and the per-turn reminder tags (`<plan_state>`, `<step_results>`, `<pending_callback>`, `<end_of_plan>`). `build_context` sets `response_mode="plan_execute"` when the agent has `planning_config.enabled`.

---

## Non-negotiable constraints

These are from `CLAUDE.md` and `docs/architecture/adr-planning.md` and must not be violated:

1. **No silent JSON repair.** Invalid `submit_plan` → `ValueError`, not coercion. Fix upstream (prompts, `response_format`) not downstream.
2. **`amend_plan` is reserved.** The driver raises `NotImplementedError`. Do not implement it without a dedicated FEAT.
3. **Lenient ref resolution only.** `ref_resolution: strict` is accepted by the parser but raises `ValueError` ("reserved for a future release"). Do not enable it without a FEAT.
4. **`reflect_after_each_batch: true` is reserved.** Same — parser accepts, raises `ValueError`. Do not enable without FEAT #73.
5. **`planning_active` gate.** `submit_plan`, `amend_plan`, `continue_plan` are only valid when `planning_active=True` is passed to `from_model_response`. Non-planning agents must never see these kinds.
6. **Zero impact on existing agents.** Any agent without `planning: enabled: true` must behave identically to before.

---

## Running the tests

```bash
# All planning tests
pytest tests/planning/ tests/test_turn_driver.py -v

# Safety caps specifically
pytest tests/planning/test_planning_turn_driver_safety.py -v

# Full suite (20 pre-existing DIAL-driver failures are expected and unrelated)
pytest -q
```

---

## File map

```
src/agent_framework/
  agents/
    turn_driver.py               # TurnDriver protocol + StandardTurnDriver
    agent_decision.py            # Extended with submit_plan/amend_plan/continue_plan
    agent.py                     # planning_config field; _select_turn_driver; build_context wiring
    agent_run.py                 # plan_state: PlanState | None = None
    system.plan_execute.md       # Planning system prompt template
  planning/
    __init__.py
    config.py                    # PlanningConfig
    plan_state.py                # PlanStep, CompletedStep, PlanState
    step_reference.py            # {{token}} resolver
    turn_driver.py               # PlanningTurnDriver (core)
  model.py                       # plan_execute response_mode added

tests/
  planning/
    test_config.py
    test_decisions.py
    test_plan_state.py
    test_step_reference.py
    test_planning_turn_driver.py          # happy-path, parallel, replan
    test_planning_turn_driver_callbacks.py  # model-bound callback steps
    test_planning_turn_driver_safety.py     # caps, validation recovery
    test_system_plan_execute.py
  test_turn_driver.py

docs/
  architecture/adr-planning.md    # Full design ADR (authoritative)
  research/planning_agents/        # Research that informed the design
    00_synthesis.md               # Start here
    01_claude_code.md
    02_nano_claude_code.md
    03_langgraph.md
    04_microsoft_agent_framework.md
    05_other_frameworks.md
  planning-implementation-status.md  # This file
```

---

## Next steps for the incoming agent

1. **FEAT #67 — player_controller integration**
   - Open `agent-adventure/agents/player_controller.md` (separate repo or directory).
   - Add `planning: enabled: true` to its frontmatter (plus any config tuning needed).
   - Run the existing evaluator suite: `python -m agent_framework --evaluate path/to/cases.xml` or use the evaluator web UI.
   - Compare pass rate against baseline (must not regress).
   - See ADR §16 for the full integration checklist.

2. **Issue #74 — Documentation review**
   - Read `docs/architecture/adr-planning.md` end-to-end.
   - Verify it has a table of contents and a coherent reading narrative.
   - Update the References section (§20) to point to `docs/research/planning_agents/` (the `research/` folder moved there).
   - Commit any documentation fixes.
