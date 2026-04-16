"""Realistic initializer — support, formatting, and hallucination test cases.

These cases exercise common real-world agent quality concerns:
  01 — summarisation quality and tone control
  02 — structured output format compliance
  03 — hallucination resistance (answer only from context)

All cases use ``case_run_mode: no_callbacks`` so the agent never pauses
to ask clarifying questions during a batch run.

Run all cases:
    python -m agent_framework_evaluator evaluate --env .env --initializer path/to/realistic/init.py

Run only the first case:
    python -m agent_framework_evaluator evaluate --env .env --initializer path/to/realistic/init.py --case 0

Save full results JSON:
    python -m agent_framework_evaluator evaluate --env .env --initializer path/to/realistic/init.py --output results.json
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework_evaluator.case_markdown import MarkdownCaseLoader

_HERE = Path(__file__).resolve().parent

CASES_GLOB = "cases/*.md"
DEFAULT_AGENT = "root"
DEFAULT_EVAL_MODEL = "gpt-4o-mini"

_LOADER = MarkdownCaseLoader(_HERE, CASES_GLOB)


def get_test_cases() -> list[dict[str, Any]]:
    """Return all discovered test cases (cached; invalidates when files change)."""
    return _LOADER.get_test_cases()
