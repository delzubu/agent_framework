from __future__ import annotations

import asyncio

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
        comm.submit_user_input("hello")

    task = asyncio.create_task(comm.read_user_input("Question? "))
    await answer_later()
    result = await task
    assert result == "hello"


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
