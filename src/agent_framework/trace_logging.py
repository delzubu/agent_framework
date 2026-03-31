"""Shared console tracing behavior for agent lifecycle events."""

from __future__ import annotations

import json
from typing import Any

from agent_framework.agent import (
    AgentBehavior,
    AgentEndEvent,
    AgentStartEvent,
    SubagentEndEvent,
    SubagentStartEvent,
    ToolEndEvent,
    ToolStartEvent,
)

_EVENT_COLOR = "\033[96m"
_PAYLOAD_COLOR = "\033[97m"
_RESET = "\033[0m"


class TraceLoggingBehavior(AgentBehavior):
    """Write lifecycle traces for agent, tool, and subagent activity."""

    def attach(self, agent) -> None:
        """Attach tracing hooks to the supplied agent instance."""
        agent.onPreAgent += self._log_pre_agent
        agent.onPostAgent += self._log_post_agent
        agent.onPreTool += self._log_pre_tool
        agent.onPostTool += self._log_post_tool
        agent.onPreSubagent += self._log_pre_subagent
        agent.onPostSubagent += self._log_post_subagent

    def _log_pre_agent(self, event: AgentStartEvent):
        """Log the prompt about to be sent into the current agent."""
        _emit(f"PRE AGENT {event.invocation.agent_id}", event.invocation.rendered_prompt)
        return None

    def _log_post_agent(self, event: AgentEndEvent):
        """Log the final response returned by the current agent."""
        _emit(f"POST AGENT {event.invocation.agent_id}", event.result.message)
        return None

    def _log_pre_tool(self, event: ToolStartEvent):
        """Log one tool request before execution."""
        _emit(
            f"PRE TOOL {event.invocation.agent_id}.{event.tool_name}",
            json.dumps(event.tool_input, indent=2, sort_keys=True),
        )
        return None

    def _log_post_tool(self, event: ToolEndEvent):
        """Log one tool result after execution."""
        _emit(f"POST TOOL {event.invocation.agent_id}.{event.tool_name}", event.result)
        return None

    def _log_pre_subagent(self, event: SubagentStartEvent):
        """Log one subagent request before execution."""
        _emit(
            f"PRE SUBAGENT {event.invocation.agent_id}->{event.subagent_id}",
            json.dumps(event.subagent_input, indent=2, sort_keys=True),
        )
        return None

    def _log_post_subagent(self, event: SubagentEndEvent):
        """Log one subagent result after execution."""
        _emit(f"POST SUBAGENT {event.invocation.agent_id}->{event.subagent_id}", event.result.message)
        return None


def build_behavior() -> AgentBehavior:
    """Build the shared trace logging behavior."""
    return TraceLoggingBehavior()


def _emit(label: str, payload: Any) -> None:
    """Write one colored event log to the console."""
    print(f"{_EVENT_COLOR}[{label}]{_RESET}")
    print(f"{_PAYLOAD_COLOR}{payload}{_RESET}")
