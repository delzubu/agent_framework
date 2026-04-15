"""Example initializer layout for markdown cases next to ``deck-review.py``.

If ``deck-review.py`` lives in ``…/scripts/eval/`` and case files in ``…/scripts/eval/eval/*.md``,
use glob ``eval/*.md`` (relative to this file).

Copy and adapt into your project (e.g. dial-agent ``deck-review.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework_evaluator.case_markdown import MarkdownCaseLoader

_HERE = Path(__file__).resolve().parent

# Matches ``eval/deck-review-01.md`` when this file is in ``scripts/eval/``.
CASES_GLOB = "eval/*.md"

DEFAULT_AGENT = "root"
DEFAULT_EVAL_MODEL = ""

_EVALUATORS: dict[str, Any] = {}

_LOADER = MarkdownCaseLoader(_HERE, CASES_GLOB, _EVALUATORS)


def get_test_cases() -> list[dict[str, Any]]:
    return _LOADER.get_test_cases()
