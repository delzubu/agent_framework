from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agent_framework.agent import AgentResult
from agent_framework.agent_registry import normalize_agent_id
from agent_framework.host import AgentHost
from agent_framework.tracing import TraceContext, make_trace_event
from agent_framework.tracing_bridge import active_tracer_scope

from agent_framework_evaluator.models import SessionContext
from agent_framework_evaluator.runtime.setup_loader import load_setup_module

HostFactory = Callable[..., Any]
_LOGGER = logging.getLogger(__name__)


def _wire_one_shot_capture(host: Any, callback: Callable[[Any], None]) -> None:
    """Invoke ``callback`` once on the first ``on_request_trace`` (LLM request), then chain."""
    try:
        raw = host.get_model_driver_raw()
    except Exception:
        return
    prev_req = getattr(raw, "on_request_trace", None)
    prev_resp = getattr(raw, "on_response_trace", None)
    fired = [False]

    def one_shot(trace: Any) -> None:
        if not fired[0]:
            fired[0] = True
            callback(trace)
        if prev_req is not None:
            prev_req(trace)

    raw.set_trace_callbacks(on_request=one_shot, on_response=prev_resp)


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
        audit_sub = host._audit_trace_subscriber
        receive_sub = host._host_receive_log_subscriber
        if runtime_tracer is not None:
            host.runtime_tracer = runtime_tracer
            if audit_sub is not None:
                host.runtime_tracer.subscribe(audit_sub)
            if receive_sub is not None:
                host.runtime_tracer.subscribe(receive_sub)
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
        on_first_llm_call: Callable[[Any], None] | None = None,
    ) -> dict[str, object]:
        _LOGGER.debug(
            "SessionRunner.run_once entry",
            extra={
                "trace_kind": "runtime.session_runner.entry",
                "trace_title": "SessionRunner.run_once entry",
                "trace_payload": {
                    "agent_id": agent_id,
                    "prompt": prompt,
                    "setup_path": str(setup_path) if setup_path else None,
                    "session_id": session_id,
                },
            },
        )
        self._suite_teardown_done = False
        sid = session_id or self._new_session_id()
        resolved_agent_id = normalize_agent_id(agent_id)
        session_context = SessionContext(
            session_id=sid,
            agent_id=resolved_agent_id,
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
                make_trace_event(
                    channel="runtime",
                    level="info",
                    kind="runtime.session_started",
                    title="Session started",
                    span_id=sid,
                    context=TraceContext(
                        session_id=session_context.session_id,
                        agent_id=resolved_agent_id,
                    ),
                )
            )
        _LOGGER.info(
            "session started",
            extra={
                "trace_kind": "runtime.session_runner.session_started",
                "trace_title": "SessionRunner session started",
                "trace_payload": {
                    "session_id": session_context.session_id,
                    "agent_id": resolved_agent_id,
                    "setup_path": str(setup_path) if setup_path else None,
                },
            },
        )

        host = self._create_host(
            session_context,
            user_comm=user_comm,
            runtime_tracer=runtime_tracer,
        )
        if runtime_tracer is not None:
            from agent_framework.agent_event_publisher import agent_events
            from agent_framework.llm_trace_logging import wire_llm_traces_to_runtime_tracer

            host._llm_traces_wired = False
            wire_llm_traces_to_runtime_tracer(host)
            agent_events.attach_log_sources()
        if on_first_llm_call is not None:
            _wire_one_shot_capture(host, on_first_llm_call)
        prev_overlay = host.trace_context_overlay
        host.trace_context_overlay = TraceContext(session_id=sid)
        try:
            with active_tracer_scope(runtime_tracer, host.trace_context_overlay):
                _LOGGER.debug(
                    "SessionRunner.run_once active entry",
                    extra={
                        "trace_kind": "runtime.session_runner.entry",
                        "trace_title": "SessionRunner.run_once entry",
                        "trace_payload": {
                            "agent_id": resolved_agent_id,
                            "prompt": prompt,
                            "setup_path": str(setup_path) if setup_path else None,
                            "session_id": sid,
                        },
                    },
                )
                _LOGGER.info(
                    "session started",
                    extra={
                        "trace_kind": "runtime.session_runner.session_started",
                        "trace_title": "SessionRunner session started",
                        "trace_payload": {
                            "session_id": session_context.session_id,
                            "agent_id": resolved_agent_id,
                            "setup_path": str(setup_path) if setup_path else None,
                        },
                    },
                )
                if setup_module and hasattr(setup_module, "register"):
                    _LOGGER.debug("setup register entry")
                    setup_module.register(host, session_context)
                    _LOGGER.debug("setup register exit")
                if setup_module and hasattr(setup_module, "suite_setup"):
                    _LOGGER.debug("suite_setup entry")
                    setup_module.suite_setup(session_context)
                    _LOGGER.debug("suite_setup exit")
                if setup_module and hasattr(setup_module, "test_setup"):
                    _LOGGER.debug("test_setup entry")
                    setup_module.test_setup({"prompt": prompt}, session_context)
                    _LOGGER.debug("test_setup exit")
                result = host.run_agent(resolved_agent_id, initial_instruction=prompt)
                if setup_module and hasattr(setup_module, "test_teardown"):
                    _LOGGER.debug("test_teardown entry")
                    setup_module.test_teardown({"prompt": prompt}, session_context)
                    _LOGGER.debug("test_teardown exit")
        except Exception:
            _LOGGER.error(
                "SessionRunner.run_once failed",
                exc_info=True,
                extra={
                    "trace_kind": "runtime.session_runner.failed",
                    "trace_title": "SessionRunner.run_once failed",
                    "trace_payload": {
                        "session_id": sid,
                        "agent_id": resolved_agent_id,
                        "traceback": traceback.format_exc(),
                    },
                },
            )
            _LOGGER.debug("SessionRunner.run_once failure stack", exc_info=True)
            raise
        finally:
            host.trace_context_overlay = prev_overlay
        if runtime_tracer is not None:
            runtime_tracer.publish(
                make_trace_event(
                    channel="runtime",
                    level="info",
                    kind="runtime.session_finished",
                    title="Session finished",
                    span_id=sid,
                    context=TraceContext(
                        session_id=session_context.session_id,
                        agent_id=resolved_agent_id,
                    ),
                    payload={"status": result.status},
                )
            )
        with active_tracer_scope(runtime_tracer, TraceContext(session_id=sid)):
            _LOGGER.info(
                "session finished",
                extra={
                    "trace_kind": "runtime.session_runner.session_finished",
                    "trace_title": "SessionRunner session finished",
                    "trace_payload": {
                        "session_id": sid,
                        "agent_id": resolved_agent_id,
                        "status": result.status,
                    },
                },
            )
            _LOGGER.debug(
                "SessionRunner.run_once exit",
                extra={
                    "trace_kind": "runtime.session_runner.exit",
                    "trace_title": "SessionRunner.run_once exit",
                    "trace_payload": {
                        "session_id": sid,
                        "agent_id": resolved_agent_id,
                        "result": {"status": result.status, "message": result.message},
                    },
                },
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
        except Exception as exc:
            _LOGGER.warning("suite_teardown failed: %s", exc, exc_info=True)
        self._suite_teardown_done = True
