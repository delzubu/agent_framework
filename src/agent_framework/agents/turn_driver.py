"""TurnDriver protocol and StandardTurnDriver implementation.

The TurnDriver seam extracts the per-turn loop body from Agent.run, making it
possible to swap in a PlanningTurnDriver without modifying Agent or its dispatch
handlers.
"""

from __future__ import annotations

import logging
from typing import Protocol, TYPE_CHECKING

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


class StandardTurnDriver:
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


__all__ = ["TurnDriver", "StandardTurnDriver"]
