"""Built-in Grep tool — search file contents with regex."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent_framework.builtin_tools.base import build_definition
from agent_framework.tool import Tool, ToolParameter

_MAX_RESULTS = 1000
_MAX_OUTPUT_CHARS = 80_000

_DEFINITION = build_definition(
    "Grep",
    "Search for a regex pattern in file contents.",
    [
        ToolParameter("pattern", "Regular expression pattern to search for.", required=True),
        ToolParameter("path", "File or directory to search. Defaults to current directory.", required=False),
        ToolParameter("glob", "Glob pattern to filter files (e.g. '*.py').", required=False),
        ToolParameter(
            "output_mode",
            "Output mode: 'content' (matching lines), 'files_with_matches' (file paths only), 'count'. Default: 'files_with_matches'.",
            required=False,
        ),
        ToolParameter("case_insensitive", "Case-insensitive matching. Default: false.", required=False, value_type="boolean"),
        ToolParameter("context", "Number of context lines before and after each match.", required=False, value_type="integer"),
    ],
)


class GrepTool(Tool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        raw_pattern = arguments.get("pattern", "")
        if not raw_pattern:
            return "Error: pattern is required."
        search_path = Path(arguments.get("path") or ".")
        glob_pattern = arguments.get("glob") or "**/*"
        output_mode = str(arguments.get("output_mode") or "files_with_matches")
        case_insensitive = bool(arguments.get("case_insensitive", False))
        context_lines = int(arguments.get("context") or 0)

        flags = re.IGNORECASE if case_insensitive else 0
        try:
            compiled = re.compile(raw_pattern, flags)
        except re.error as exc:
            return f"Error: invalid regex pattern — {exc}"

        # Collect files
        if search_path.is_file():
            files = [search_path]
        elif search_path.is_dir():
            files = sorted(search_path.glob(glob_pattern))
            files = [f for f in files if f.is_file()]
        else:
            return f"Error: path not found: {search_path}"

        results: list[str] = []
        total_count = 0

        for file_path in files:
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = text.splitlines()
            matched_indices = [i for i, line in enumerate(lines) if compiled.search(line)]
            if not matched_indices:
                continue

            total_count += len(matched_indices)

            if output_mode == "files_with_matches":
                results.append(str(file_path))
            elif output_mode == "count":
                results.append(f"{file_path}: {len(matched_indices)}")
            else:  # content
                shown: set[int] = set()
                for idx in matched_indices:
                    start = max(0, idx - context_lines)
                    end = min(len(lines) - 1, idx + context_lines)
                    for j in range(start, end + 1):
                        shown.add(j)
                for j in sorted(shown):
                    prefix = ">" if compiled.search(lines[j]) else " "
                    results.append(f"{file_path}:{j + 1}{prefix}{lines[j]}")

            if len(results) >= _MAX_RESULTS:
                results.append(f"... (truncated at {_MAX_RESULTS} results)")
                break

        if not results:
            return "(no matches)"
        output = "\n".join(results)
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[:_MAX_OUTPUT_CHARS] + "\n... (truncated)"
        return output


def build() -> GrepTool:
    return GrepTool(definition=_DEFINITION)
