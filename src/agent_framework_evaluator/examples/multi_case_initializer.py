"""Reference multi-case initializer: discovers ``.md`` cases via glob and caches parsed data.

Copy this file and the ``cases/`` directory into ``AGENT_EVAL_INITIALIZER_DIR``, or reference
this path directly in the evaluator UI.

Case files use ``---`` on its own line to separate: YAML frontmatter, prompt, evaluation criteria.
For richer frontmatter, replace parsing in :mod:`agent_framework_evaluator.case_markdown` with ``yaml.safe_load``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_framework_evaluator.case_markdown import MarkdownCaseLoader

# Relative to this file: all matching markdown files become test cases.
CASES_GLOB = "cases/*.md"

# Default agent id when running from this initializer (SessionRunner / UI).
DEFAULT_AGENT = "root"

# Preferred evaluator model (comma-separated fallbacks allowed). Used when the host loads
# the evaluator LLM for ``/api/evaluate-case``; overrides ``AGENT_EVAL_MODEL`` from ``.env``
# when the initializer defines it.
DEFAULT_EVAL_MODEL = "gpt-4o-mini"

_HERE = Path(__file__).resolve().parent

_EVALUATORS: dict[str, Callable[..., Any]] = {}


def _evaluator(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a programmatic evaluator callable by name (referenced from case frontmatter)."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        _EVALUATORS[name] = fn
        return fn

    return deco


@_evaluator("example_non_empty")
def _example_non_empty(prompt: str, agent_message: str) -> dict[str, Any]:
    """Example: score based on non-empty agent output."""
    ok = bool(str(agent_message).strip())
    return {
        "score": 8 if ok else 2,
        "result": "Response is non-empty." if ok else "Response is empty.",
        "evaluation": [
            {
                "criteria": "Agent produced non-empty output",
                "passed": ok,
                "reason": "ok" if ok else "empty",
            }
        ],
    }


_LOADER = MarkdownCaseLoader(_HERE, CASES_GLOB, _EVALUATORS)


def get_test_cases() -> list[dict[str, Any]]:
    """Return test cases; invalidate cache when any matching file path or mtime changes."""
    return _LOADER.get_test_cases()
