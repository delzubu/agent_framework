from __future__ import annotations

import os

import pytest

from agent_framework.model import ModelResponse

# Avoid writing agent-host-*.jsonl under ./logs for every AgentHost.from_env() test.
os.environ.setdefault("AGENT_HOST_RECEIVE_LOG", "0")


class FakeModelDriver:
    def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        return ModelResponse(payload={"kind": "final_message", "message": "ok"}, raw_text="ok")

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        pass


@pytest.fixture
def fake_model_driver():
    return FakeModelDriver()
