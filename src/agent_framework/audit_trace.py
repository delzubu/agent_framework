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
from typing import TYPE_CHECKING, Any

from agent_framework.agents.agent_decision import AgentDecision

if TYPE_CHECKING:
    from agent_framework.tracing import TraceEvent

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
    status: str = "loaded"
    loaded_resources: tuple[str, ...] = ()


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
class MemoryOperationRecord:
    """Session-level record of a memory mutation or normalization event."""

    timestamp: str
    operation: str
    memory_uri: str | None = None
    scope: str | None = None
    mime_type: str | None = None
    title: str | None = None
    summary: str | None = None
    size_bytes: int | None = None
    version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
                "response": decision.response,
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
        status: str = "loaded",
        loaded_resources: list[str] | None = None,
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
                status=status,
                loaded_resources=tuple(loaded_resources or ()),
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

    def record_memory_operation(
        self,
        *,
        operation: str,
        memory_uri: str | None = None,
        scope: str | None = None,
        mime_type: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        size_bytes: int | None = None,
        version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a host-level memory operation record to the session JSONL."""
        if self.output_path is None:
            return
        record = MemoryOperationRecord(
            timestamp=_utc_now(),
            operation=operation,
            memory_uri=memory_uri,
            scope=scope,
            mime_type=mime_type,
            title=title,
            summary=summary,
            size_bytes=size_bytes,
            version=version,
            metadata=dict(metadata or {}),
        )
        self._append_session_record("memory_operation", asdict(record))

    def _append_session_record(self, record_type: str, payload: dict) -> None:
        """Write a typed top-level record to the session JSONL file."""
        entry = {"type": record_type, **payload}
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


class AuditTraceSubscriber:
    """Maps unified ``TraceEvent`` stream to :class:`InMemoryAuditTracer` JSONL records.

    Subscribes only to ``runtime`` and ``llm`` channels (ignores ``log`` and ``user``).
    """

    trace_channels = frozenset({"runtime", "llm"})

    def __init__(self, store: InMemoryAuditTracer) -> None:
        self._store = store

    def consume(self, event: "TraceEvent") -> None:
        from agent_framework.tracing import TraceEvent as _TE

        if not isinstance(event, _TE):
            return
        kind = event.kind
        ctx = event.context
        run_id = ctx.run_id
        payload = event.payload

        if kind == "runtime.audit.agent_call_started":
            self._store.start_agent_call(
                run_id=str(payload["run_id"]),
                caller_id=payload.get("caller_id"),
                agent_name=str(payload["agent_name"]),
                system_prompt=str(payload["system_prompt"]),
                system_prompt_sources=tuple(payload["system_prompt_sources"]),
                user_prompt=str(payload["user_prompt"]),
                user_prompt_sources=tuple(payload["user_prompt_sources"]),
            )
            return
        if kind == "runtime.audit.agent_call_finished":
            self._store.finish_agent_call(run_id=str(payload["run_id"]))
            return
        if kind == "runtime.audit.decision" and run_id:
            d = payload.get("decision") or {}
            decision = AgentDecision(
                kind=str(d["kind"]),
                message=str(d.get("message", "")),
                parameters=dict(d.get("parameters") or {}),
                subagent_id=d.get("subagent_id"),
                tool_name=d.get("tool_name"),
                callback_intent=d.get("callback_intent"),
                skill_name=d.get("skill_name"),
            )
            self._store.record_decision(run_id=run_id, decision=decision)
            return
        if kind == "runtime.audit.callback" and run_id:
            self._store.record_callback(
                run_id=run_id,
                intent=str(payload["intent"]),
                prompt=str(payload["prompt"]),
                target=str(payload["target"]),
                response=payload.get("response"),
            )
            self._store.record_event(run_id=run_id, event=dict(payload.get("event") or {}))
            return
        if kind == "runtime.audit.named_event" and run_id:
            ev = dict(payload.get("event") or {})
            self._store.record_event(run_id=run_id, event=ev)
            return
        if kind in ("runtime.memory_put", "runtime.memory_update", "runtime.memory_autostore"):
            self._store.record_memory_operation(
                operation=kind.split(".")[-1],
                memory_uri=payload.get("memory_uri"),
                scope=payload.get("scope"),
                mime_type=payload.get("mime_type"),
                title=payload.get("title"),
                summary=payload.get("summary"),
                size_bytes=payload.get("size_bytes"),
                version=payload.get("version"),
                metadata=dict(payload),
            )
            return
        if kind == "runtime.audit.skill_invocation" and run_id:
            self._store.record_skill_invocation(
                run_id=run_id,
                skill_name=str(payload["skill_name"]),
                parameters=dict(payload.get("parameters") or {}),
                inventory=list(payload.get("inventory") or []),
                status=str(payload.get("status") or "loaded"),
                loaded_resources=list(payload.get("loaded_resources") or []),
            )
            return
        if kind == "llm.request":
            rid = payload.get("run_id") or run_id
            if rid and payload.get("input_payload") is not None:
                self._store.record_llm_request(run_id=str(rid), payload=payload["input_payload"])
            return
        if kind in ("llm.response", "llm.error"):
            rid = payload.get("run_id") or run_id
            if rid:
                raw = str(payload.get("raw_text") or "")
                parsed = payload.get("parsed_payload")
                parsed_dict = None if parsed is None else dict(parsed)
                self._store.record_llm_response(
                    run_id=str(rid),
                    raw_text=raw,
                    parsed_payload=parsed_dict,
                )
            return


__all__ = [
    "AuditTraceSubscriber",
    "CallbackAuditRecord",
    "SkillInvocationRecord",
    "UserOutputRecord",
    "UserInputRecord",
    "PermissionRequestRecord",
    "MemoryOperationRecord",
    "AgentCallAuditRecord",
    "InMemoryAuditTracer",
]
