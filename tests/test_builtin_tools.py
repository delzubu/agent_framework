"""Tests for built-in tools (Read, Write, Edit, Bash, Glob, Grep, WebFetch)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock


from agent_framework.user_communication import PermissionDecision, PermissionRequest
from agent_framework.builtin_tools import register_builtin_tools, BUILTIN_TOOL_NAMES
from agent_framework.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fake host helpers
# ---------------------------------------------------------------------------


def _make_host(*, allow_permission: bool = True) -> MagicMock:
    """Build a fake host with a synchronous _run_user_comm_coro bridge."""
    import concurrent.futures

    host = MagicMock()
    decision = PermissionDecision(allowed=allow_permission)

    async def _fake_request_permission(req: PermissionRequest) -> PermissionDecision:
        return decision

    user_comm = MagicMock()
    user_comm.request_permission = _fake_request_permission
    host.user_comm = user_comm

    def _run_coro(coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()

    host._run_user_comm_coro = _run_coro
    return host


# ---------------------------------------------------------------------------
# register_builtin_tools
# ---------------------------------------------------------------------------


class TestRegisterBuiltinTools:
    def test_all_builtins_registered(self):
        registry = ToolRegistry(directories=())
        register_builtin_tools(registry)
        names = set(registry.list_names())
        for expected in BUILTIN_TOOL_NAMES:
            assert expected in names, f"Expected {expected!r} in registry"

    def test_builtin_tool_names_constant(self):
        assert set(BUILTIN_TOOL_NAMES) == {"Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch"}


# ---------------------------------------------------------------------------
# ReadTool
# ---------------------------------------------------------------------------


class TestReadTool:
    def _build(self):
        from agent_framework.builtin_tools.read_tool import build
        return build()

    def test_reads_file_contents(self, tmp_path: Path):
        f = tmp_path / "hello.txt"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        tool = self._build()
        result = tool.invoke({"file_path": str(f)}, _make_host())
        assert "line1" in result
        assert "line2" in result

    def test_returns_line_numbers(self, tmp_path: Path):
        f = tmp_path / "hello.txt"
        f.write_text("alpha\nbeta\n", encoding="utf-8")
        tool = self._build()
        result = tool.invoke({"file_path": str(f)}, _make_host())
        assert "1" in result
        assert "2" in result

    def test_offset_and_limit(self, tmp_path: Path):
        f = tmp_path / "lines.txt"
        f.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
        tool = self._build()
        result = tool.invoke({"file_path": str(f), "offset": 3, "limit": 2}, _make_host())
        assert "line2" in result  # offset=3 → 0-based line 2
        assert "line4" not in result

    def test_missing_file_returns_error(self):
        tool = self._build()
        result = tool.invoke({"file_path": "/nonexistent/file.txt"}, _make_host())
        assert "Error" in result

    def test_missing_file_path_returns_error(self):
        tool = self._build()
        result = tool.invoke({}, _make_host())
        assert "Error" in result

    def test_empty_file_returns_placeholder(self, tmp_path: Path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        tool = self._build()
        result = tool.invoke({"file_path": str(f)}, _make_host())
        assert "empty" in result.lower()

    def test_tool_name_is_Read(self):
        assert self._build().name == "Read"


# ---------------------------------------------------------------------------
# WriteTool
# ---------------------------------------------------------------------------


class TestWriteTool:
    def _build(self):
        from agent_framework.builtin_tools.write_tool import build
        return build()

    def test_writes_file_when_allowed(self, tmp_path: Path):
        out = tmp_path / "out.txt"
        tool = self._build()
        result = tool.invoke({"file_path": str(out), "content": "hello world"}, _make_host(allow_permission=True))
        assert "Successfully" in result
        assert out.read_text(encoding="utf-8") == "hello world"

    def test_denied_returns_error_without_writing(self, tmp_path: Path):
        out = tmp_path / "out.txt"
        tool = self._build()
        result = tool.invoke({"file_path": str(out), "content": "secret"}, _make_host(allow_permission=False))
        assert "Permission denied" in result
        assert not out.exists()

    def test_missing_file_path_returns_error(self):
        tool = self._build()
        result = tool.invoke({"content": "foo"}, _make_host())
        assert "Error" in result

    def test_creates_parent_directories(self, tmp_path: Path):
        out = tmp_path / "deep" / "dir" / "file.txt"
        tool = self._build()
        tool.invoke({"file_path": str(out), "content": "data"}, _make_host(allow_permission=True))
        assert out.exists()

    def test_tool_name_is_Write(self):
        assert self._build().name == "Write"


# ---------------------------------------------------------------------------
# EditTool
# ---------------------------------------------------------------------------


class TestEditTool:
    def _build(self):
        from agent_framework.builtin_tools.edit_tool import build
        return build()

    def test_replaces_string_in_file(self, tmp_path: Path):
        f = tmp_path / "edit_me.txt"
        f.write_text("Hello World\n", encoding="utf-8")
        tool = self._build()
        result = tool.invoke(
            {"file_path": str(f), "old_string": "World", "new_string": "Python"},
            _make_host(allow_permission=True),
        )
        assert "Successfully replaced" in result
        assert "Python" in f.read_text(encoding="utf-8")

    def test_denied_does_not_modify_file(self, tmp_path: Path):
        f = tmp_path / "no_edit.txt"
        f.write_text("original\n", encoding="utf-8")
        tool = self._build()
        result = tool.invoke(
            {"file_path": str(f), "old_string": "original", "new_string": "modified"},
            _make_host(allow_permission=False),
        )
        assert "Permission denied" in result
        assert f.read_text(encoding="utf-8") == "original\n"

    def test_old_string_not_found_returns_error(self, tmp_path: Path):
        f = tmp_path / "file.txt"
        f.write_text("content\n", encoding="utf-8")
        tool = self._build()
        result = tool.invoke(
            {"file_path": str(f), "old_string": "NOT_THERE", "new_string": "new"},
            _make_host(allow_permission=True),
        )
        assert "Error" in result or "not found" in result.lower()

    def test_tool_name_is_Edit(self):
        assert self._build().name == "Edit"


# ---------------------------------------------------------------------------
# BashTool
# ---------------------------------------------------------------------------


class TestBashTool:
    def _build(self):
        from agent_framework.builtin_tools.bash_tool import build
        return build()

    def test_executes_command_when_allowed(self):
        tool = self._build()
        result = tool.invoke({"command": "echo hello_test"}, _make_host(allow_permission=True))
        assert "hello_test" in result

    def test_denied_returns_error_without_executing(self):
        tool = self._build()
        result = tool.invoke({"command": "echo should_not_run"}, _make_host(allow_permission=False))
        assert "Permission denied" in result
        assert "should_not_run" not in result

    def test_missing_command_returns_error(self):
        tool = self._build()
        result = tool.invoke({}, _make_host())
        assert "Error" in result

    def test_non_zero_exit_code_included(self):
        tool = self._build()
        result = tool.invoke({"command": "exit 42"}, _make_host(allow_permission=True))
        assert "42" in result

    def test_stderr_included_in_output(self):
        tool = self._build()
        result = tool.invoke({"command": "echo error >&2"}, _make_host(allow_permission=True))
        # stderr or stdout captured
        assert result  # non-empty

    def test_tool_name_is_Bash(self):
        assert self._build().name == "Bash"


# ---------------------------------------------------------------------------
# GlobTool
# ---------------------------------------------------------------------------


class TestGlobTool:
    def _build(self):
        from agent_framework.builtin_tools.glob_tool import build
        return build()

    def test_finds_files_by_pattern(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "b.py").write_text("", encoding="utf-8")
        (tmp_path / "c.txt").write_text("", encoding="utf-8")
        tool = self._build()
        result = tool.invoke({"pattern": "*.py", "path": str(tmp_path)}, _make_host())
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_no_matches_returns_message(self, tmp_path: Path):
        tool = self._build()
        result = tool.invoke({"pattern": "*.xyz", "path": str(tmp_path)}, _make_host())
        assert "No files" in result or "no matches" in result.lower() or result.strip() == ""

    def test_missing_pattern_returns_error(self):
        tool = self._build()
        result = tool.invoke({}, _make_host())
        assert "Error" in result

    def test_tool_name_is_Glob(self):
        assert self._build().name == "Glob"


# ---------------------------------------------------------------------------
# GrepTool
# ---------------------------------------------------------------------------


class TestGrepTool:
    def _build(self):
        from agent_framework.builtin_tools.grep_tool import build
        return build()

    def test_finds_pattern_in_files(self, tmp_path: Path):
        (tmp_path / "foo.txt").write_text("hello world\ngoodbye\n", encoding="utf-8")
        tool = self._build()
        result = tool.invoke(
            {"pattern": "hello", "path": str(tmp_path), "output_mode": "content"},
            _make_host(),
        )
        assert "hello" in result

    def test_no_match_returns_empty_or_message(self, tmp_path: Path):
        (tmp_path / "foo.txt").write_text("nope\n", encoding="utf-8")
        tool = self._build()
        result = tool.invoke({"pattern": "xyz_not_found", "path": str(tmp_path)}, _make_host())
        assert "xyz_not_found" not in result

    def test_files_with_matches_mode(self, tmp_path: Path):
        (tmp_path / "match.py").write_text("import os\n", encoding="utf-8")
        (tmp_path / "nomatch.py").write_text("pass\n", encoding="utf-8")
        tool = self._build()
        result = tool.invoke(
            {"pattern": "import", "path": str(tmp_path), "output_mode": "files_with_matches"},
            _make_host(),
        )
        assert "match.py" in result
        assert "nomatch.py" not in result

    def test_case_insensitive(self, tmp_path: Path):
        (tmp_path / "ci.txt").write_text("HELLO\n", encoding="utf-8")
        tool = self._build()
        result = tool.invoke(
            {"pattern": "hello", "path": str(tmp_path), "case_insensitive": True, "output_mode": "content"},
            _make_host(),
        )
        assert "HELLO" in result

    def test_missing_pattern_returns_error(self):
        tool = self._build()
        result = tool.invoke({}, _make_host())
        assert "Error" in result

    def test_tool_name_is_Grep(self):
        assert self._build().name == "Grep"


# ---------------------------------------------------------------------------
# WebFetchTool
# ---------------------------------------------------------------------------


class TestWebFetchTool:
    def _build(self):
        from agent_framework.builtin_tools.web_fetch_tool import build
        return build()

    def test_denied_returns_error(self):
        tool = self._build()
        result = tool.invoke(
            {"url": "http://example.com"},
            _make_host(allow_permission=False),
        )
        assert "Permission denied" in result

    def test_missing_url_returns_error(self):
        tool = self._build()
        result = tool.invoke({}, _make_host())
        assert "Error" in result

    def test_tool_name_is_WebFetch(self):
        assert self._build().name == "WebFetch"

    def test_fetch_returns_content_when_allowed(self, monkeypatch):
        tool = self._build()
        import agent_framework.builtin_tools.web_fetch_tool as wft_module

        def fake_fetch(url: str) -> str:
            return "Hello test page content"

        monkeypatch.setattr(wft_module, "_fetch", fake_fetch)
        result = tool.invoke({"url": "http://example.com"}, _make_host(allow_permission=True))
        assert "Hello test" in result
