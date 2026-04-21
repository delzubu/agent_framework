"""Host-managed memory tools."""

from __future__ import annotations

import json
from typing import Any

from agent_framework.builtin_tools.base import build_definition
from agent_framework.tool import Tool, ToolParameter


_MEMORY_GET_DEFINITION = build_definition(
    "memory_get",
    "Retrieve the full contents of a memory entry by its mem:// identifier.",
    [
        ToolParameter("uri", "Exact mem:// identifier of the memory entry.", required=True),
    ],
)

_MEMORY_LIST_DEFINITION = build_definition(
    "memory_list",
    "List visible memory identifiers with titles and summaries.",
    [
        ToolParameter("scope_kind", "Optional scope kind filter.", required=False),
        ToolParameter("scope_key", "Optional scope key filter.", required=False),
        ToolParameter("limit", "Maximum number of results to return.", required=False, value_type="integer"),
    ],
)

_MEMORY_QUERY_DEFINITION = build_definition(
    "memory_query",
    "Search visible memory identifiers and summaries for a query string.",
    [
        ToolParameter("query", "Search text to match against ids, titles, and summaries.", required=True),
        ToolParameter("limit", "Maximum number of results to return.", required=False, value_type="integer"),
    ],
)


class _MemoryGetTool(Tool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        uri = str(arguments.get("uri", "")).strip()
        if not uri:
            return "Error: uri is required."
        try:
            return host.render_memory_entry(uri)
        except (KeyError, ValueError) as exc:
            return f"Error: {exc}"


class _MemoryListTool(Tool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        limit_raw = arguments.get("limit")
        limit = int(limit_raw) if limit_raw is not None else 20
        scope_kind = str(arguments.get("scope_kind", "")).strip() or None
        scope_key = str(arguments.get("scope_key", "")).strip() or None
        try:
            refs = host.list_memory_refs(scope_kind=scope_kind, scope_key=scope_key, limit=limit)
        except ValueError as exc:
            return f"Error: {exc}"
        payload = [
            {
                "uri": ref.uri,
                "scope": ref.scope.as_text(),
                "mime_type": ref.mime_type,
                "title": ref.title,
                "summary": ref.summary,
            }
            for ref in refs
        ]
        return json.dumps(payload, indent=2, ensure_ascii=False)


class _MemoryQueryTool(Tool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "Error: query is required."
        limit_raw = arguments.get("limit")
        limit = int(limit_raw) if limit_raw is not None else 10
        hits = host.query_memory(query, limit=limit)
        payload = [
            {
                "uri": hit.ref.uri,
                "scope": hit.ref.scope.as_text(),
                "mime_type": hit.ref.mime_type,
                "title": hit.ref.title,
                "summary": hit.ref.summary,
                "score": hit.score,
            }
            for hit in hits
        ]
        return json.dumps(payload, indent=2, ensure_ascii=False)


def register_memory_tools(registry: Any) -> None:
    """Register read-side memory tools into a ToolRegistry."""
    registry.register(_MemoryGetTool(definition=_MEMORY_GET_DEFINITION))
    registry.register(_MemoryListTool(definition=_MEMORY_LIST_DEFINITION))
    registry.register(_MemoryQueryTool(definition=_MEMORY_QUERY_DEFINITION))


__all__ = ["register_memory_tools"]
