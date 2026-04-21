"""Per-run mutable state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_framework.memory import MemoryRef, MemoryScope

from .call_context import CallContext


@dataclass(slots=True)
class AgentRun:
    """Mutable per-invocation runtime state."""

    run_id: str
    rendered_prompt: str
    seed_parameters: dict[str, Any]
    parameter_values: dict[str, Any]
    placeholder_values: dict[str, str] = field(default_factory=dict)
    missing_parameters: list[str] = field(default_factory=list)
    invalid_parameters: dict[str, str] = field(default_factory=dict)
    prompt_fragments: list[str] = field(default_factory=list)
    transcript_entries: list[str] = field(default_factory=list)
    conversation_messages: list[dict[str, str]] = field(default_factory=list)
    contexts: list[CallContext] = field(default_factory=list)
    history: list[str] = field(default_factory=list)
    visible_memory_scopes: tuple[MemoryScope, ...] = ()
    resolved_memory_refs: tuple[MemoryRef, ...] = ()
    memory_projection_requests: tuple[str, ...] = ()
    in_parallel_batch: bool = False

__all__ = ["AgentRun"]
