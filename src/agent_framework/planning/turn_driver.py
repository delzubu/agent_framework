"""PlanningTurnDriver — the core plan/execute/reflect loop.

Implements the TurnDriver protocol for planning-enabled agents. Each
run_turn call dispatches one phase:

  PLAN    — no plan yet; call model, expect submit_plan.
  EXECUTE — ready batch available; dispatch steps, inject reminder.
  REFLECT — no ready batch; call model, expect continue_plan or final_message.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait as _futures_wait
from typing import TYPE_CHECKING, Any

from agent_framework.agents.agent_decision import AgentDecision
from agent_framework.agents.turn_driver import BaseTurnDriver
from agent_framework.planning.plan_state import CompletedStep, PlanState, PlanStep, plan_step_to_dict
from agent_framework.planning.step_reference import resolve as _default_resolve

if TYPE_CHECKING:
    from agent_framework.agents.agent import Agent
    from agent_framework.agents.agent_result import AgentResult
    from agent_framework.agents.agent_run import AgentRun
    from agent_framework.agents.turn_driver import TurnDriver
    from agent_framework.planning.config import PlanningConfig

_LOGGER = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r'\{\{([^}]+?)\}\}')


def _extract_token_roots(value: Any) -> set[str]:
    """Recursively collect {{token}} root names (everything before the first dot) from a value."""
    roots: set[str] = set()
    if isinstance(value, str):
        for m in _TOKEN_RE.finditer(value):
            token = m.group(1).strip()
            roots.add(token.split('.')[0])
    elif isinstance(value, dict):
        for v in value.values():
            roots.update(_extract_token_roots(v))
    elif isinstance(value, (list, tuple)):
        for item in value:
            roots.update(_extract_token_roots(item))
    return roots


def _display_step_result(result: Any) -> Any:
    """Convert a step result to a display-friendly form for the <step_results> reminder.

    Subagent results are stored as dicts (message/response/status) for token
    resolution. For display we re-render them through the subagent envelope so
    the model sees the familiar <subagent_result> format.
    Plain strings longer than 200 characters are truncated.
    """
    if isinstance(result, dict) and "message" in result and "status" in result:
        from agent_framework.agents.result_envelope import render_subagent_envelope
        return render_subagent_envelope(
            message=result.get("message", "") or "",
            response=result.get("response"),
        )
    if isinstance(result, str) and len(result) > 200:
        return result[:200]
    return result


def _select_ready_batch(
    plan_state: PlanState,
    *,
    parallel_execution: bool,
) -> list[PlanStep]:
    """Return steps whose dependencies are all completed and not yet dispatched.

    Uses step_results (not completed_steps) as the "already dispatched" guard
    so that pending callback steps are skipped without being re-dispatched.
    If parallel_execution is False, returns at most one step.
    """
    dispatched_ids: set[str] = set(plan_state.step_results.keys())
    completed_ids: set[str] = {c.step_id for c in plan_state.completed_steps}
    ready: list[PlanStep] = []
    for step in plan_state.plan:
        if step.id in dispatched_ids:
            continue
        if all(dep in completed_ids for dep in step.depends_on):
            ready.append(step)
    if not parallel_execution:
        return ready[:1]
    return ready


def _all_steps_done(plan_state: PlanState) -> bool:
    completed_ids: set[str] = {c.step_id for c in plan_state.completed_steps}
    return all(step.id in completed_ids for step in plan_state.plan)


def _resolve_step_parameters(
    step: PlanStep,
    plan_state: PlanState,
    run: "AgentRun",
    host: Any,
) -> dict[str, Any]:
    """Resolve {{token}} references in step parameters."""
    resolver = getattr(host, "step_ref_resolver", None)
    kwargs = dict(
        invocation_parameters=dict(run.parameter_values),
        step_results=dict(plan_state.step_results),
        run_id=run.run_id,
        agent_id="",
        step_id=step.id,
    )
    if resolver is not None:
        return resolver.resolve(dict(step.parameters), **kwargs)
    return _default_resolve(dict(step.parameters), **kwargs)


def _inject_reminder(run: "AgentRun", plan_state: PlanState, *, end_of_plan: bool = False) -> None:
    """Append a <system_reminder> user message to the conversation."""
    completed_ids = {c.step_id for c in plan_state.completed_steps}
    pending_ids = [s.id for s in plan_state.plan if s.id not in completed_ids]

    plan_summary = json.dumps(
        [{"id": s.id, "kind": s.kind, "status": "done" if s.id in completed_ids else "pending"}
         for s in plan_state.plan],
        indent=2,
    )
    results_summary = json.dumps(
        {k: _display_step_result(v) for k, v in plan_state.step_results.items()},
        indent=2,
        default=str,
    )
    end_tag = "<end_of_plan>true</end_of_plan>\n" if end_of_plan else ""
    pending_tag = (
        f"<pending_steps>{json.dumps(pending_ids)}</pending_steps>\n" if pending_ids else ""
    )
    # Include callback reminder when a step is awaiting model resolution.
    callback_tag = ""
    if plan_state.pending_callback_step_id:
        pending_step = next(
            (s for s in plan_state.plan if s.id == plan_state.pending_callback_step_id), None
        )
        intent = (pending_step.callback_intent or "information_request") if pending_step else "information_request"
        callback_tag = (
            f"<pending_callback step_id=\"{plan_state.pending_callback_step_id}\" "
            f"intent=\"{intent}\">"
            f"A step is awaiting your resolution. "
            f"Emit continue_plan with parameters.resolution to answer, or submit_plan to replan."
            f"</pending_callback>\n"
        )
    reminder = (
        f"<system_reminder>\n"
        f"<plan_state revision=\"{plan_state.plan_revision}\">\n{plan_summary}\n</plan_state>\n"
        f"<step_results>\n{results_summary}\n</step_results>\n"
        f"{callback_tag}"
        f"{pending_tag}"
        f"{end_tag}"
        f"</system_reminder>"
    )
    run.conversation_messages.append({"role": "user", "content": reminder})
    run.prompt_fragments.append(reminder)


def _inject_cap_reminder(run: "AgentRun", *, cap: str, detail: str) -> None:
    """Append a safety-cap exceeded reminder to the conversation."""
    reminder = (
        f"<system_reminder>\n"
        f"<safety_cap_exceeded cap=\"{cap}\">{detail}</safety_cap_exceeded>\n"
        f"</system_reminder>"
    )
    run.conversation_messages.append({"role": "user", "content": reminder})
    run.prompt_fragments.append(reminder)


def _emit_plan_updated(
    run: "AgentRun",
    agent: "Agent",
    *,
    is_initial: bool,
    previous_plan: "tuple[PlanStep, ...] | None",
) -> None:
    """Emit a plan_updated named event after PlanState.plan has been mutated."""
    from agent_framework.agent_event_publisher import agent_events

    plan_state = run.plan_state
    new_ids = {s.id for s in plan_state.plan}
    prev_ids = {s.id for s in previous_plan} if previous_plan else set()
    agent_events.audit_named_event(
        run_id=run.run_id,
        agent_id=agent.agent_id,
        event={
            "type": "plan_updated",
            "is_initial": is_initial,
            "plan_revision": plan_state.plan_revision,
            "step_count": len(plan_state.plan),
            "added_step_ids": sorted(new_ids - prev_ids),
            "dropped_step_ids": sorted(prev_ids - new_ids),
            "plan": [plan_step_to_dict(s) for s in plan_state.plan],
        },
    )


def _dispatch_step(
    step: PlanStep,
    *,
    agent: "Agent",
    host: Any,
    run: "AgentRun",
    caller_id: str | None,
    plan_state: PlanState,
) -> Any:
    """Execute one plan step synchronously; store result in plan_state."""
    started_at = time.time()
    error: str | None = None
    result: Any = None

    params = _resolve_step_parameters(step, plan_state, run, host)

    _LOGGER.debug(
        "Planning: dispatching step %r (kind=%s) for agent %s",
        step.id, step.kind, agent.agent_id,
    )

    try:
        if step.kind == "call_tool":
            result = host.execute_tool(step.tool_name, params)

        elif step.kind == "call_subagent":
            agent_result = host.call_subagent(
                caller=agent,
                callee_id=step.subagent_id,
                parameters=params,
                parent_run_id=run.run_id,
            )
            # Store as a structured dict so {{step.response.field}} and
            # {{step.message}} tokens resolve correctly via the step reference
            # resolver. The rendered envelope (for prompt injection) is produced
            # lazily in _inject_reminder via _render_subagent_result_for_display.
            result = {
                "message": getattr(agent_result, "message", "") or "",
                "response": getattr(agent_result, "response", None),
                "status": getattr(agent_result, "status", "completed"),
            }

        elif step.kind == "invoke_skill":
            # Delegate to the agent's skill invocation handler.
            synthetic = AgentDecision(
                kind="invoke_skill",
                skill_name=step.skill_name,
                parameters=params,
                message=step.message,
            )
            agent.dispatch_decision(
                host=host, run=run, decision=synthetic, caller_id=caller_id
            )
            result = f"skill:{step.skill_name}:invoked"

        elif step.kind == "callback":
            # Model-bound callback: pause plan and let reflect resolve it.
            # Store a pending sentinel in step_results (prevents re-dispatch)
            # but do NOT add to completed_steps (so _all_steps_done stays False).
            plan_state.step_results[step.id] = {
                "_callback_pending": True,
                "intent": step.callback_intent or "information_request",
                "step_id": step.id,
            }
            plan_state.pending_callback_step_id = step.id
            _LOGGER.info(
                "Planning: step %r is a model-bound callback (intent=%s)",
                step.id, step.callback_intent,
            )
            return plan_state.step_results[step.id]

        else:
            raise ValueError(f"Unsupported step kind {step.kind!r} for step {step.id!r}")

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result = {"error": error, "step_id": step.id}
        _LOGGER.warning("Planning: step %r failed: %s", step.id, error)

    finished_at = time.time()
    plan_state.step_results[step.id] = result
    plan_state.completed_steps.append(
        CompletedStep(
            step_id=step.id,
            step=step,
            result=result,
            started_at=started_at,
            finished_at=finished_at,
            plan_revision_at_start=plan_state.plan_revision,
            error=error,
        )
    )
    plan_state.total_steps_executed += 1

    _LOGGER.debug(
        "Planning: step %r done in %.2fs (error=%s)",
        step.id, finished_at - started_at, error,
    )
    return result


def _dispatch_parallel_batch(
    batch: list[PlanStep],
    *,
    agent: "Agent",
    host: Any,
    run: "AgentRun",
    caller_id: str | None,
    plan_state: PlanState,
    timeout_seconds: float,
) -> None:
    """Dispatch a batch of steps in parallel via a thread pool."""
    with ThreadPoolExecutor(max_workers=len(batch)) as executor:
        futures = {
            executor.submit(
                _dispatch_step,
                step,
                agent=agent,
                host=host,
                run=run,
                caller_id=caller_id,
                plan_state=plan_state,
            ): step
            for step in batch
        }
        done, not_done = _futures_wait(
            list(futures),
            timeout=timeout_seconds if timeout_seconds > 0 else None,
        )
        for fut in not_done:
            step = futures[fut]
            plan_state.step_results[step.id] = {"error": "timed_out", "step_id": step.id}
            plan_state.completed_steps.append(
                CompletedStep(
                    step_id=step.id,
                    step=step,
                    result={"error": "timed_out"},
                    started_at=time.time(),
                    finished_at=time.time(),
                    plan_revision_at_start=plan_state.plan_revision,
                    error="timed_out",
                )
            )
            plan_state.total_steps_executed += 1
            _LOGGER.warning("Planning: step %r timed out", step.id)
        for fut in done:
            if fut.exception() is not None:
                _LOGGER.warning(
                    "Planning: step %r raised: %s", futures[fut].id, fut.exception()
                )


class PlanningTurnDriver(BaseTurnDriver):
    """TurnDriver for planning-enabled agents.

    Drives the plan → execute → reflect lifecycle. Each run_turn call
    advances the state machine by one phase.
    """

    def __init__(self, config: "PlanningConfig") -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Semantic validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_submit_plan(
        decision: AgentDecision,
        plan_state: "PlanState | None",
        run: "AgentRun",
    ) -> "str | None":
        """Return an error message if the submit_plan is semantically invalid.

        Checks:
        - {{token}} roots must resolve to a step in this plan, a completed step, or a parameter.
        - New step IDs must not collide with already-completed step IDs (immutable history).
        """
        new_step_ids = {s.id for s in decision.plan}
        completed_ids = (
            {c.step_id for c in plan_state.completed_steps}
            if plan_state is not None else set()
        )
        param_names = set(run.parameter_values.keys())
        valid_roots = new_step_ids | completed_ids | param_names

        errors: list[str] = []

        # Check for step ID collisions with completed history.
        colliding = sorted(new_step_ids & completed_ids)
        if colliding:
            errors.append(
                "The following step IDs in the new plan collide with already-completed steps "
                f"(completed steps are immutable history and cannot be redefined): {colliding}. "
                "Choose different IDs for the new steps. Reference the completed results via "
                "{{step_id}} in parameters instead."
            )

        # Check for unresolvable {{token}} references.
        bad_tokens: list[str] = []
        for step in decision.plan:
            for root in sorted(_extract_token_roots(step.parameters)):
                if root not in valid_roots:
                    bad_tokens.append(
                        f"  - step {step.id!r}: {{{{root}}}} — {root!r} is not a step id "
                        "in this plan, a completed step id, or an invocation parameter name"
                    )
        if bad_tokens:
            lines = ["The submit_plan contains {{token}} references that cannot be resolved:"]
            lines.extend(bad_tokens)
            lines.append("")
            lines.append("Valid token roots (must match exactly):")
            lines.append(f"  Step IDs in this plan: {sorted(new_step_ids)}")
            if completed_ids:
                lines.append(f"  Completed step IDs from prior plan: {sorted(completed_ids)}")
            lines.append(f"  Invocation parameter names: {sorted(param_names)}")
            lines.append("Correct the token names to exactly match one of the above and resubmit.")
            errors.append("\n".join(lines))

        return "\n\n".join(errors) if errors else None

    @staticmethod
    def _validate_reflect_callback(
        decision: AgentDecision,
        plan_state: "PlanState",
        end_of_plan: bool,
    ) -> "str | None":
        """Return error if model emits callback at end_of_plan when there are failed steps."""
        if not end_of_plan or decision.kind != "callback":
            return None

        failed = [
            (sid, r.get("error", "unknown error"))
            for sid, r in plan_state.step_results.items()
            if isinstance(r, dict) and "error" in r
        ]
        if not failed:
            return None

        failed_summary = "; ".join(f"{sid!r}: {err}" for sid, err in failed[:5])
        return (
            f"You emitted 'callback' at end_of_plan, but the plan completed with step execution "
            f"errors: {failed_summary}. "
            "Step execution errors are system results — do not escalate them to the user as if "
            "they need to provide clarification. "
            "Instead: emit 'final_message' to report the plan outcome (summarise which steps "
            "failed and why), or emit 'submit_plan' with corrected steps to retry the failed work."
        )

    def _handle_semantic_failure(
        self,
        error: str,
        *,
        agent: "Agent",
        host: Any,
        run: "AgentRun",
        caller_id: str | None,
    ) -> "AgentResult | None":
        """Inject a correction reminder; abort if this is the second consecutive semantic failure."""
        run.planning_semantic_failures += 1
        _LOGGER.error(
            "Planning semantic validation failed for agent %s (attempt %d/2): %s",
            agent.agent_id, run.planning_semantic_failures, error,
        )
        if run.planning_semantic_failures >= 2:
            run.planning_semantic_failures = 0
            return self._emit_safety_cap_callback(
                agent=agent,
                host=host,
                run=run,
                caller_id=caller_id,
                cap="planning_semantic_validation",
                detail=f"Two consecutive planning semantic validation failures. Last error: {error}",
            )
        reminder = (
            "<system_reminder>\n"
            f"<planning_validation_error>{error}</planning_validation_error>\n"
            "Your decision did not conform to the specification. Correct the issue described "
            "above and resubmit, matching the specification exactly.\n"
            "</system_reminder>"
        )
        run.conversation_messages.append({"role": "user", "content": reminder})
        run.prompt_fragments.append(reminder)
        return None

    def run_turn(
        self,
        *,
        agent: "Agent",
        host: Any,
        run: "AgentRun",
        caller_id: str | None,
    ) -> "AgentResult | None":
        """Advance the planning state machine by one phase.

        Returns an AgentResult to end the loop, or None to continue.
        """
        plan_state = run.plan_state

        if plan_state is None:
            return self._plan_phase(agent, host, run, caller_id)

        ready_batch = _select_ready_batch(
            plan_state, parallel_execution=self.config.parallel_execution
        )

        if ready_batch:
            return self._execute_phase(agent, host, run, caller_id, ready_batch, plan_state)
        else:
            return self._reflect_phase(agent, host, run, caller_id, plan_state)

    # ------------------------------------------------------------------
    # Phases
    # ------------------------------------------------------------------

    def _plan_phase(
        self,
        agent: "Agent",
        host: Any,
        run: "AgentRun",
        caller_id: str | None,
    ) -> "AgentResult | None":
        """No plan yet — call model, expect submit_plan."""
        _LOGGER.debug("Planning: entering PLAN phase for agent %s", agent.agent_id)

        result = self._try_decide(agent, host, run, caller_id, planning_active=True)
        if not isinstance(result, AgentDecision):
            return result
        decision = result

        if decision.kind == "submit_plan":
            error = self._validate_submit_plan(decision, plan_state=None, run=run)
            if error is not None:
                return self._handle_semantic_failure(
                    error, agent=agent, host=host, run=run, caller_id=caller_id
                )
            run.planning_semantic_failures = 0
            plan_state = PlanState(plan=decision.plan, plan_revision=1)
            run.plan_state = plan_state
            _LOGGER.info(
                "Planning: plan submitted for agent %s — %d steps",
                agent.agent_id, len(decision.plan),
            )
            _emit_plan_updated(run, agent, is_initial=True, previous_plan=None)
            _inject_reminder(run, plan_state, end_of_plan=False)
            return None

        if decision.kind == "final_message":
            return self._make_result(decision, run)

        # Non-planning decision (call_tool, call_subagent, etc.) — dispatch normally.
        return agent.dispatch_decision(host=host, run=run, decision=decision, caller_id=caller_id)

    def _execute_phase(
        self,
        agent: "Agent",
        host: Any,
        run: "AgentRun",
        caller_id: str | None,
        ready_batch: list[PlanStep],
        plan_state: PlanState,
    ) -> "AgentResult | None":
        """Dispatch the ready batch of steps; inject per-turn reminder."""
        _LOGGER.debug(
            "Planning: EXECUTE phase — dispatching %d step(s): %s",
            len(ready_batch), [s.id for s in ready_batch],
        )

        # max_steps safety cap.
        projected = plan_state.total_steps_executed + len(ready_batch)
        max_steps = self.config.max_steps
        if max_steps > 0:
            pct = projected / max_steps
            if pct > 0.8:
                _LOGGER.warning(
                    "Planning: agent %s approaching max_steps cap (%d/%d)",
                    agent.agent_id, projected, max_steps,
                )
        if max_steps > 0 and plan_state.total_steps_executed >= max_steps:
            _LOGGER.error(
                "Planning: agent %s exceeded max_steps=%d (executed=%d)",
                agent.agent_id, max_steps, plan_state.total_steps_executed,
            )
            return self._emit_safety_cap_callback(
                agent=agent,
                host=host,
                run=run,
                caller_id=caller_id,
                cap="max_steps",
                detail=f"max_steps={max_steps} exceeded (executed={plan_state.total_steps_executed})",
            )

        timeout = self.config.step_timeout_seconds

        if len(ready_batch) == 1 or not self.config.parallel_execution:
            for step in ready_batch:
                _dispatch_step(
                    step,
                    agent=agent,
                    host=host,
                    run=run,
                    caller_id=caller_id,
                    plan_state=plan_state,
                )
        else:
            _dispatch_parallel_batch(
                ready_batch,
                agent=agent,
                host=host,
                run=run,
                caller_id=caller_id,
                plan_state=plan_state,
                timeout_seconds=timeout,
            )

        _inject_reminder(run, plan_state, end_of_plan=False)
        return None

    def _reflect_phase(
        self,
        agent: "Agent",
        host: Any,
        run: "AgentRun",
        caller_id: str | None,
        plan_state: PlanState,
    ) -> "AgentResult | None":
        """No ready batch — call model for continuation or final answer."""
        end_of_plan = _all_steps_done(plan_state)
        _LOGGER.debug(
            "Planning: REFLECT phase for agent %s (end_of_plan=%s)",
            agent.agent_id, end_of_plan,
        )
        if end_of_plan:
            _LOGGER.info(
                "Planning: end-of-plan reflect for agent %s — %d steps completed",
                agent.agent_id, len(plan_state.completed_steps),
            )

        _inject_reminder(run, plan_state, end_of_plan=end_of_plan)

        result = self._try_decide(agent, host, run, caller_id, planning_active=True)
        if not isinstance(result, AgentDecision):
            return result
        decision = result

        if decision.kind == "final_message":
            return self._make_result(decision, run)

        if decision.kind == "continue_plan":
            # Guard: continue_plan is invalid when the plan is fully done.
            if end_of_plan and plan_state.pending_callback_step_id is None:
                plan_state.eop_stall_count += 1
                _LOGGER.warning(
                    "Planning: agent %s emitted continue_plan at end_of_plan "
                    "(stall %d/3) — injecting reminder to finalize",
                    agent.agent_id, plan_state.eop_stall_count,
                )
                if plan_state.eop_stall_count >= 3:
                    return self._emit_safety_cap_callback(
                        agent=agent,
                        host=host,
                        run=run,
                        caller_id=caller_id,
                        cap="eop_stall",
                        detail=(
                            "Agent emitted continue_plan 3 times after end_of_plan "
                            "with no pending steps. Expected final_message or submit_plan."
                        ),
                    )
                run.conversation_messages.append({
                    "role": "user",
                    "content": (
                        "<system_reminder>\n"
                        "All plan steps are complete (<end_of_plan> was signalled). "
                        "continue_plan is not valid here. "
                        "Emit final_message to return your result, or submit_plan "
                        "if the results require additional steps.\n"
                        "</system_reminder>"
                    ),
                })
                return None

            # If there's a pending callback step, resolve it now.
            pending_id = plan_state.pending_callback_step_id
            if pending_id is not None:
                resolution = dict(decision.parameters).get("resolution")
                intent = None
                pending_step = next((s for s in plan_state.plan if s.id == pending_id), None)
                if pending_step is not None:
                    intent = pending_step.callback_intent
                resolved_result = {
                    "_callback_resolved": True,
                    "intent": intent or "information_request",
                    "resolution": resolution,
                }
                plan_state.step_results[pending_id] = resolved_result
                plan_state.completed_steps.append(
                    CompletedStep(
                        step_id=pending_id,
                        step=pending_step or plan_state.plan[0],
                        result=resolved_result,
                        started_at=time.time(),
                        finished_at=time.time(),
                        plan_revision_at_start=plan_state.plan_revision,
                    )
                )
                plan_state.total_steps_executed += 1
                plan_state.pending_callback_step_id = None
                _LOGGER.info(
                    "Planning: callback step %r resolved via continue_plan", pending_id
                )
            return None

        if decision.kind == "submit_plan":
            error = self._validate_submit_plan(decision, plan_state=plan_state, run=run)
            if error is not None:
                return self._handle_semantic_failure(
                    error, agent=agent, host=host, run=run, caller_id=caller_id
                )
            run.planning_semantic_failures = 0
            return self._apply_replan(
                decision, agent=agent, host=host, run=run,
                caller_id=caller_id, plan_state=plan_state,
            )

        if decision.kind == "amend_plan":
            raise NotImplementedError(
                "amend_plan is reserved for a future FEAT and is not yet implemented."
            )

        if decision.kind == "callback":
            error = self._validate_reflect_callback(decision, plan_state, end_of_plan)
            if error is not None:
                return self._handle_semantic_failure(
                    error, agent=agent, host=host, run=run, caller_id=caller_id
                )
            run.planning_semantic_failures = 0

        # Non-planning decision — dispatch normally.
        return agent.dispatch_decision(host=host, run=run, decision=decision, caller_id=caller_id)

    def _apply_replan(
        self,
        decision: AgentDecision,
        *,
        agent: "Agent",
        host: Any,
        run: "AgentRun",
        caller_id: str | None,
        plan_state: PlanState,
    ) -> "AgentResult | None":
        """Apply a submit_plan decision as a re-plan, enforcing max_plan_revisions."""
        max_revisions = self.config.max_plan_revisions
        if max_revisions > 0:
            pct = plan_state.plan_revision / max_revisions
            if pct >= 0.8:
                _LOGGER.warning(
                    "Planning: agent %s approaching max_plan_revisions cap (%d/%d)",
                    agent.agent_id, plan_state.plan_revision, max_revisions,
                )
        if max_revisions > 0 and plan_state.plan_revision >= max_revisions:
            _LOGGER.error(
                "Planning: agent %s exceeded max_plan_revisions=%d",
                agent.agent_id, max_revisions,
            )
            return self._emit_safety_cap_callback(
                agent=agent,
                host=host,
                run=run,
                caller_id=caller_id,
                cap="max_plan_revisions",
                detail=f"max_plan_revisions={max_revisions} exceeded",
            )

        # Completed steps are immutable history — never purge them.
        completed_step_ids = {c.step_id for c in plan_state.completed_steps}
        new_step_ids = {s.id for s in decision.plan}

        # Drop only non-completed sentinels (e.g. _callback_pending) that are
        # absent from the new plan.  Completed results must survive for {{token}}
        # resolution by new steps.
        for sid in list(plan_state.step_results):
            if sid not in completed_step_ids and sid not in new_step_ids:
                del plan_state.step_results[sid]

        # Build the merged plan: completed prefix (in execution order) + new pending.
        completed_steps_in_order = tuple(
            cs.step for cs in plan_state.completed_steps
            if cs.step_id in completed_step_ids
        )
        previous_plan = plan_state.plan
        plan_state.plan = completed_steps_in_order + decision.plan
        plan_state.plan_revision += 1
        plan_state.pending_callback_step_id = None
        run.consecutive_validation_failures = 0
        _LOGGER.info(
            "Planning: agent %s re-planned (revision %d) — %d steps",
            agent.agent_id, plan_state.plan_revision, len(decision.plan),
        )
        _emit_plan_updated(run, agent, is_initial=False, previous_plan=previous_plan)
        _inject_reminder(run, plan_state, end_of_plan=False)
        return None


__all__ = ["PlanningTurnDriver"]
