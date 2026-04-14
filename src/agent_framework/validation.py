"""JSON validation utilities for model output processing.

Provides helpers to parse JSON from model responses (stripping markdown
fences) and a retry-on-failure pattern for when the model returns malformed
JSON.

Usage::

    from agent_framework.validation import parse_json_content, validate_and_retry

    # Simple parse
    data = parse_json_content(raw_text)

    # Parse + validate + retry once on failure
    result = await validate_and_retry(
        content=response.raw_text,
        validator=MyPydanticModel.model_validate,
        retry_fn=lambda err: host.complete_async(
            messages=[
                *original_messages,
                {"role": "assistant", "content": raw_text},
                {"role": "user", "content": f"Fix this error: {err}"},
            ]
        ),
    )
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


def parse_json_content(content: str) -> Any:
    """Parse JSON from model output, stripping markdown code fences.

    Handles bare JSON, ````` ```json ... ``` `````, and ````` ``` ... ``` `````
    fenced blocks.

    **Intentional (confirmed):** Fence stripping and the optional leading ``json``
    label are **transport normalization** only — they do not reinterpret model
    semantics; the remaining text must still be valid JSON for :func:`json.loads`.

    Args:
        content: Raw model output text.

    Returns:
        Parsed Python object.

    Raises:
        json.JSONDecodeError: If the text cannot be parsed as JSON after
            fence stripping.
    """
    return json.loads(_normalize_json_text(content))


# TODO: Revisit validate_and_retry — audit call sites, whether a second model round
# for malformed JSON fits the strict validate-and-fail policy for agent decisions,
# and document or narrow scope (deferred; intentionally unchanged for now).


async def validate_and_retry(
    content: str,
    validator: Callable[[Any], T],
    retry_fn: Callable[[str], Awaitable[str]],
) -> T:
    """Parse JSON, validate, and retry once on failure.

    Calls ``validator`` on the parsed JSON.  If that raises any exception,
    calls ``retry_fn`` with a description of the error to obtain new model
    output, then tries ``validator`` once more.  Raises if the second attempt
    also fails.

    Args:
        content: Raw model output text from the first call.
        validator: Callable that accepts a parsed JSON value and returns a
            typed result.  Should raise on invalid input (e.g. Pydantic
            ``ValidationError``, ``ValueError``).
        retry_fn: Async callable that accepts an error description string and
            returns new raw model output text to try.

    Returns:
        Validated result of type ``T``.

    Raises:
        The validator's exception if the retry attempt also fails.
    """
    try:
        parsed = parse_json_content(content)
        return validator(parsed)
    except Exception as first_error:
        error_desc = f"JSON validation failed: {first_error}"
        retry_content = await retry_fn(error_desc)
        parsed = parse_json_content(retry_content)
        return validator(parsed)


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


__all__ = ["parse_json_content", "validate_and_retry"]
