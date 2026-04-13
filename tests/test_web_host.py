from __future__ import annotations

from agent_framework.host import AgentHost
from agent_framework.tracing import CompositeRuntimeTracer
from agent_framework.web_communication import WebUserCommunication
from agent_framework.web_host import create_web_host


def test_create_web_host_wires_user_comm_and_tracer(fake_model_driver) -> None:
    tracer = CompositeRuntimeTracer()
    comm = WebUserCommunication(session_id="sess-1")
    host = create_web_host(
        model_driver=fake_model_driver,
        user_comm=comm,
        runtime_tracer=tracer,
    )
    assert isinstance(host, AgentHost)
    assert host.user_comm is comm
    assert host.runtime_tracer is tracer
