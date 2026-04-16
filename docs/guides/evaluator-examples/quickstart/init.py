"""Quickstart initializer — two simple cases next to this file.

Run all cases:
    python -m agent_framework_evaluator evaluate --env .env --initializer path/to/quickstart/init.py

Run a single case (0-based index):
    python -m agent_framework_evaluator evaluate --env .env --initializer path/to/quickstart/init.py --case 0
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework_evaluator.case_markdown import MarkdownCaseLoader

_HERE = Path(__file__).resolve().parent

# Discover all .md files inside the cases/ subdirectory.
CASES_GLOB = "cases/*.md"

# The agent id to invoke for these cases.  Change to match your agent.
DEFAULT_AGENT = "root"

# Optional: pin the evaluator model.  Leave empty to use DEFAULT_MODEL from .env.
DEFAULT_EVAL_MODEL = ""

_LOADER = MarkdownCaseLoader(_HERE, CASES_GLOB)


def get_test_cases() -> list[dict[str, Any]]:
    """Return all discovered test cases (cached; invalidates when files change)."""
    return _LOADER.get_test_cases()
