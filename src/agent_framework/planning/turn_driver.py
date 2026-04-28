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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait as _futures_wait
from typing import TYPE_CHECKING, Any

from agent_framework.agents.agent import _subagent_result_payload
from agent_framework.agents.agent_decision import AgentDecision
from agent_framework.planning.plan_state import CompletedStep, PlanState, PlanStep, plan_step_to_dict
from agent_framework.planning.step_reference import resolve as _default_resolve

if TYPE_CHECKING:
    from agent_framework.agents.agent import Agent
    from agent_framework.agents.agent_result import AgentResult
    from agent_framework.agents.agent_run import AgentRun
    from agent_framework.agents.turn_driver import TurnDriver
    from agent_framework.planning.config import PlanningConfig

_LOGGER = logging.getLogger(__name__)


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
        {k: (v[:200] if isinstance(v, str) and len(v) > 200 else v)
         for k, v in plan_state.step_results.items()},
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
            result = _subagent_result_payload(
                agent_result.message,
                agent_result.parameters,
                agent_result.parameters_injection,
            )

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


class PlanningTurnDriver:
    """TurnDriver for planning-enabled agents.

    Drives the plan → execute → reflect lifecycle. Each run_turn call
    advances the state machine by one phase.
    """

    def __init__(self, config: "PlanningConfig") -> None:
        self.config = config
        # Tracks consecutive plan-phase validation failures per run_id.
        # Used because plan_state doesn't exist until the first valid submit_plan.
        self._plan_phase_failures: dict[str, int] = {}

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

        context = agent.build_context(host=host, run=run)
        try:
            decision = agent.decide(host=host, run=run, context=context, planning_active=True)
        except ValueError as exc:
            run_id = run.run_id
            failures = self._plan_phase_failures.get(run_id, 0) + 1
            self._plan_phase_failures[run_id] = failures
            _LOGGER.error(
                "Planning: plan validation error for agent %s (consecutive=%d): %s",
                agent.agent_id, failures, exc,
                exc_info=True,
            )
            if failures >= 2:
                self._plan_phase_failures.pop(run_id, None)
                return self._emit_safety_cap_callback(
                    agent=agent,
                    host=host,
                    run=run,
                    caller_id=caller_id,
                    cap="consecutive_validation_failures",
                    detail=f"Two consecutive plan validation errors: {exc}",
                )
            error_reminder = (
                f"<system_reminder>\n"
                f"<plan_validation_error>{exc}</plan_validation_error>\n"
                f"The plan you submitted was invalid. Please submit a corrected plan.\n"
                f"</system_reminder>"
            )
            run.conversation_messages.append({"role": "user", "content": error_reminder})
            run.prompt_fragments.append(error_reminder)
            return None

        # Clear per-run failure counter on successful model call.
        self._plan_phase_failures.pop(run.run_id, None)

        if decision.kind == "submit_plan":
            # decision.plan is already validated by from_model_response — no extra validation needed.
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

        context = agent.build_context(host=host, run=run)
        try:
            decision = agent.decide(host=host, run=run, context=context, planning_active=True)
        except ValueError as exc:
            # Plan validation failed (invalid submit_plan from the model).
            plan_state.consecutive_validation_failures += 1
            _LOGGER.error(
                "Planning: plan validation error for agent %s (consecutive=%d): %s",
                agent.agent_id, plan_state.consecutive_validation_failures, exc,
                exc_info=True,
            )
            if plan_state.consecutive_validation_failures >= 2:
                return self._emit_safety_cap_callback(
                    agent=agent,
                    host=host,
                    run=run,
                    caller_id=caller_id,
                    cap="consecutive_validation_failures",
                    detail=f"Two consecutive plan validation errors: {exc}",
                )
            # First failure: inject error reminder and let model retry next reflect.
            error_reminder = (
                f"<system_reminder>\n"
                f"<plan_validation_error>{exc}</plan_validation_error>\n"
                f"The plan you submitted was invalid. Please submit a corrected plan.\n"
                f"</system_reminder>"
            )
            run.conversation_messages.append({"role": "user", "content": error_reminder})
            run.prompt_fragments.append(error_reminder)
            return None

        if decision.kind == "final_message":
            return self._make_result(decision, run)

        if decision.kind == "continue_plan":
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
            return self._apply_replan(
                decision, agent=agent, host=host, run=run,
                caller_id=caller_id, plan_state=plan_state,
            )

        if decision.kind == "amend_plan":
            raise NotImplementedError(
                "amend_plan is reserved for a future FEAT and is not yet implemented."
            )

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

        previous_plan = plan_state.plan
        new_step_ids = {s.id for s in decision.plan}
        for dropped_id in list(plan_state.step_results):
            if dropped_id not in new_step_ids:
                del plan_state.step_results[dropped_id]
        # Remove completed_steps for dropped step ids.
        plan_state.completed_steps[:] = [
            cs for cs in plan_state.completed_steps if cs.step_id in new_step_ids
        ]
        plan_state.plan = decision.plan
        plan_state.plan_revision += 1
        plan_state.pending_callback_step_id = None
        plan_state.consecutive_validation_failures = 0
        _LOGGER.info(
            "Planning: agent %s re-planned (revision %d) — %d steps",
            agent.agent_id, plan_state.plan_revision, len(decision.plan),
        )
        _emit_plan_updated(run, agent, is_initial=False, previous_plan=previous_plan)
        _inject_reminder(run, plan_state, end_of_plan=False)
        return None

    def _emit_safety_cap_callback(
        self,
        *,
        agent: "Agent",
        host: Any,
        run: "AgentRun",
        caller_id: str | None,
        cap: str,
        detail: str,
    ) -> "AgentResult | None":
        """Terminate execution due to a safety-cap violation.

        When there is a parent caller, escalate via a synthetic callback so the
        caller can handle the failure. When there is no caller (top-level run),
        return a failed AgentResult directly — calling handle_callback in that
        case would block waiting for user input.
        """
        from agent_framework.agents.agent_result import AgentResult

        message = (
            f"Planning safety cap exceeded: {detail}. "
            "The plan execution has been halted. Please review the plan configuration."
        )
        _inject_cap_reminder(run, cap=cap, detail=detail)

        has_real_caller = caller_id is not None and caller_id != "host"
        if has_real_caller:
            synthetic = AgentDecision(
                kind="callback_to_caller",
                callback_intent="execution_recovery",
                message=message,
                parameters={"cap": cap, "detail": detail},
            )
            return agent.handle_callback(
                host=host, run=run, decision=synthetic, caller_id=caller_id
            )

        _LOGGER.error(
            "Planning: safety cap %r triggered for agent %s — terminating run. %s",
            cap, agent.agent_id, detail,
        )
        return AgentResult(
            status="failed",
            message=message,
            prompt=run.rendered_prompt,
        )

    @staticmethod
    def _make_result(decision: AgentDecision, run: "AgentRun") -> "AgentResult":
        from agent_framework.agents.agent_result import AgentResult
        return AgentResult(
            status="completed",
            message=decision.message,
            parameters=decision.parameters if decision.parameters else None,
            decision=decision,
            prompt=run.rendered_prompt,
        )


__all__ = ["PlanningTurnDriver"]
