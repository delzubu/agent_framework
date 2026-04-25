from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent_framework.agent_registry import AgentRegistry, normalize_agent_id
from agent_framework.agents.helpers import AgentMarkdownError
from agent_framework.config import load_host_config
from agent_framework.model_overrides import normalize_agent_model_override_scope
from agent_framework.tracing import TraceContext, make_trace_event
from agent_framework.tracing_bridge import active_tracer_scope
from agent_framework_evaluator.auto_user_reply import reply_text_for_outbox_item
from agent_framework_evaluator.evaluation import (
    CASE_NO_CALLBACKS_POSTFIX,
    EvaluatorLogCallback,
    extract_first_llm_request_prompts,
    run_code_evaluations,
    run_evaluation,
    select_agent_result_field,
)
from agent_framework_evaluator.initializer_catalog import (
    evaluator_initializer_root,
    list_initializer_scripts,
    load_initializer_default_agent,
    load_initializer_default_agent_model_override,
    load_initializer_default_agent_model_override_scope,
    load_initializer_default_evaluator_criteria,
    load_initializer_default_prompt,
    load_initializer_default_eval_model,
    load_raw_test_cases,
    load_test_cases,
    resolve_env_path,
    resolve_setup_path_for_run,
)
from agent_framework_evaluator.runtime.setup_loader import load_setup_module
from agent_framework_evaluator.session_manager import session_manager

_WEB_DIR = Path(__file__).resolve().parent / "web"
_executor = ThreadPoolExecutor(max_workers=4)
_LOG_LEVEL_ORDER = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_EVALUATOR_LOGGER = logging.getLogger("agent_framework_evaluator.evaluation")


class UserInputBody(BaseModel):
    prompt_id: str = Field(..., min_length=1)
    text: str | None = None


class EvaluateResultBody(BaseModel):
    """POST body for post-run scoring. ``evaluator_prompt`` is never sent to the agent."""

    session_id: str = ""
    evaluator_prompt: str = ""
    result_field: str = "message"
    log_level: str = "warning"


class EvaluateCaseBody(BaseModel):
    """POST body for scoring a run against a named initializer test case."""

    session_id: str = ""
    initializer: str = ""
    case_index: int = Field(..., ge=0)
    log_level: str = "warning"


class EvaluateBatchBody(BaseModel):
    """POST body for server-side batch evaluation of all (or selected) initializer cases."""

    session_id: str = ""
    initializer: str = ""
    case_indices: list[int] | None = None
    log_level: str = "warning"
    case_run_mode: str = "standard"
    agent_model_override: str = ""
    agent_model_override_scope: str = "root_only"


def _normalize_log_level(value: Any) -> str:
    level = str(value or "warning").strip().lower()
    return level if level in _LOG_LEVEL_ORDER else "warning"


def _level_enabled(event_level: str, configured_level: str) -> bool:
    return _LOG_LEVEL_ORDER.get(event_level, 20) >= _LOG_LEVEL_ORDER.get(configured_level, 30)


def _emit_structured_log(
    logger: logging.Logger,
    *,
    level: str,
    message: str,
    kind: str,
    title: str,
    payload: dict[str, Any],
) -> None:
    level_name = _normalize_log_level(level)
    levelno = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }[level_name]
    record = logger.makeRecord(
        logger.name,
        levelno,
        __file__,
        0,
        message,
        args=(),
        exc_info=None,
        extra={
            "trace_kind": kind,
            "trace_title": title,
            "trace_payload": payload,
            "trace_skip_bridge": True,
        },
    )
    logger.handle(record)


def _publish_structured_log_event(
    *,
    tracer: Any | None,
    session_id: str,
    level: str,
    kind: str,
    title: str,
    summary: str,
    payload: dict[str, Any],
) -> None:
    if tracer is None:
        return
    tracer.publish(
        make_trace_event(
            channel="log",
            level=_normalize_log_level(level),  # type: ignore[arg-type]
            kind=kind,
            title=title,
            summary=summary,
            span_id=session_id or None,
            context=TraceContext(session_id=session_id) if session_id else TraceContext(),
            payload={
                "logger_name": _EVALUATOR_LOGGER.name,
                "message": summary,
                **payload,
            },
        )
    )


