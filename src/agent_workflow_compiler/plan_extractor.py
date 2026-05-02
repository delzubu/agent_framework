"""Extract PlanCompilation from an ordered list of AuditEvents for a single run."""
from __future__ import annotations

from typing import Any

from .models import AuditEvent, CompiledStep, PlanCompilation, ReplanCheckpoint


def extract_plan(events: list[AuditEvent], run_id: str) -> PlanCompilation:
    """Build a PlanCompilation from the audit events for *run_id*.

    The final plan is the last ``plan_updated`` event's plan array.
    ReplanCheckpoints are created for every non-initial ``plan_updated``.

    Args:
        events: All events from the log (not pre-filtered).
        run_id: The exact run_id of the planning call to compile.
    """
    # Filter to this run's events (including nested child-agent events, which
    # have longer run_ids that start with the parent run_id).
    run_events = [e for e in events if (e.context.get("run_id") or "").startswith(run_id)]
    # But plan_updated events belong specifically to the planning agent's run_id.
    planner_events = [e for e in events if e.context.get("run_id") == run_id]

    # ------------------------------------------------------------------
    # Invocation parameters + prompt
    # ------------------------------------------------------------------
    invocation_parameters: dict[str, Any] = {}
    invocation_prompt: str = ""
    source_agent_id: str = ""

    for e in planner_events:
        if e.kind == "runtime.agent_started":
            invocation_parameters = dict(e.payload.get("parameters") or {})
            invocation_prompt = str(e.payload.get("prompt") or "")
        if e.kind == "runtime.audit.agent_call_started":
            source_agent_id = source_agent_id or str(e.payload.get("agent_name") or "")

    # ------------------------------------------------------------------
    # Collect plan_updated events in order
    # ------------------------------------------------------------------
    plan_updates: list[dict[str, Any]] = []
    for e in planner_events:
        if e.kind == "runtime.audit.named_event":
            ev = e.payload.get("event", {})
            if ev.get("type") == "plan_updated":
                plan_updates.append(ev)

    if not plan_updates:
        raise ValueError(f"No plan_updated events found for run_id={run_id!r}.")

    # ------------------------------------------------------------------
    # Final plan = last plan_updated's plan array
    # ------------------------------------------------------------------
    final_plan_raw: list[dict[str, Any]] = plan_updates[-1].get("plan", [])

    # ------------------------------------------------------------------
    # Compute linear step order based on depends_on / execution order
    # ------------------------------------------------------------------
    # Use a topological sort respecting the plan's depends_on declarations.
    final_steps = _ordered_compiled_steps(final_plan_raw)

    # ------------------------------------------------------------------
    # Replan checkpoints for each non-initial plan_updated
    # ------------------------------------------------------------------
    replan_checkpoints: list[ReplanCheckpoint] = []
    completed_step_ids: set[str] = set()

    for rev_idx, plan_update in enumerate(plan_updates):
        added = list(plan_update.get("added_step_ids", []))
        all_step_ids_in_this_plan = {s["id"] for s in plan_update.get("plan", [])}
        is_initial = bool(plan_update.get("is_initial", True))

        if is_initial:
            # After the initial plan, the "completed" steps are those that were
            # in the initial plan and not in added_step_ids of subsequent replans.
            completed_step_ids = all_step_ids_in_this_plan
        else:
            # The steps that were completed just before this replan are those
            # present in the previous plan revision but NOT in added_step_ids.
            completed_at_replan = all_step_ids_in_this_plan - set(added)
            # The trigger step is the last completed step in the previous plan
            # (the one whose result prompted the replan). We use the dependency
            # order to identify it: it's the step with the most dependencies that
            # is still in completed_at_replan.
            trigger_step = _find_last_completed_step(
                plan_update.get("plan", []), completed_at_replan, added
            )
            replan_checkpoints.append(
                ReplanCheckpoint(
                    after_step_id=trigger_step,
                    trigger_message=_infer_trigger_message(plan_update),
                    plan_revision=int(plan_update.get("plan_revision", rev_idx + 1)),
                    added_step_ids=added,
                )
            )

    return PlanCompilation(
        source_run_id=run_id,
        source_agent_id=source_agent_id,
        invocation_parameters=invocation_parameters,
        invocation_prompt=invocation_prompt,
        final_steps=final_steps,
        replan_checkpoints=replan_checkpoints,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ordered_compiled_steps(plan_raw: list[dict[str, Any]]) -> list[CompiledStep]:
    """Topological sort of plan steps, then assign next_step pointers."""
    id_to_raw = {s["id"]: s for s in plan_raw}
    deps: dict[str, list[str]] = {s["id"]: list(s.get("depends_on") or []) for s in plan_raw}

    # Kahn's algorithm
    in_degree: dict[str, int] = {sid: 0 for sid in id_to_raw}
    for sid, dep_list in deps.items():
        for dep in dep_list:
            if dep in in_degree:
                in_degree[sid] = in_degree.get(sid, 0) + 1

    # Recompute: count how many unresolved deps each step has
    in_degree = {sid: len(dep_list) for sid, dep_list in deps.items()}
    ready = [sid for sid, cnt in in_degree.items() if cnt == 0]
    # Stable sort by original plan order for determinism
    plan_order = {s["id"]: i for i, s in enumerate(plan_raw)}
    ready.sort(key=lambda sid: plan_order.get(sid, 0))

    ordered: list[str] = []
    while ready:
        sid = ready.pop(0)
        ordered.append(sid)
        for other_sid, dep_list in deps.items():
            if sid in dep_list:
                in_degree[other_sid] -= 1
                if in_degree[other_sid] == 0:
                    ready.append(other_sid)
                    ready.sort(key=lambda s: plan_order.get(s, 0))

    if len(ordered) != len(id_to_raw):
        # Cycle or unresolved deps — fall back to plan order
        ordered = [s["id"] for s in plan_raw]

    compiled: list[CompiledStep] = []
    for i, sid in enumerate(ordered):
        raw = id_to_raw[sid]
        next_step = ordered[i + 1] if i + 1 < len(ordered) else None
        compiled.append(
            CompiledStep(
                step_id=sid,
                kind=str(raw.get("kind", "call_tool")),
                tool_name=raw.get("tool_name"),
                subagent_id=raw.get("subagent_id"),
                skill_name=raw.get("skill_name"),
                parameters=dict(raw.get("parameters") or {}),
                depends_on=list(raw.get("depends_on") or []),
                next_step=next_step,
            )
        )
    return compiled


def _find_last_completed_step(
    plan: list[dict[str, Any]],
    completed_ids: set[str],
    added_ids: list[str],
) -> str:
    """Find the last completed step in dependency order before the replan.

    The trigger step is the completed step that no other completed step depends on,
    i.e. the "leaf" of the completed subgraph.
    """
    if not completed_ids:
        return ""

    # Build reverse-dep map within completed steps
    deps_in_completed: dict[str, set[str]] = {sid: set() for sid in completed_ids}
    for raw_step in plan:
        sid = raw_step["id"]
        if sid not in completed_ids:
            continue
        for dep in raw_step.get("depends_on") or []:
            if dep in completed_ids:
                deps_in_completed[sid].add(dep)

    # The trigger step is the one with the most ancestors in completed_ids
    # (deepest in the dependency chain)
    def depth(sid: str, visited: set[str] | None = None) -> int:
        if visited is None:
            visited = set()
        if sid in visited:
            return 0
        visited.add(sid)
        if not deps_in_completed.get(sid):
            return 0
        return 1 + max(depth(d, visited) for d in deps_in_completed[sid])

    return max(completed_ids, key=depth)


def _infer_trigger_message(plan_update: dict[str, Any]) -> str:
    """Extract a human-readable message from the replan event."""
    msg = str(plan_update.get("message") or "")
    added = plan_update.get("added_step_ids", [])
    if msg:
        return msg
    if added:
        return f"Added steps after replan: {', '.join(added)}"
    return "Replan with no message."
