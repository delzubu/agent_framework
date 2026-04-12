"""Built-in WebFetch tool — fetch a URL and return text content."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.error import URLError

from agent_framework.builtin_tools.base import PermissionGatedTool, build_definition
from agent_framework.tool import ToolParameter
from agent_framework.user_communication import PermissionRequest

_MAX_CHARS = 50_000

_DEFINITION = build_definition(
    "WebFetch",
    "Fetch a URL and return its text content (HTML stripped to plain text).",
    [
        ToolParameter("url", "The URL to fetch.", required=True),
        ToolParameter("prompt", "Optional description of what to look for on the page.", required=False),
    ],
)

# Simple HTML tag stripper
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _strip_html(raw: str) -> str:
    text = html.unescape(raw)
    text = _TAG_RE.sub(" ", text)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


class WebFetchTool(PermissionGatedTool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        url = arguments.get("url", "")
        if not url:
            return "Error: url is required."
        request = PermissionRequest(
            tool_name="WebFetch",
            action="network",
            resource=str(url),
            summary=f"Fetch {url}",
            details={"url": url},
        )
        if not self._request_permission(host, request):
            return f"Permission denied: fetch {url}"
        return _fetch(str(url))


def _fetch(url: str) -> str:
    """Fetch a URL, preferring httpx but falling back to urllib."""
    try:
        import httpx
        with httpx.Client(follow_redirects=True, timeout=30) as client:
            response = client.get(url)
            response.raise_for_status()
            text = _strip_html(response.text)
    except ImportError:
        from urllib.request import urlopen
        try:
            with urlopen(url, timeout=30) as resp:
                raw_bytes = resp.read(1_000_000)
                charset = "utf-8"
                content_type = resp.headers.get("Content-Type", "")
                if "charset=" in content_type:
                    charset = content_type.split("charset=")[-1].split(";")[0].strip()
                text = _strip_html(raw_bytes.decode(charset, errors="replace"))
        except URLError as exc:
            return f"Error fetching URL: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Error fetching URL: {exc}"
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + "\n... (truncated)"
    return text or "(empty response)"


def build() -> WebFetchTool:
    return WebFetchTool(definition=_DEFINITION)