def _make_evaluator_log_callback(
    *,
    tracer: Any | None,
    session_id: str,
    configured_level: str,
) -> EvaluatorLogCallback | None:
    """Build a callback that emits evaluator diagnostics to logging and trace."""
    if tracer is None:
        return None
    _normalize_log_level(configured_level)

    def emit(event: dict[str, Any]) -> None:
        level = str(event.get("level") or "info").strip().lower()
        if level not in _LOG_LEVEL_ORDER:
            level = "info"
        raw_payload = event.get("payload")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        kind = str(event.get("kind") or "evaluator.log")
        title = str(event.get("title") or "Evaluator")
        summary = str(event.get("summary") or event.get("title") or "Evaluator")
        _emit_structured_log(
            _EVALUATOR_LOGGER,
            level=level,
            message=summary,
            kind=kind,
            title=title,
            payload=payload,
        )
        _publish_structured_log_event(
            tracer=tracer,
            session_id=session_id,
            level=level,
            kind=kind,
            title=title,
            summary=summary,
            payload=payload,
        )

    return emit


# Alias for backward compat within this module; new code uses select_agent_result_field directly.
_select_agent_result_field = select_agent_result_field


def _evaluation_trace_scope(rec: Any, session_id: str):
    tracer = rec.tracer if rec else None
    ctx = TraceContext(session_id=session_id) if session_id else TraceContext()
    return active_tracer_scope(tracer, ctx)


