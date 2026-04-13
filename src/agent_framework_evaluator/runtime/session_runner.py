from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agent_framework.agent import AgentResult
from agent_framework.host import AgentHost
from agent_framework.tracing import TraceContext, TraceEvent, utc_now_iso

from agent_framework_evaluator.models import SessionContext
from agent_framework_evaluator.runtime.setup_loader import load_setup_module

HostFactory = Callable[..., Any]


class SessionRunner:
    def __init__(
        self,
        env_path: str | Path,
        *,
        host_factory: HostFactory | None = None,
    ) -> None:
        self.env_path = Path(env_path)
        self._host_factory = host_factory
        self._last_setup_module: Any | None = None
        self._last_session_context: SessionContext | None = None
        self._suite_teardown_done: bool = False

    def _new_session_id(self) -> str:
        return str(uuid4())

    def _create_host(
        self,
        session_context: SessionContext,
        *,
        user_comm: Any | None = None,
        runtime_tracer: Any | None = None,
    ) -> Any:
        if self._host_factory is not None:
            return self._host_factory(
                session_context=session_context,
                env_path=self.env_path,
                user_comm=user_comm,
                runtime_tracer=runtime_tracer,
            )
        host = AgentHost.from_env(str(self.env_path), user_comm=user_comm)
        if runtime_tracer is not None:
            host.runtime_tracer = runtime_tracer
        return host

    def run_once(
        self,
        *,
        agent_id: str,
        prompt: str,
        setup_path: Path | None = None,
        user_comm: Any | None = None,
        runtime_tracer: Any | None = None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        self._suite_teardown_done = False
        sid = session_id or self._new_session_id()
        session_context = SessionContext(
            session_id=sid,
            agent_id=agent_id,
            env_path=self.env_path.resolve(),
            setup_path=setup_path,
        )
        setup_module = None
        if setup_path is not None and setup_path.exists() and setup_path.suffix == ".py":
            setup_module = load_setup_module(setup_path)

        self._last_setup_module = setup_module
        self._last_session_context = session_context

        if runtime_tracer is not None:
            runtime_tracer.publish(
                TraceEvent(
                    event_id=str(uuid4()),
                    parent_event_id=None,
                    span_id=sid,
                    parent_span_id=None,
                    timestamp=utc_now_iso(),
                    channel="runtime",
                    level="info",
                    kind="runtime.session_started",
                    title="Session started",
                    context=TraceContext(
                        session_id=session_context.session_id,
                        agent_id=agent_id,
                    ),
                )
            )

        host = self._create_host(
            session_context,
            user_comm=user_comm,
            runtime_tracer=runtime_tracer,
        )
        if setup_module and hasattr(setup_module, "register"):
            setup_module.register(host, session_context)
        if setup_module and hasattr(setup_module, "suite_setup"):
            setup_module.suite_setup(session_context)
        if setup_module and hasattr(setup_module, "test_setup"):
            setup_module.test_setup({"prompt": prompt}, session_context)
        prev_overlay = host.trace_context_overlay
        host.trace_context_overlay = TraceContext(session_id=sid)
        try:
            result: AgentResult = host.run_agent(agent_id, initial_instruction=prompt)
        finally:
            host.trace_context_overlay = prev_overlay
        if setup_module and hasattr(setup_module, "test_teardown"):
            setup_module.test_teardown({"prompt": prompt}, session_context)
        if runtime_tracer is not None:
            runtime_tracer.publish(
                TraceEvent(
                    event_id=str(uuid4()),
                    parent_event_id=None,
                    span_id=sid,
                    parent_span_id=None,
                    timestamp=utc_now_iso(),
                    channel="runtime",
                    level="info",
                    kind="runtime.session_finished",
                    title="Session finished",
                    context=TraceContext(
                        session_id=session_context.session_id,
                        agent_id=agent_id,
                    ),
                    payload={"status": result.status},
                )
            )
        return {"status": result.status, "message": result.message}

    def suite_teardown_if_any(self) -> None:
        """Invoke ``suite_teardown`` on the last loaded setup module, if present."""
        if self._suite_teardown_done:
            return
        mod = self._last_setup_module
        ctx = self._last_session_context
        if mod is None or ctx is None or not hasattr(mod, "suite_teardown"):
            return
        try:
            mod.suite_teardown(ctx)
        except Exception:
            pass
        self._suite_teardown_done = True
