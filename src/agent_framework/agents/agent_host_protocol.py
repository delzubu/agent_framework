"""Minimal host protocol for agent execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, TYPE_CHECKING

from agent_framework.model import ModelDriver

from .agent_result import AgentResult
from .call_context import CallContext
from .model_end_event import ModelEndEvent
from .model_start_event import ModelStartEvent

if TYPE_CHECKING:
    from .agent import Agent


class AgentHostProtocol(Protocol):
    """Minimal host contract required by `Agent.run()`."""

    def get_model_driver(self, agent: "Agent") -> ModelDriver:
        raise NotImplementedError

    def get_agent(self, agent_id: str, *, base_dir: Path | None = None) -> "Agent":
        raise NotImplementedError

    def request_user_input(self, prompt: str) -> str:
        raise NotImplementedError

    def call_subagent(self, *, caller: "Agent", callee_id: str, parameters: dict[str, Any]) -> AgentResult:
        raise NotImplementedError

    def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> str:
        raise NotImplementedError

    def get_tool(self, tool_name: str):
        raise NotImplementedError

    def resolve_callback(self, *, caller_id: str, callee: "Agent", prompt: str) -> str:
        raise NotImplementedError

    def open_context(self, *, caller_id: str, callee_id: str, kind: str) -> CallContext:
        raise NotImplementedError

    def run_pre_model_hooks(self, event: ModelStartEvent) -> None:
        raise NotImplementedError

    def run_post_model_hooks(self, event: ModelEndEvent) -> None:
        raise NotImplementedError

    def get_skill_registry(self) -> "Any":
        raise NotImplementedError

    def register_tool(self, tool: "Any") -> None:
        raise NotImplementedError

__all__ = ["AgentHostProtocol"]
