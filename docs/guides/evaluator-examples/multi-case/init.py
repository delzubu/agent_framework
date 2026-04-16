"""Multi-case initializer demonstrating LLM + programmatic evaluation.

Cases live in the cases/ subdirectory next to this file.  Case 02 uses
``code_evaluator: example_non_empty`` which is registered below.  Case 03
demonstrates ``result_field: parameters`` — the evaluator reads the agent's
``parameters`` field instead of the default ``message`` field.

Run all cases:
    python -m agent_framework_evaluator evaluate --env .env --initializer path/to/multi-case/init.py

Run with verbose output:
    python -m agent_framework_evaluator evaluate --env .env --initializer path/to/multi-case/init.py --verbose

Save results to a file:
    python -m agent_framework_evaluator evaluate --env .env --initializer path/to/multi-case/init.py --output results.json
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_framework_evaluator.case_markdown import MarkdownCaseLoader

_HERE = Path(__file__).resolve().parent

CASES_GLOB = "cases/*.md"
DEFAULT_AGENT = "root"
DEFAULT_EVAL_MODEL = "gpt-4o-mini"

# --- Programmatic evaluator registry ---
# Keys here match the ``code_evaluator`` frontmatter field in case files.
_EVALUATORS: dict[str, Callable[..., Any]] = {}


def _evaluator(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: register a programmatic evaluator by name."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        _EVALUATORS[name] = fn
        return fn

    return deco


@_evaluator("example_non_empty")
def _check_non_empty(prompt: str, agent_message: str) -> dict[str, Any]:
    """Score based on whether the agent produced any non-empty output."""
    ok = bool(str(agent_message).strip())
    return {
        "score": 8 if ok else 2,
        "result": "Response is non-empty." if ok else "Response is empty — fail.",
        "evaluation": [
            {
                "criteria": "Agent produced non-empty output",
                "passed": ok,
                "reason": "Non-empty response received." if ok else "Response was blank.",
            }
        ],
    }


_LOADER = MarkdownCaseLoader(_HERE, CASES_GLOB, _EVALUATORS)


def get_test_cases() -> list[dict[str, Any]]:
    """Return all discovered test cases (cached; invalidates when files change)."""
    return _LOADER.get_test_cases()
