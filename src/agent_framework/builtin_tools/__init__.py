"""Built-in tool implementations for AgentHost."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_framework.tool_registry import ToolRegistry

BUILTIN_TOOL_NAMES: tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Bash",
    "Glob",
    "Grep",
    "WebFetch",
)


def register_builtin_tools(registry: "ToolRegistry") -> None:
    """Register all built-in tools into the given ToolRegistry."""
    from agent_framework.builtin_tools.read_tool import build as build_read
    from agent_framework.builtin_tools.write_tool import build as build_write
    from agent_framework.builtin_tools.edit_tool import build as build_edit
    from agent_framework.builtin_tools.bash_tool import build as build_bash
    from agent_framework.builtin_tools.glob_tool import build as build_glob
    from agent_framework.builtin_tools.grep_tool import build as build_grep
    from agent_framework.builtin_tools.web_fetch_tool import build as build_web_fetch

    for builder in (build_read, build_write, build_edit, build_bash, build_glob, build_grep, build_web_fetch):
        tool = builder()
        registry.register(tool)


__all__ = ["BUILTIN_TOOL_NAMES", "register_builtin_tools"]
