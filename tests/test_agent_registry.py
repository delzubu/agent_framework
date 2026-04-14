"""Tests for AgentRegistry: discovery, resolution order, reload."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_framework.agent_registry import AgentRegistry
from agent_framework.agents.helpers import AgentMarkdownError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_AGENT_MD_TEMPLATE = """\
---
id: {agent_id}
role: tester
parameters:
  instruction:
    description: instruction
    required: true
---
You are a tester.
---
<agent_input><instruction>{{{{instruction}}}}</instruction></agent_input>
"""


def _write_agent_md(directory: Path, filename: str, agent_id: str) -> Path:
    md_path = directory / filename
    md_path.write_text(_AGENT_MD_TEMPLATE.format(agent_id=agent_id), encoding="utf-8")
    return md_path


class _FakeConfig:
    default_provider = "openai"
    default_model = ("gpt-4o-mini",)
    agent_directory: str | None = None
    agent_models: dict = {}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestAgentRegistryDiscovery:
    def test_discover_builds_catalog(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(agents_dir, "alpha.md", "alpha")
        cfg = _FakeConfig()
        cfg.agent_directory = str(agents_dir)
        registry = AgentRegistry.from_config(cfg)
        registry.discover()
        assert "alpha" in registry.list_names()

    def test_discover_skips_missing_directory(self):
        class FakeCfg(_FakeConfig):
            agent_directory = "/nonexistent"

        registry = AgentRegistry.from_config(FakeCfg())
        registry.discover()
        assert registry.list_names() == ()

    def test_discover_first_dir_wins_on_duplicate(self, tmp_path: Path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        _write_agent_md(dir1, "agent.md", "my-agent")
        _write_agent_md(dir2, "agent.md", "my-agent")
        registry = AgentRegistry(directories=(dir1, dir2), config=None)
        registry.discover()
        assert registry.list_names().count("my-agent") == 1

    def test_discover_multiple_agents(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        for i in range(4):
            _write_agent_md(agents_dir, f"agent{i}.md", f"agent-{i}")
        registry = AgentRegistry(directories=(agents_dir,), config=None)
        registry.discover()
        assert len(registry.list_names()) == 4


# ---------------------------------------------------------------------------
# get() resolution order
# ---------------------------------------------------------------------------


class TestAgentRegistryGet:
    def test_get_from_catalog(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(agents_dir, "beta.md", "beta")
        registry = AgentRegistry(directories=(agents_dir,), config=None)
        registry.discover()
        agent = registry.get("beta")
        assert agent.agent_id == "beta"

    def test_get_from_cache(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(agents_dir, "gamma.md", "gamma")
        registry = AgentRegistry(directories=(agents_dir,), config=None)
        registry.discover()
        first = registry.get("gamma")
        second = registry.get("gamma")
        assert first is second

    def test_get_by_explicit_path(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        md = _write_agent_md(agents_dir, "delta.md", "delta")
        registry = AgentRegistry(directories=(), config=None)
        # Pass the absolute path as agent_id
        agent = registry.get(str(md))
        assert agent.agent_id == "delta"

    def test_get_by_sibling_path(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(agents_dir, "epsilon.md", "epsilon")
        registry = AgentRegistry(directories=(), config=None)
        agent = registry.get("epsilon", base_dir=agents_dir)
        assert agent.agent_id == "epsilon"

    def test_get_from_default_directory(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(agents_dir, "zeta.md", "zeta")

        class FakeCfg(_FakeConfig):
            agent_directory = str(agents_dir)

        registry = AgentRegistry(directories=(), config=FakeCfg())
        agent = registry.get("zeta")
        assert agent.agent_id == "zeta"

    def test_get_unknown_raises_key_error(self):
        registry = AgentRegistry(directories=(), config=None)
        with pytest.raises(KeyError, match="Unknown agent"):
            registry.get("nonexistent")

    def test_get_applies_model_override(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(agents_dir, "eta.md", "eta")

        class FakeCfg(_FakeConfig):
            agent_directory = str(agents_dir)
            agent_models = {"eta": ("gpt-4o",)}

        registry = AgentRegistry(directories=(agents_dir,), config=FakeCfg())
        registry.discover()
        agent = registry.get("eta")
        assert agent.model_names == ("gpt-4o",)


# ---------------------------------------------------------------------------
# list_names
# ---------------------------------------------------------------------------


class TestAgentRegistryListNames:
    def test_list_names_sorted(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        for name in ("zebra", "apple", "mango"):
            _write_agent_md(agents_dir, f"{name}.md", name)
        registry = AgentRegistry(directories=(agents_dir,), config=None)
        registry.discover()
        assert registry.list_names() == ("apple", "mango", "zebra")


# ---------------------------------------------------------------------------
# reload
# ---------------------------------------------------------------------------


class TestAgentRegistryReload:
    def test_reload_picks_up_new_files(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        registry = AgentRegistry(directories=(agents_dir,), config=None)
        registry.discover()
        assert registry.list_names() == ()

        _write_agent_md(agents_dir, "new_agent.md", "new-agent")
        registry.reload()
        assert "new-agent" in registry.list_names()

    def test_reload_clears_cache(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(agents_dir, "theta.md", "theta")
        registry = AgentRegistry(directories=(agents_dir,), config=None)
        registry.discover()
        agent1 = registry.get("theta")
        registry.reload()
        agent2 = registry.get("theta")
        # After reload, new instance loaded
        assert agent2.agent_id == "theta"
        assert agent1 is not agent2


class TestAgentMarkdownLayout:
    def test_get_raises_agent_markdown_error_when_only_two_delimiters_and_leading_fence(self, tmp_path: Path) -> None:
        """Two '---' with YAML between fences 1–2 needs a third '---' before user template."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        bad = agents_dir / "broken.md"
        bad.write_text(
            "---\nid: broken\nrole: x\n---\nSystem only — missing third --- and user template.\n",
            encoding="utf-8",
        )
        registry = AgentRegistry(directories=(agents_dir,), config=None)
        registry.discover()
        with pytest.raises(AgentMarkdownError) as excinfo:
            registry.get("broken")
        err = excinfo.value
        assert err.source_path.name == "broken.md"
        assert "third" in err.detail.lower() or "leading" in err.detail.lower()
        assert err.hint

    def test_get_loads_two_delimiter_layout_yaml_before_first_fence(self, tmp_path: Path) -> None:
        """YAML at BOF (no opening ---), then --- / system / --- / user — valid three-part split."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        ok = agents_dir / "implicit-yaml.md"
        ok.write_text(
            "id: implicit\nrole: r\ndescription: d\n"
            "parameters:\n"
            "  instruction:\n"
            "    description: i\n"
            "    required: true\n"
            "---\nSystem line\n"
            "---\nUser {{instruction}}\n",
            encoding="utf-8",
        )
        registry = AgentRegistry(directories=(agents_dir,), config=None)
        registry.discover()
        agent = registry.get("implicit")
        assert agent.system_prompt.strip() == "System line"
        assert "{{instruction}}" in agent.user_prompt_template