class _AsyncQueueSubscriber:
    """Forward trace events from a sync worker thread into an asyncio queue."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[Any],
        *,
        usage_tracker: Any | None = None,
    ) -> None:
        self._loop = loop
        self._queue = queue
        self._usage_tracker = usage_tracker

    def consume(self, event: Any) -> None:
        data = asdict(event)
        if self._usage_tracker is not None:
            try:
                self._usage_tracker.consume_trace_event(data)
            except Exception:
                pass

        def put() -> None:
            try:
                self._queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

        self._loop.call_soon_threadsafe(put)


def _finalize_session_record(rec: Any) -> None:
    try:
        rec.runner.suite_teardown_if_any()
    except Exception:
        pass


def _error_payload_for_evaluator(exc: BaseException) -> dict[str, Any]:
    """WebSocket JSON body for failures; agent markdown issues stay readable (no traceback wall)."""
    if isinstance(exc, AgentMarkdownError):
        return {
            "type": "error",
            "error_type": "AgentMarkdownError",
            "message": exc.detail,
            "path": str(exc.source_path),
            "detail": exc.detail,
            "hint": exc.hint,
        }
    return {
        "type": "error",
        "error_type": type(exc).__name__,
        "message": str(exc),
    }


def _current_usage_summary(rec: Any) -> dict[str, Any] | None:
    summary = getattr(rec.runner, "_last_usage_summary", None)
    if isinstance(summary, dict):
        return summary
    if rec.usage_tracker is not None:
        return rec.usage_tracker.snapshot()
    return None


def _publish_evaluator_run_failure(tracer: Any, session_id: str, exc: BaseException) -> None:
    """Emit a trace event and rely on WebSocket subscribers to show it in the UI."""
    if tracer is None:
        return
    if isinstance(exc, AgentMarkdownError):
        title = "Invalid agent markdown"
        summary = exc.detail
        payload: dict[str, Any] = {
            "error_type": "AgentMarkdownError",
            "path": str(exc.source_path),
            "detail": exc.detail,
            "hint": exc.hint,
        }
    else:
        title = f"Run failed: {type(exc).__name__}"
        summary = str(exc)
        payload = {
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    tracer.publish(
        make_trace_event(
            channel="runtime",
            level="error",
            kind="runtime.run_failed",
            title=title,
            summary=summary,
            span_id=session_id,
            context=TraceContext(session_id=session_id),
            payload=payload,
        )
    )


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Evaluator")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")

    @app.get("/api/evaluator-defaults")
    def evaluator_defaults() -> dict[str, str]:
        """Defaults from ``agent-eval web`` CLI (overridable in the UI)."""
        return {
            "env_path": os.environ.get("AGENT_EVAL_DEFAULT_ENV_PATH", ".env"),
            "agent": os.environ.get("AGENT_EVAL_DEFAULT_AGENT", ""),
            "initializer": os.environ.get("AGENT_EVAL_DEFAULT_INITIALIZER", ""),
            "agent_model_override": os.environ.get("AGENT_EVAL_DEFAULT_AGENT_MODEL_OVERRIDE", ""),
            "agent_model_override_scope": normalize_agent_model_override_scope(
                os.environ.get("AGENT_EVAL_DEFAULT_AGENT_MODEL_OVERRIDE_SCOPE", "root_only")
            ),
        }

    @app.get("/api/evaluator-model-options")
    def evaluator_model_options(env_path: str = ".env") -> dict[str, Any]:
        cfg = load_host_config(resolve_env_path(env_path))
        return {"model_options": list(cfg.default_model)}

    @app.post("/api/sessions")
    def create_session(
        payload: Annotated[dict[str, Any] | None, Body()] = None,
    ) -> dict[str, str]:
        body = payload or {}
        env_path = str(body.get("env_path", ".env"))
        rec = session_manager.create_session(env_path=env_path)
        return {"session_id": rec.session_id}

    @app.post("/api/sessions/{session_id}/close")
    def close_session(session_id: str) -> dict[str, str]:
        """Idempotent: browsers may send duplicate beforeunload closes; unknown id is OK."""
        rec = session_manager.pop(session_id)
        if rec is not None:
            rec.comm.cancel_wait()
            _finalize_session_record(rec)
            return {"status": "closed"}
        return {"status": "already_closed"}

    @app.post("/api/evaluate-result")
    def evaluate_result(body: EvaluateResultBody) -> dict[str, Any]:
        """Score the agent run after the fact. Does not invoke the agent."""
        rec = session_manager.get(body.session_id) if body.session_id else None
        if rec is None or rec.last_run_result is None:
            raise HTTPException(
                status_code=400,
                detail="no run result for this session — run the agent first",
            )
        env_path = rec.env_path
        prompts = rec.last_run_prompts
        result_field = body.result_field or "message"
        agent_message = select_agent_result_field(rec.last_run_result, result_field)
        if agent_message is None:
            raise HTTPException(
                status_code=400,
                detail=f"result_field '{result_field}' not present in agent result",
            )
        with _evaluation_trace_scope(rec, body.session_id):
            if _level_enabled("debug", body.log_level):
                _emit_structured_log(
                    _EVALUATOR_LOGGER,
                    level="debug",
                    message="evaluate_result entry",
                    kind="evaluator.evaluate_result.entry",
                    title="Evaluate result entry",
                    payload={
                        "session_id": body.session_id,
                        "log_level": body.log_level,
                        "result_field": result_field,
                        "selected_payload_preview": agent_message[:200],
                        "last_run_result": rec.last_run_result,
                        "evaluator_input": {
                            "env_path": str(env_path),
                            "evaluator_prompt": body.evaluator_prompt,
                            "agent_message": agent_message,
                            "system_prompt": (prompts or {}).get("system_prompt", ""),
                            "user_prompt": (prompts or {}).get("user_prompt", ""),
                        },
                    },
                )
            out = run_evaluation(
                env_path=resolve_env_path(env_path),
                evaluator_prompt=body.evaluator_prompt,
                agent_message=agent_message,
                system_prompt=(prompts or {}).get("system_prompt", ""),
                user_prompt=(prompts or {}).get("user_prompt", ""),
                log_callback=_make_evaluator_log_callback(
                    tracer=rec.tracer,
                    session_id=body.session_id,
                    configured_level=body.log_level,
                ),
            )
            if _level_enabled("debug", body.log_level):
                _emit_structured_log(
                    _EVALUATOR_LOGGER,
                    level="debug",
                    message="evaluate_result exit",
                    kind="evaluator.evaluate_result.exit",
                    title="Evaluate result exit",
                    payload={"session_id": body.session_id, "result": out},
                )
        score = float(out["score"])
        out["score"] = min(10.0, max(0.0, score))
        return out

    @app.get("/api/initializer-cases")
    def initializer_cases(env_path: str, initializer: str) -> dict[str, Any]:
        """List test cases from initializer ``get_test_cases()``."""
        env_file = resolve_env_path(env_path)
        resolved = resolve_setup_path_for_run(env_file, initializer)
        cases = load_test_cases(env_file, initializer)
        hint = ""
        if resolved is None:
            hint = "Initializer file not found."
        return {
            "cases": cases,
            "initializer_resolved": str(resolved) if resolved is not None else "",
            "hint": hint,
        }

    @app.post("/api/evaluate-case")
    def evaluate_case(body: EvaluateCaseBody) -> dict[str, Any]:
        """LLM + optional programmatic evaluation for one test case index."""
        rec = session_manager.get(body.session_id) if body.session_id else None
        if rec is None or rec.last_run_result is None:
            raise HTTPException(
                status_code=400,
                detail="no run result for this session — run the agent first",
            )
        env_path = rec.env_path
        env_file = resolve_env_path(env_path)
        cases = load_raw_test_cases(env_file, body.initializer)
        if body.case_index >= len(cases):
            raise HTTPException(status_code=400, detail="invalid case_index for this initializer")
        case = cases[body.case_index]
        criteria = str(case.get("evaluation_criteria", "") or "")
        prompts = rec.last_run_prompts
        eval_model = load_initializer_default_eval_model(env_file, body.initializer)
        result_field = str(case.get("result_field", "message") or "message")
        agent_message = select_agent_result_field(rec.last_run_result, result_field)
        if agent_message is None:
            raise HTTPException(
                status_code=400,
                detail=f"result_field '{result_field}' not present in agent result",
            )
        with _evaluation_trace_scope(rec, body.session_id):
            if _level_enabled("debug", body.log_level):
                _emit_structured_log(
                    _EVALUATOR_LOGGER,
                    level="debug",
                    message="evaluate_case entry",
                    kind="evaluator.evaluate_case.entry",
                    title="Evaluate case entry",
                    payload={
                        "session_id": body.session_id,
                        "initializer": body.initializer,
                        "case_index": body.case_index,
                        "result_field": result_field,
                        "selected_payload_preview": agent_message[:200],
                        "last_run_result": rec.last_run_result,
                        "log_level": body.log_level,
                        "evaluator_input": {
                            "env_path": str(env_file),
                            "evaluator_prompt": criteria,
                            "agent_message": agent_message,
                            "system_prompt": (prompts or {}).get("system_prompt", ""),
                            "user_prompt": (prompts or {}).get("user_prompt", ""),
                            "model_override": eval_model if eval_model else None,
                        },
                    },
                )
            llm = run_evaluation(
                env_path=env_file,
                evaluator_prompt=criteria,
                agent_message=agent_message,
                system_prompt=(prompts or {}).get("system_prompt", ""),
                user_prompt=(prompts or {}).get("user_prompt", ""),
                model_override=eval_model if eval_model else None,
                log_callback=_make_evaluator_log_callback(
                    tracer=rec.tracer if rec else None,
                    session_id=body.session_id,
                    configured_level=body.log_level,
                ),
            )
            if _level_enabled("debug", body.log_level):
                _emit_structured_log(
                    _EVALUATOR_LOGGER,
                    level="debug",
                    message="evaluate_case llm result",
                    kind="evaluator.evaluate_case.llm_result",
                    title="Evaluate case LLM result",
                    payload={
                        "session_id": body.session_id,
                        "case_index": body.case_index,
                        "result": llm,
                    },
                )
        score = float(llm["score"])
        llm["score"] = min(10.0, max(0.0, score))

        try:
            code_results = run_code_evaluations(
                case.get("code_evaluators", []),
                prompt=str(case.get("prompt", "")),
                agent_message=agent_message,
                flags=case.get("flags", set()),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        parts = [float(llm["score"])] + [float(r["score"]) for r in code_results if r is not None]
        average = sum(parts) / len(parts)

        return {
            "llm_result": llm,
            "code_results": code_results,
            "average_score": average,
        }

    @app.post("/api/sessions/{session_id}/user-input")
    def post_user_input(session_id: str, body: UserInputBody) -> dict[str, str]:
        """Deliver user text (or cancel with ``text: null``) for the pending ``prompt_id``."""
        rec = session_manager.get(session_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown session")
        ok = rec.comm.submit_user_input(body.text, prompt_id=body.prompt_id)
        if not ok:
            raise HTTPException(
                status_code=409,
                detail="no pending prompt for this session or prompt_id mismatch",
            )
        return {"status": "ok"}

    @app.get("/api/agents")
    def list_agents(env_path: str = ".env") -> dict[str, list[str]]:
        cfg = load_host_config(resolve_env_path(env_path))
        registry = AgentRegistry.from_config(cfg)
        registry.discover()
        return {"agents": sorted(registry.list_names())}

    @app.get("/api/agent-system-prompt")
    def agent_system_prompt(env_path: str, agent_id: str) -> dict[str, str]:
        """Return the raw ``system_prompt`` from the agent markdown (not provider-expanded)."""
        cfg = load_host_config(resolve_env_path(env_path))
        registry = AgentRegistry.from_config(cfg)
        registry.discover()
        aid = normalize_agent_id(agent_id) or "root"
        try:
            agent = registry.get(aid)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"system_prompt": agent.system_prompt}

    @app.get("/api/sessions/{session_id}/last-prompts")
    def last_prompts(session_id: str) -> dict[str, Any]:
        """Snapshots from the last run: system, user parts, and text entered in the UI."""
        rec = session_manager.get(session_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown session")
        snap = rec.last_run_prompts
        if not snap:
            return {
                "system_prompt": "",
                "user_prompt": "",
                "instruction_entered": "",
                "user_messages": [],
            }
        um = snap.get("user_messages")
        if not isinstance(um, list):
            um = [str(snap.get("user_prompt", ""))] if snap.get("user_prompt") else []
        return {
            "system_prompt": str(snap.get("system_prompt", "")),
            "user_prompt": str(snap.get("user_prompt", "")),
            "instruction_entered": str(snap.get("instruction_entered", "")),
            "user_messages": [str(x) for x in um],
        }

    @app.get("/api/sessions/{session_id}/usage-summary")
    def usage_summary(session_id: str) -> dict[str, Any]:
        rec = session_manager.get(session_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown session")
        if rec.last_usage_summary is not None:
            return rec.last_usage_summary
        if rec.usage_tracker is not None:
            return rec.usage_tracker.snapshot()
        return {"session_totals": {}, "agents": {}, "runs": {}}

    @app.get("/api/initializers")
    def list_initializers(env_path: str = ".env") -> dict[str, Any]:
        env_file = resolve_env_path(env_path)
        names = list_initializer_scripts(env_file)
        init_root = evaluator_initializer_root(env_file)
        env_resolved = str(env_file)
        return {
            "initializers": names,
            "initializer_dir": str(init_root) if init_root is not None else "",
            "env_exists": env_file.exists(),
            "env_resolved": env_resolved,
        }

    @app.get("/api/initializer-template")
    def initializer_template(env_path: str, initializer: str) -> dict[str, Any]:
        """Load initializer/setup ``.py`` and return default prompt, criteria, and optional agent id."""
        env_file = resolve_env_path(env_path)
        try:
            text = load_initializer_default_prompt(env_file, initializer)
            criteria = load_initializer_default_evaluator_criteria(env_file, initializer)
            agent = load_initializer_default_agent(env_file, initializer)
            agent_model_override = load_initializer_default_agent_model_override(env_file, initializer)
            agent_model_override_scope = load_initializer_default_agent_model_override_scope(
                env_file, initializer
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if (
            not text
            and not criteria
            and not agent
            and not agent_model_override
            and not agent_model_override_scope
        ):
            cases = load_test_cases(env_file, initializer)
            if not cases:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "initializer not found or no PROMPT_TEMPLATE / get_prompt_template() / "
                        "get_test_cases() / EVALUATOR_CRITERIA / DEFAULT_AGENT / get_default_agent()"
                    ),
                )
            c0 = cases[0]
            text = str(c0.get("prompt", "") or "")
            criteria = str(c0.get("criteria", "") or "")
        return {
            "template": text or "",
            "evaluator_criteria": criteria or "",
            "agent": agent or "",
            "agent_model_override": agent_model_override or "",
            "agent_model_override_scope": normalize_agent_model_override_scope(
                agent_model_override_scope or "root_only"
            ),
        }

    @app.get("/api/setup-template")
    def setup_template(path: str) -> dict[str, Any]:
        p = Path(path)
        if p.suffix != ".py" or not p.exists():
            raise HTTPException(status_code=400, detail="invalid or missing setup file")
        try:
            module = load_setup_module(p)
            text = getattr(module, "PROMPT_TEMPLATE", None)
            if text is None and hasattr(module, "get_prompt_template"):
                gt = module.get_prompt_template()
                text = json.dumps(gt) if isinstance(gt, dict) else (gt or "")
            agent = ""
            raw_agent = getattr(module, "DEFAULT_AGENT", None)
            if isinstance(raw_agent, str) and raw_agent.strip():
                agent = raw_agent.strip()
            elif hasattr(module, "get_default_agent"):
                g = module.get_default_agent()
                if isinstance(g, str) and g.strip():
                    agent = g.strip()
            return {"template": text or "", "agent": agent}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/evaluate-batch")
    async def evaluate_batch(body: EvaluateBatchBody) -> Any:
        """Run and evaluate all (or selected) initializer cases server-side, streaming NDJSON progress."""
        from fastapi.responses import StreamingResponse

        rec = session_manager.get(body.session_id) if body.session_id else None
        if rec is None:
            raise HTTPException(status_code=400, detail="unknown session — create a session first")
        crm = str(body.case_run_mode or "standard").strip()
        rec.case_run_mode = crm if crm in ("standard", "no_callbacks") else "standard"
        env_file = resolve_env_path(rec.env_path)
        cases = load_raw_test_cases(env_file, body.initializer)
        if not cases:
            raise HTTPException(status_code=400, detail="no cases found for this initializer")
        indices = body.case_indices if body.case_indices is not None else list(range(len(cases)))
        setup_path = resolve_setup_path_for_run(env_file, body.initializer)
        default_agent = load_initializer_default_agent(env_file, body.initializer) or "root"
        eval_model = load_initializer_default_eval_model(env_file, body.initializer)

        async def stream() -> Any:
            loop = asyncio.get_running_loop()
            for idx in indices:
                if idx >= len(cases):
                    yield json.dumps({"case_index": idx, "error": "invalid case_index"}) + "\n"
                    continue
                case = cases[idx]
                result_field = str(case.get("result_field", "message") or "message")
                raw_prompt = str(case.get("prompt", ""))
                prompt_for_run = (
                    raw_prompt.rstrip() + CASE_NO_CALLBACKS_POSTFIX
                    if rec.case_run_mode == "no_callbacks"
                    else raw_prompt
                )
                criteria = str(case.get("evaluation_criteria", "") or "")

                case_prompts: dict[str, Any] = {}

                def on_case_first_llm_call(
                    trace: Any, _cap: dict = case_prompts
                ) -> None:
                    llm_payload = getattr(trace, "input_payload", None)
                    _cap.update(extract_first_llm_request_prompts(llm_payload))

                def run_case(
                    p: str = prompt_for_run, _on_llm: Any = on_case_first_llm_call
                ) -> dict[str, object]:
                    return rec.runner.run_once(
                        agent_id=default_agent,
                        prompt=p,
                        setup_path=setup_path,
                        user_comm=rec.comm,
                        runtime_tracer=rec.tracer,
                        session_id=body.session_id,
                        on_first_llm_call=_on_llm,
                        agent_model_override=body.agent_model_override or None,
                        agent_model_override_scope=body.agent_model_override_scope,
                    )

                try:
                    if rec.usage_tracker is not None:
                        rec.usage_tracker.reset()
                    run_result = await loop.run_in_executor(_executor, run_case)
                    rec.last_run_result = run_result
                    rec.last_usage_summary = (
                        rec.usage_tracker.snapshot() if rec.usage_tracker is not None else None
                    )
                    agent_msg = select_agent_result_field(run_result, result_field)
                    if agent_msg is None:
                        raise ValueError(f"result_field '{result_field}' not present in agent result")

                    def run_evaluation_for_case(am: str = agent_msg, cr: str = criteria, pr: dict = case_prompts) -> dict[str, Any]:
                        return run_evaluation(
                            env_path=env_file,
                            evaluator_prompt=cr,
                            agent_message=am,
                            system_prompt=pr.get("system_prompt", ""),
                            user_prompt=pr.get("user_prompt", ""),
                            model_override=eval_model if eval_model else None,
                        )

                    llm: dict[str, Any] = await loop.run_in_executor(_executor, run_evaluation_for_case)
                    llm["score"] = min(10.0, max(0.0, float(llm["score"])))

                    code_results = run_code_evaluations(
                        case.get("code_evaluators", []),
                        prompt=raw_prompt,
                        agent_message=agent_msg,
                        flags=case.get("flags", set()),
                    )

                    parts = [float(llm["score"])] + [float(r["score"]) for r in code_results if r is not None]
                    average = sum(parts) / len(parts)

                    yield json.dumps({
                        "case_index": idx,
                        "title": case.get("title", f"Case {idx}"),
                        "llm_result": llm,
                        "code_results": code_results,
                        "average_score": average,
                        "usage_summary": rec.last_usage_summary,
                    }) + "\n"
                except Exception as exc:
                    yield json.dumps({
                        "case_index": idx,
                        "title": case.get("title", f"Case {idx}"),
                        "error": str(exc),
                        "usage_summary": _current_usage_summary(rec),
                    }) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.websocket("/ws/{session_id}")
    async def session_socket(websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        rec = session_manager.get(session_id)
        if rec is None:
            await websocket.close(code=4404)
            return

        loop = asyncio.get_running_loop()
        trace_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=2000)
        bridge = _AsyncQueueSubscriber(loop, trace_queue, usage_tracker=rec.usage_tracker)
        rec.tracer.subscribe(bridge)
        stop = asyncio.Event()

        async def pump_outbox_and_traces() -> None:
            while not stop.is_set():
                try:
                    try:
                        ev = await asyncio.wait_for(trace_queue.get(), timeout=0.1)
                        await websocket.send_text(json.dumps({"type": "trace", "event": ev}))
                    except asyncio.TimeoutError:
                        pass
                    except (OSError, RuntimeError):
                        return
                    for item in rec.comm.drain_outbox():
                        try:
                            auto_text = reply_text_for_outbox_item(
                                item, case_run_mode=getattr(rec, "case_run_mode", "standard")
                            )
                            if auto_text is not None:
                                item["evaluator_auto_reply_text"] = auto_text
                            await websocket.send_text(json.dumps({"type": "outbox", "item": item}))
                            if auto_text is not None:
                                pid = item.get("prompt_id")
                                if isinstance(pid, str):
                                    rec.comm.submit_user_input(auto_text, prompt_id=pid)
                        except (OSError, RuntimeError):
                            return
                except (OSError, RuntimeError):
                    return

        pumper = asyncio.create_task(pump_outbox_and_traces())
        try:
            while True:
                try:
                    raw = await websocket.receive_text()
                except WebSocketDisconnect:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "error_type": "JSONDecodeError",
                                "message": "Invalid JSON in WebSocket message.",
                            },
                            ensure_ascii=False,
                        )
                    )
                    continue
                if msg.get("type") == "user_input":
                    raw_pid = msg.get("prompt_id")
                    pid = str(raw_pid) if raw_pid else None
                    rec.comm.submit_user_input(msg.get("text"), prompt_id=pid)
                elif msg.get("type") == "run":
                    crm_raw = msg.get("case_run_mode", "standard")
                    crm = str(crm_raw).strip() if crm_raw is not None else "standard"
                    rec.case_run_mode = crm if crm in ("standard", "no_callbacks") else "standard"
                    init_raw = msg.get("initializer") if msg.get("initializer") is not None else msg.get("setup_path")
                    instruction_entered = str(msg.get("prompt", ""))
                    prompt_for_run = instruction_entered
                    if rec.case_run_mode == "no_callbacks":
                        prompt_for_run = prompt_for_run.rstrip() + CASE_NO_CALLBACKS_POSTFIX
                    # Seed so the UI always has composer text even if no LLM request fires;
                    # on_first_llm_call replaces this with full trace-derived snapshots.
                    rec.last_run_prompts = {
                        "instruction_entered": instruction_entered,
                        "system_prompt": "",
                        "user_prompt": "",
                        "user_messages": [],
                    }
                    rec.last_run_result = None
                    rec.last_usage_summary = None
                    if rec.usage_tracker is not None:
                        rec.usage_tracker.reset()

                    def on_first_llm_call(trace: Any) -> None:
                        payload = getattr(trace, "input_payload", None)
                        snaps = extract_first_llm_request_prompts(payload)
                        snaps["instruction_entered"] = instruction_entered
                        rec.last_run_prompts = snaps

                    def work() -> dict[str, object]:
                        env_fp = resolve_env_path(rec.env_path)
                        sp = resolve_setup_path_for_run(env_fp, str(init_raw).strip() if init_raw else None)
                        return rec.runner.run_once(
                            agent_id=str(msg["agent_id"]),
                            prompt=prompt_for_run,
                            setup_path=sp,
                            user_comm=rec.comm,
                            runtime_tracer=rec.tracer,
                            session_id=session_id,
                            on_first_llm_call=on_first_llm_call,
                            agent_model_override=(
                                str(msg.get("agent_model_override", "")).strip() or None
                            ),
                            agent_model_override_scope=str(
                                msg.get("agent_model_override_scope", "root_only")
                            ),
                        )

                    try:
                        result = await asyncio.get_running_loop().run_in_executor(_executor, work)
                        rec.last_run_result = result
                        rec.last_usage_summary = (
                            rec.usage_tracker.snapshot() if rec.usage_tracker is not None else None
                        )
                    except Exception as exc:
                        rec.last_usage_summary = _current_usage_summary(rec)
                        _publish_evaluator_run_failure(rec.tracer, session_id, exc)
                        payload = _error_payload_for_evaluator(exc)
                        if rec.last_usage_summary is not None:
                            payload["usage_summary"] = rec.last_usage_summary
                        await websocket.send_text(
                            json.dumps(payload, ensure_ascii=False)
                        )
                        continue
                    try:
                        snaps = rec.last_run_prompts
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "result",
                                    "payload": result,
                                    "prompt_snapshots": snaps
                                    if isinstance(snaps, dict)
                                    else {
                                        "system_prompt": "",
                                        "user_prompt": "",
                                        "instruction_entered": "",
                                        "user_messages": [],
                                    },
                                    "usage_summary": rec.last_usage_summary,
                                },
                                ensure_ascii=False,
                            )
                        )
                    except (OSError, RuntimeError):
                        break
        except WebSocketDisconnect:
            pass
        finally:
            stop.set()
            rec.tracer.unsubscribe(bridge)
            pumper.cancel()
            try:
                await pumper
            except asyncio.CancelledError:
                pass
            rec.comm.cancel_wait()
            _finalize_session_record(rec)

    return app


app = create_app()
