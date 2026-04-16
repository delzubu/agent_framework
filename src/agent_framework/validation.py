"""JSON normalization utilities for model output processing."""

from __future__ import annotations


def _normalize_json_text(raw_text: str) -> str:
    """Extract JSON text from plain or fenced model responses.

    This is the canonical implementation.  ``model.py`` delegates to this
    function to keep a single source of truth.

    **Intentional (confirmed):** Removing markdown fences / a ``json`` line prefix
    is agreed transport cleanup, not inference of a different structured intent
    from prose.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    return text
