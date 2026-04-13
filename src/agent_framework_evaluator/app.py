from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent_framework.config import load_host_config
from agent_framework.host import AgentHost
from agent_framework_evaluator.runtime.session_runner import SessionRunner
from agent_framework_evaluator.runtime.setup_loader import load_setup_module
from agent_framework_evaluator.session_manager import session_manager

_WEB_DIR = Path(__file__).resolve().parent / "web"
_executor = ThreadPoolExecutor(max_workers=4)


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
        rec = session_manager.pop(session_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown session")
        _finalize_session_record(rec)
        return {"status": "closed"}

    @app.get("/api/agents")
    def list_agents(env_path: str = ".env") -> dict[str, list[str]]:
        class _DiscoveredDriver:
            def decide(self, **kwargs: Any) -> None:
                raise RuntimeError("catalog probe only")

            def set_trace_callbacks(self, **kwargs: Any) -> None:
                pass

        cfg = load_host_config(env_path)
        host = AgentHost.create(model_driver=_DiscoveredDriver(), config=cfg, mcp_enabled=False)
        host.agent_registry.discover()
        return {"agents": sorted(host.agent_registry.list_names())}

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
                    ev = await asyncio.wait_for(trace_queue.get(), timeout=0.1)
                    await websocket.send_text(json.dumps({"type": "trace", "event": ev}))
                except asyncio.TimeoutError:
                    pass
                for item in rec.comm.drain_outbox():
                    await websocket.send_text(json.dumps({"type": "outbox", "item": item}))

        pumper = asyncio.create_task(pump_outbox_and_traces())
        try:
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                if msg.get("type") == "user_input":
                    rec.comm.submit_user_input(msg.get("text"))
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

                    result = await asyncio.get_running_loop().run_in_executor(_executor, work)
                    await websocket.send_text(json.dumps({"type": "result", "payload": result}))
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
            _finalize_session_record(rec)

    return app


app = create_app()
