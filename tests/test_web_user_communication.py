from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agent_framework.tracing import CompositeRuntimeTracer, TraceEvent
from agent_framework.tracing_bridge import active_tracer_scope
from agent_framework.web_communication import WebUserCommunication


class _Recorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def consume(self, event: TraceEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_web_user_communication_waits_for_answer() -> None:
    comm = WebUserCommunication(session_id="sess-1")

    async def answer_later() -> None:
        await asyncio.sleep(0.01)
        assert comm.submit_user_input("hello") is True

    task = asyncio.create_task(comm.read_user_input("Question? "))
    await answer_later()
    result = await task
    assert result == "hello"
    drained = comm.drain_outbox()
    assert len(drained) == 1
    assert drained[0]["kind"] == "prompt"
    assert "prompt_id" in drained[0]


@pytest.mark.asyncio
async def test_web_user_communication_preserves_host_metadata() -> None:
    comm = WebUserCommunication(session_id="sess-1")

    async def answer_later() -> None:
        await asyncio.sleep(0.01)
        assert comm.submit_user_input("hello", prompt_id="pid-123") is True

    task = asyncio.create_task(
        comm.read_user_input(
            "Question? ",
            prompt_id="pid-123",
            metadata={"agent_id": "deck_review_intake", "run_id": "run-1", "intent": "information_request"},
        )
    )
    await answer_later()
    result = await task
    assert result == "hello"
    drained = comm.drain_outbox()
    assert drained[0]["prompt_id"] == "pid-123"
    assert drained[0]["agent_id"] == "deck_review_intake"
    assert drained[0]["run_id"] == "run-1"
    assert drained[0]["intent"] == "information_request"


def test_web_user_communication_cross_thread_submit() -> None:
    """Worker thread runs asyncio.run(read_user_input); main thread submits (evaluator pattern)."""
    comm = WebUserCommunication(session_id="sess-x")
    result_holder: dict[str, str | None] = {}

    def worker() -> None:
        async def run() -> None:
            result_holder["v"] = await comm.read_user_input("q")

        asyncio.run(run())

    t = threading.Thread(target=worker)
    t.start()
    pid = None
    for _ in range(200):
        time.sleep(0.01)
        items = comm.drain_outbox()
        if items:
            pid = items[0]["prompt_id"]
            break
    assert pid is not None
    assert comm.submit_user_input("cross", prompt_id=pid) is True
    t.join(timeout=5.0)
    assert result_holder.get("v") == "cross"


@pytest.mark.asyncio
async def test_web_user_communication_records_outgoing_messages() -> None:
    comm = WebUserCommunication(session_id="sess-1")
    await comm.send_message("done")
    queued = comm.drain_outbox()
    assert queued[0]["text"] == "done"


@pytest.mark.asyncio
async def test_web_user_communication_publishes_user_channel_when_tracer_active() -> None:
    recorder = _Recorder()
    tracer = CompositeRuntimeTracer(subscribers=[recorder])
    comm = WebUserCommunication(session_id="sess-x")
    with active_tracer_scope(tracer, None):
        await comm.send_message("hello")
    kinds = [e.kind for e in recorder.events]
    assert "user.message_sent" in kinds
