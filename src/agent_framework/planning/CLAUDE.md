# agent_framework/planning — planning agent support

## Source tree

```
planning/
├── turn_driver.py   PlanningTurnDriver — PLAN/EXECUTE/REFLECT state machine
├── plan_state.py    PlanState — tracks step status, results, token refs
├── step_reference.py StepReferenceResolver — resolves {{step_id.path}} tokens
└── config.py        PlanningConfig dataclass (max_steps, parallel_execution, …)
```

## Planning agent loop (PlanningTurnDriver)

Activated when an agent's `.json` sidecar has `"planning": {"enabled": true}`. The outer `Agent.run` loop is unchanged; `PlanningTurnDriver` replaces `StandardTurnDriver` for each iteration.

```
run_turn phases (one phase per outer loop iteration):

  PLAN phase     — no plan in PlanState yet
    model call → expect submit_plan decision
    validate step graph (no cycles, token refs resolvable)
    emit runtime.audit.named_event {type: plan_updated, is_initial: true}
    transition to EXECUTE

  EXECUTE phase  — ready batch (steps with satisfied deps) available
    _select_ready_batch(plan_state) → batch of parallel-ready steps
    _dispatch_parallel_batch(batch) → ThreadPoolExecutor for parallel steps
      each step: call_tool or call_subagent, store result in plan_state
    _resolve_step_parameters with {{step_id.path}} token substitution
    inject reminder (completed steps + next ready batch) into run context
    if all done → transition to REFLECT with end_of_plan=True

  REFLECT phase  — no ready steps (waiting on model)
    model call → expect continue_plan or final_message
    continue_plan: update plan, emit plan_updated (is_initial: false), back to EXECUTE
    final_message: return AgentResult
```
