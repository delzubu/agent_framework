"""Tests for ToolRegistry: discovery, precedence, register/get/list, reload."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_framework.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_tool_md(directory: Path, name: str, tool_id: str | None = None) -> Path:
    """Write a minimal tool markdown file."""
    md_path = directory / f"{name}.md"
    if tool_id is not None:
        md_path.write_text(
            f"---\nid: {tool_id}\ndescription: A tool\n---\nBody.\n",
            encoding="utf-8",
        )
    else:
        md_path.write_text("No frontmatter here.\n", encoding="utf-8")
    return md_path


def _make_fake_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestToolRegistryDiscovery:
    def test_discover_builds_catalog_from_frontmatter_id(self, tmp_path: Path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_tool_md(tools_dir, "my_tool", tool_id="MyTool")
        registry = ToolRegistry(directories=(tools_dir,))
        registry.discover()
        assert "MyTool" in registry.list_names()

    def test_discover_falls_back_to_file_stem(self, tmp_path: Path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_tool_md(tools_dir, "fallback_tool", tool_id=None)
        registry = ToolRegistry(directories=(tools_dir,))
        registry.discover()
        assert "fallback_tool" in registry.list_names()

    def test_discover_skips_missing_directory(self):
        registry = ToolRegistry(directories=(Path("/nonexistent/path"),))
        registry.discover()  # Should not raise
        assert registry.list_names() == ()

    def test_discover_first_dir_wins_on_duplicate(self, tmp_path: Path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        _write_tool_md(dir1, "alpha", tool_id="Alpha")
        _write_tool_md(dir2, "alpha", tool_id="Alpha")
        registry = ToolRegistry(directories=(dir1, dir2))
        registry.discover()
        names = registry.list_names()
        assert names.count("Alpha") == 1

    def test_discover_multiple_tools(self, tmp_path: Path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        for i in range(3):
            _write_tool_md(tools_dir, f"tool_{i}", tool_id=f"Tool{i}")
        registry = ToolRegistry(directories=(tools_dir,))
        registry.discover()
        assert len(registry.list_names()) == 3


# ---------------------------------------------------------------------------
# Programmatic registration
# ---------------------------------------------------------------------------


class TestToolRegistryRegister:
    def test_register_adds_to_programmatic(self):
        registry = ToolRegistry(directories=())
        tool = _make_fake_tool("MyTool")
        registry.register(tool)
        assert "MyTool" in registry.list_names()

    def test_register_overrides_on_duplicate(self):
        registry = ToolRegistry(directories=())
        tool1 = _make_fake_tool("Alpha")
        tool2 = _make_fake_tool("Alpha")
        registry.register(tool1)
        registry.register(tool2)
        assert registry.get("Alpha") is tool2

    def test_programmatic_takes_precedence_over_catalog(self, tmp_path: Path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_tool_md(tools_dir, "my_tool", tool_id="MyTool")
        registry = ToolRegistry(directories=(tools_dir,))
        registry.discover()
        prog_tool = _make_fake_tool("MyTool")
        registry.register(prog_tool)
        assert registry.get("MyTool") is prog_tool


# ---------------------------------------------------------------------------
# get() resolution
# ---------------------------------------------------------------------------


class TestToolRegistryGet:
    def test_get_programmatic_tool(self):
        registry = ToolRegistry(directories=())
        tool = _make_fake_tool("Bash")
        registry.register(tool)
        assert registry.get("Bash") is tool

    def test_get_unknown_raises_key_error(self):
        registry = ToolRegistry(directories=())
        with pytest.raises(KeyError, match="Unknown tool"):
            registry.get("Nonexistent")

    def test_get_caches_loaded_tool(self, tmp_path: Path, monkeypatch):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_tool_md(tools_dir, "cached_tool", tool_id="CachedTool")
        registry = ToolRegistry(directories=(tools_dir,))
        registry.discover()

        fake_tool = _make_fake_tool("CachedTool")
        load_calls = []

        def fake_from_name(name, directory):
            load_calls.append(name)
            return fake_tool

        from agent_framework import tool as tool_module
        monkeypatch.setattr(tool_module.Tool, "from_name", staticmethod(fake_from_name))

        first = registry.get("CachedTool")
        second = registry.get("CachedTool")
        assert first is second
        assert len(load_calls) == 1


# ---------------------------------------------------------------------------
# list_names / get_all
# ---------------------------------------------------------------------------


class TestToolRegistryList:
    def test_list_names_includes_programmatic_and_catalog(self, tmp_path: Path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_tool_md(tools_dir, "disk_tool", tool_id="DiskTool")
        registry = ToolRegistry(directories=(tools_dir,))
        registry.discover()
        registry.register(_make_fake_tool("ProgTool"))
        names = registry.list_names()
        assert "DiskTool" in names
        assert "ProgTool" in names

    def test_list_names_sorted(self):
        registry = ToolRegistry(directories=())
        for name in ("Zebra", "Apple", "Mango"):
            registry.register(_make_fake_tool(name))
        assert registry.list_names() == ("Apple", "Mango", "Zebra")

    def test_get_all_returns_programmatic_tools(self):
        registry = ToolRegistry(directories=())
        tools = [_make_fake_tool(f"T{i}") for i in range(3)]
        for t in tools:
            registry.register(t)
        result = registry.get_all()
        assert set(t.name for t in result) == {"T0", "T1", "T2"}


# ---------------------------------------------------------------------------
# reload
# ---------------------------------------------------------------------------


class TestToolRegistryReload:
    def test_reload_clears_all_state(self, tmp_path: Path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        _write_tool_md(tools_dir, "alpha", tool_id="Alpha")
        registry = ToolRegistry(directories=(tools_dir,))
        registry.discover()
        registry.register(_make_fake_tool("Prog"))
        assert "Alpha" in registry.list_names()
        assert "Prog" in registry.list_names()

        registry.reload()
        # After reload programmatic is also cleared; disk tools are re-discovered
        assert "Alpha" in registry.list_names()
        assert "Prog" not in registry.list_names()

    def test_reload_picks_up_new_files(self, tmp_path: Path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        registry = ToolRegistry(directories=(tools_dir,))
        registry.discover()
        assert registry.list_names() == ()

        _write_tool_md(tools_dir, "new_tool", tool_id="NewTool")
        registry.reload()
        assert "NewTool" in registry.list_names()


# ---------------------------------------------------------------------------
# from_config
# ---------------------------------------------------------------------------


class TestToolRegistryFromConfig:
    def test_from_config_with_directory(self, tmp_path: Path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        class FakeConfig:
            tools_directory = str(tools_dir)

        registry = ToolRegistry.from_config(FakeConfig())
        assert len(registry.directories) == 1

    def test_from_config_without_directory(self):
        class FakeConfig:
            tools_directory = None

        registry = ToolRegistry.from_config(FakeConfig())
        assert registry.directories == ()
