from __future__ import annotations

import asyncio
import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent_framework.agent_registry import AgentRegistry
from agent_framework.agents.helpers import AgentMarkdownError
from agent_framework.config import load_host_config
from agent_framework.tracing import TraceContext, make_trace_event
from agent_framework_evaluator.runtime.session_runner import SessionRunner
from agent_framework_evaluator.runtime.setup_loader import load_setup_module
from agent_framework_evaluator.session_manager import session_manager

_WEB_DIR = Path(__file__).resolve().parent / "web"
_executor = ThreadPoolExecutor(max_workers=4)


class UserInputBody(BaseModel):
    prompt_id: str = Field(..., min_length=1)
    text: str | None = None


class _AsyncQueueSubscriber:
    """Forward trace events from a sync worker thread into an asyncio queue."""

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[Any]) -> None:
        self._loop = loop
        self._queue = queue

    def consume(self, event: Any) -> None:
        data = asdict(event)

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
        cfg = load_host_config(env_path)
        registry = AgentRegistry.from_config(cfg)
        registry.discover()
        return {"agents": sorted(registry.list_names())}

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
            return {"template": text or ""}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.websocket("/ws/{session_id}")
    async def session_socket(websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        rec = session_manager.get(session_id)
        if rec is None:
            await websocket.close(code=4404)
            return

        loop = asyncio.get_running_loop()
        trace_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=2000)
        bridge = _AsyncQueueSubscriber(loop, trace_queue)
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
                            await websocket.send_text(json.dumps({"type": "outbox", "item": item}))
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
                    setup_raw = msg.get("setup_path")

                    def work() -> dict[str, object]:
                        return rec.runner.run_once(
                            agent_id=str(msg["agent_id"]),
                            prompt=str(msg["prompt"]),
                            setup_path=Path(setup_raw) if setup_raw else None,
                            user_comm=rec.comm,
                            runtime_tracer=rec.tracer,
                            session_id=session_id,
                        )

                    try:
                        result = await asyncio.get_running_loop().run_in_executor(_executor, work)
                    except Exception as exc:
                        _publish_evaluator_run_failure(rec.tracer, session_id, exc)
                        await websocket.send_text(
                            json.dumps(_error_payload_for_evaluator(exc), ensure_ascii=False)
                        )
                        continue
                    try:
                        await websocket.send_text(
                            json.dumps({"type": "result", "payload": result}, ensure_ascii=False)
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
