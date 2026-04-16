from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agent_framework.agent_event_publisher import agent_events
from agent_framework.tracing import CompositeRuntimeTracer
from agent_framework.web_communication import WebUserCommunication

from agent_framework_evaluator.runtime.debug_subscriber import DebuggerSubscriber
from agent_framework_evaluator.runtime.session_runner import SessionRunner


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    comm: WebUserCommunication
    tracer: CompositeRuntimeTracer
    debugger: DebuggerSubscriber
    env_path: str
    runner: SessionRunner
    last_run_prompts: dict[str, Any] | None = None
    last_run_result: dict[str, Any] | None = None
    #: From WebSocket ``run`` message: ``standard`` (auto-answer confirmations/permissions) vs
    #: ``no_callbacks`` (only auto-answer text prompts; user handles confirm/permission).
    case_run_mode: str = "standard"


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}

    def create_session(self, *, env_path: str = ".env") -> SessionRecord:
        session_id = str(uuid4())
        comm = WebUserCommunication(session_id=session_id)
        tracer = CompositeRuntimeTracer()
        debugger = DebuggerSubscriber()
        tracer.subscribe(debugger)
        agent_events.attach_log_sources()
        runner = SessionRunner(env_path)
        rec = SessionRecord(
            session_id=session_id,
            comm=comm,
            tracer=tracer,
            debugger=debugger,
            env_path=env_path,
            runner=runner,
        )
        self._sessions[session_id] = rec
        return rec

    def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    def pop(self, session_id: str) -> SessionRecord | None:
        return self._sessions.pop(session_id, None)


session_manager = SessionManager()
