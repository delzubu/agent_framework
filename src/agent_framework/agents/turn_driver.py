"""TurnDriver protocol, BaseTurnDriver, and StandardTurnDriver.

The TurnDriver seam extracts the per-turn loop body from Agent.run, making it
possible to swap in a PlanningTurnDriver without modifying Agent or its dispatch
handlers.
"""

from __future__ import annotations

import logging
from typing import Protocol, TYPE_CHECKING

from .agent_decision import AgentDecision
from .agent_result import AgentResult
from .agent_run import AgentRun

if TYPE_CHECKING:
    from .agent import Agent
    from .agent_host_protocol import AgentHostProtocol

_LOGGER = logging.getLogger(__name__)


class TurnDriver(Protocol):
    """Drives one or more turns of an agent invocation.

    A driver is invoked once per outer loop iteration in Agent.run.
    Returns AgentResult to terminate the run, or None to continue looping.
    The outer loop handles post-agent hooks; the driver is responsible only
    for the per-iteration model+dispatch work.
    """

    def run_turn(
        self,
        *,
        agent: "Agent",
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
    ) -> AgentResult | None: ...


class BaseTurnDriver:
    """Shared helpers for all TurnDriver implementations."""

    @staticmethod
    def _make_result(decision: AgentDecision, run: AgentRun) -> AgentResult:
        """Canonical AgentResult builder for final_message decisions."""
        return AgentResult(
            status="completed",
            message=decision.message,
            response=decision.response,
            decision=decision,
            prompt=run.rendered_prompt,
        )

    @staticmethod
    def _emit_safety_cap_callback(
        *,
        agent: "Agent",
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        cap: str,
        detail: str,
    ) -> AgentResult | None:
        """Terminate execution due to a safety-cap violation.

        Escalates via a synthetic callback when there is a parent caller;
        returns a failed AgentResult directly for top-level runs.
        """
        from agent_framework.planning.turn_driver import _inject_cap_reminder

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
            "Safety cap %r triggered for agent %s — terminating run. %s",
            cap, agent.agent_id, detail,
        )
        return AgentResult(
            status="failed",
            message=message,
            prompt=run.rendered_prompt,
        )

    def _try_decide(
        self,
        agent: "Agent",
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
        *,
        planning_active: bool = False,
    ) -> AgentDecision | AgentResult | None:
        """Call agent.decide and handle ValueError with run-scoped retry counting.

        Returns:
        - AgentDecision on success (resets the failure counter).
        - None on first validation failure (injects error reminder; caller returns None).
        - AgentResult or None from _emit_safety_cap_callback on second consecutive failure.
        """
        context = agent.build_context(host=host, run=run)
        try:
            decision = agent.decide(host=host, run=run, context=context, planning_active=planning_active)
            run.consecutive_validation_failures = 0
            return decision
        except ValueError as exc:
            run.consecutive_validation_failures += 1
            _LOGGER.error(
                "Plan validation error for agent %s (consecutive=%d): %s",
                agent.agent_id, run.consecutive_validation_failures, exc,
                exc_info=True,
            )
            if run.consecutive_validation_failures >= 2:
                run.consecutive_validation_failures = 0
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


class StandardTurnDriver(BaseTurnDriver):
    """Faithful extraction of the existing per-turn loop body.

    Behavior is identical to the inline loop in Agent.run prior to this
    refactor. Used for all agents that do not enable planning.
    """

    def run_turn(
        self,
        *,
        agent: "Agent",
        host: "AgentHostProtocol",
        run: AgentRun,
        caller_id: str | None,
    ) -> AgentResult | None:
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


__all__ = ["TurnDriver", "BaseTurnDriver", "StandardTurnDriver"]
