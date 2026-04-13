"""Immutable in-memory audit tracing for agent runs.

This module remains the compatibility API for existing JSONL audit dumps.
Unified runtime tracing lives in :mod:`agent_framework.tracing` and may
eventually be backed by shared subscribers rather than duplicating writes here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_framework.agents.agent_decision import AgentDecision

_TRACE_SESSION_FILENAME = f"trace-{datetime.now().strftime('%y%m%d_%H%M%S')}.jsonl"


def _utc_now() -> str:
    return datetime.now().astimezone().isoformat()


@dataclass(frozen=True, slots=True)
class CallbackAuditRecord:
    """Single callback event observed during an agent run."""

    timestamp: str
    intent: str
    prompt: str
    target: str
    response: str | None = None


@dataclass(frozen=True, slots=True)
class SkillInvocationRecord:
    """Single skill invocation event observed during an agent run."""

    timestamp: str
    skill_name: str
    parameters: dict[str, Any]
    inventory: tuple[str, ...]  # file paths listed in inventory (no file contents)


@dataclass(frozen=True, slots=True)
class UserOutputRecord:
    """Host-level record of output sent to the user."""
    timestamp: str
    role: str          # e.g. "assistant", "system"
    text: str


@dataclass(frozen=True, slots=True)
class UserInputRecord:
    """Host-level record of input received from the user."""
    timestamp: str
    prompt: str        # the prompt shown to the user
    response: str      # the user's reply


@dataclass(frozen=True, slots=True)
class PermissionRequestRecord:
    """Host-level record of a permission request and its decision."""
    timestamp: str
    tool_name: str
    action: str        # e.g. "write", "execute", "network"
    resource: str      # file path, command, URL etc.
    summary: str
    allowed: bool
    remember_for_session: bool


@dataclass(frozen=True, slots=True)
class AgentCallAuditRecord:
    """Immutable audit record for a single agent invocation."""

    timestamp: str
    run_id: str
    caller_id: str | None
    agent_name: str
    system_prompt: str
    system_prompt_sources: tuple[str, ...]
    user_prompt: str
    user_prompt_sources: tuple[str, ...]
    llm_message_sent: Any = None
    llm_message_received: str | None = None
    model_response: dict[str, Any] | None = None
    agent_decision: dict[str, Any] | None = None
    callbacks: tuple[CallbackAuditRecord, ...] = ()
    skill_invocations: tuple[SkillInvocationRecord, ...] = ()
    events: tuple[dict[str, Any], ...] = ()

    def to_jsonable(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


@dataclass(slots=True)
class InMemoryAuditTracer:
    """Host-owned audit tracer that stays separate from agent runtime state."""

    output_dir: Path
    active_records: dict[str, AgentCallAuditRecord] = field(default_factory=dict)
    output_path: Path | None = None

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path = self.output_dir / _TRACE_SESSION_FILENAME

    def start_agent_call(
        self,
        *,
        run_id: str,
        caller_id: str | None,
        agent_name: str,
        system_prompt: str,
        system_prompt_sources: tuple[str, ...],
        user_prompt: str,
        user_prompt_sources: tuple[str, ...],
    ) -> None:
        self.active_records[run_id] = AgentCallAuditRecord(
            timestamp=_utc_now(),
            run_id=run_id,
            caller_id=caller_id,
            agent_name=agent_name,
            system_prompt=system_prompt,
            system_prompt_sources=system_prompt_sources,
            user_prompt=user_prompt,
            user_prompt_sources=user_prompt_sources,
        )

    def record_llm_request(self, *, run_id: str, payload: Any) -> None:
        record = self.active_records.get(run_id)
        if record is None:
            return
        try:
            payload_copy = json.loads(json.dumps(payload, ensure_ascii=False))
        except TypeError:
            payload_copy = payload
        self.active_records[run_id] = replace(
            record,
            llm_message_sent=payload_copy,
        )

    def record_llm_response(
        self,
        *,
        run_id: str,
        raw_text: str,
        parsed_payload: dict[str, Any] | None,
    ) -> None:
        record = self.active_records.get(run_id)
        if record is None:
            return
        payload_copy = None if parsed_payload is None else dict(parsed_payload)
        self.active_records[run_id] = replace(
            record,
            llm_message_received=raw_text,
            model_response=payload_copy,
        )

    def record_decision(self, *, run_id: str, decision: AgentDecision) -> None:
        record = self.active_records.get(run_id)
        if record is None:
            return
        self.active_records[run_id] = replace(
            record,
            agent_decision={
                "kind": decision.kind,
                "message": decision.message,
                "parameters": dict(decision.parameters),
                "subagent_id": decision.subagent_id,
                "tool_name": decision.tool_name,
                "callback_intent": decision.callback_intent,
            },
        )

    def record_callback(
        self,
        *,
        run_id: str,
        intent: str,
        prompt: str,
        target: str,
        response: str | None = None,
    ) -> None:
        record = self.active_records.get(run_id)
        if record is None:
            return
        callbacks = list(record.callbacks)
        callbacks.append(
            CallbackAuditRecord(
                timestamp=_utc_now(),
                intent=intent,
                prompt=prompt,
                target=target,
                response=response,
            )
        )
        self.active_records[run_id] = replace(record, callbacks=tuple(callbacks))

    def record_skill_invocation(
        self,
        *,
        run_id: str,
        skill_name: str,
        parameters: dict[str, Any],
        inventory: list[str],
    ) -> None:
        record = self.active_records.get(run_id)
        if record is None:
            return
        invocations = list(record.skill_invocations)
        invocations.append(
            SkillInvocationRecord(
                timestamp=_utc_now(),
                skill_name=skill_name,
                parameters=dict(parameters),
                inventory=tuple(inventory),
            )
        )
        self.active_records[run_id] = replace(record, skill_invocations=tuple(invocations))

    def record_event(self, *, run_id: str, event: dict[str, Any]) -> None:
        record = self.active_records.get(run_id)
        if record is None:
            return
        events = list(record.events)
        events.append(json.loads(json.dumps(event, ensure_ascii=False)))
        self.active_records[run_id] = replace(record, events=tuple(events))

    def finish_agent_call(self, *, run_id: str) -> None:
        record = self.active_records.pop(run_id, None)
        if record is None:
            return
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_jsonable(), ensure_ascii=False) + "\n")

    def record_user_output(self, *, role: str, text: str) -> None:
        """Append a host-level user-output record to the session JSONL."""
        if self.output_path is None:
            return
        record = UserOutputRecord(timestamp=_utc_now(), role=role, text=text)
        self._append_session_record("user_output", asdict(record))

    def record_user_input(self, *, prompt: str, response: str) -> None:
        """Append a host-level user-input record to the session JSONL."""
        if self.output_path is None:
            return
        record = UserInputRecord(timestamp=_utc_now(), prompt=prompt, response=response)
        self._append_session_record("user_input", asdict(record))

    def record_permission(
        self,
        *,
        tool_name: str,
        action: str,
        resource: str,
        summary: str,
        allowed: bool,
        remember_for_session: bool,
    ) -> None:
        """Append a host-level permission-request record to the session JSONL."""
        if self.output_path is None:
            return
        record = PermissionRequestRecord(
            timestamp=_utc_now(),
            tool_name=tool_name,
            action=action,
            resource=resource,
            summary=summary,
            allowed=allowed,
            remember_for_session=remember_for_session,
        )
        self._append_session_record("permission_request", asdict(record))

    def _append_session_record(self, record_type: str, payload: dict) -> None:
        """Write a typed top-level record to the session JSONL file."""
        entry = {"type": record_type, **payload}
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


__all__ = [
    "CallbackAuditRecord",
    "SkillInvocationRecord",
    "UserOutputRecord",
    "UserInputRecord",
    "PermissionRequestRecord",
    "AgentCallAuditRecord",
    "InMemoryAuditTracer",
]
