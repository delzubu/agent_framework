"""Integration: AgentHost.run_agent expands @filename tokens in the initial_instruction."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_mock_agent():
    mock_agent = MagicMock()
    mock_agent.run.return_value = MagicMock(status="completed", message="ok", decision=None)
    return mock_agent


def _patch_host_methods(monkeypatch, mock_agent):
    """Patch AgentHost at class level so slots=True doesn't block us."""
    from agent_framework.host import AgentHost

    monkeypatch.setattr(AgentHost, "get_agent", lambda self, _: mock_agent)
    monkeypatch.setattr(AgentHost, "_agent_with_runtime_tracing", lambda self, a: a)
    monkeypatch.setattr(AgentHost, "_next_prompt_counter", lambda self: 1)


def _rendered_prompt(mock_agent) -> str:
    call_kwargs = mock_agent.run.call_args
    return call_kwargs.kwargs.get("rendered_prompt_override", "")


def test_host_has_file_ref_resolver_field():
    from agent_framework.file_reference import DefaultFileReferenceResolver
    from agent_framework.host import AgentHost, HostConfig

    host = AgentHost(config=HostConfig())
    assert isinstance(host.file_ref_resolver, DefaultFileReferenceResolver)


def test_host_file_ref_resolver_can_be_set_to_none():
    from agent_framework.host import AgentHost, HostConfig

    host = AgentHost(config=HostConfig(), file_ref_resolver=None)
    assert host.file_ref_resolver is None


def test_run_agent_expands_initial_instruction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """run_agent should expand @refs before the agent sees the prompt."""
    from pathlib import Path as P
    from agent_framework.host import AgentHost, HostConfig

    captured: list[str] = []

    class CapturingResolver:
        def resolve(self, path: P) -> str:
            captured.append(str(path))
            return f"[{path.name}]"

    (tmp_path / "ctx.txt").write_text("context data", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    mock_agent = _make_mock_agent()
    _patch_host_methods(monkeypatch, mock_agent)

    host = AgentHost(config=HostConfig(), file_ref_resolver=CapturingResolver())
    host.run_agent("someagent", initial_instruction="See @ctx.txt for details")

    assert len(captured) == 1
    assert captured[0].endswith("ctx.txt")
    rendered = _rendered_prompt(mock_agent)
    assert "[ctx.txt]" in rendered
    assert "@ctx.txt" not in rendered


def test_run_agent_no_resolver_leaves_instruction_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When file_ref_resolver is None, @refs are passed through unchanged."""
    from agent_framework.host import AgentHost, HostConfig

    (tmp_path / "ctx.txt").write_text("data", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    mock_agent = _make_mock_agent()
    _patch_host_methods(monkeypatch, mock_agent)

    host = AgentHost(config=HostConfig(), file_ref_resolver=None)
    host.run_agent("someagent", initial_instruction="See @ctx.txt for details")

    rendered = _rendered_prompt(mock_agent)
    assert "@ctx.txt" in rendered  # unchanged when resolver is None


def test_run_agent_none_instruction_not_expanded(monkeypatch: pytest.MonkeyPatch):
    """None initial_instruction must not cause an error."""
    from agent_framework.host import AgentHost, HostConfig

    mock_agent = _make_mock_agent()
    _patch_host_methods(monkeypatch, mock_agent)

    host = AgentHost(config=HostConfig())
    host.run_agent("someagent", initial_instruction=None)
    mock_agent.run.assert_called_once()
    rendered = _rendered_prompt(mock_agent)
    assert rendered == ""
