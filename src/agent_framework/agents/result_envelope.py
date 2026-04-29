"""Typed envelope for subagent results.

Replaces the ad-hoc serialization that mixed human-readable message and
structured response into a single string.
"""

from __future__ import annotations

import json
from typing import Any


def render_subagent_envelope(*, message: str, response: dict[str, Any] | None) -> str:
    """Render a typed subagent result envelope.

    When *response* is ``None`` the plain *message* is returned unchanged for
    backward-compat with callers that treat the result as a prose string.

    When *response* is set the envelope format is::

        <subagent_result message="...escaped...">
        {"json": "payload"}
        </subagent_result>

    Parent agents can extract whichever channel they need:
    - Prose summary: read the ``message`` attribute.
    - Structured payload: parse the element text content as JSON.
    """
    if response is None:
        return message
    escaped_message = message.replace('"', "&quot;").replace("\n", "&#10;")
    response_json = json.dumps(response, ensure_ascii=False)
    return f'<subagent_result message="{escaped_message}">\n{response_json}\n</subagent_result>'


__all__ = ["render_subagent_envelope"]
