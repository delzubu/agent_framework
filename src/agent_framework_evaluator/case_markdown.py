"""Load evaluator test cases from markdown files (``---``-separated frontmatter, prompt, criteria).

Use :class:`MarkdownCaseLoader` from an initializer's ``get_test_cases()`` so cases live in
``*.md`` files next to the initializer module, e.g. ``eval/deck-review-01.md`` with glob
``eval/*.md`` relative to ``deck-review.py``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent_framework.file_reference import DefaultFileReferenceResolver, FileReferenceResolver, expand_file_refs

_LOGGER = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"^---\s*$", re.MULTILINE)


def parse_simple_frontmatter(text: str) -> dict[str, str]:
    """Parse ``key: value`` lines (no nesting). For nested YAML use ``yaml.safe_load`` on the block."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        val = v.strip()
        if key:
            out[key] = val
    _LOGGER.debug(
        "Parsed evaluator case markdown frontmatter.",
        extra={
            "trace_kind": "evaluator.case_markdown.frontmatter_parsed",
            "trace_title": "Evaluator case frontmatter parsed",
            "trace_payload": {"frontmatter": dict(out)},
        },
    )
    return out


def parse_case_markdown_file(
    path: Path,
    evaluator_registry: Mapping[str, Callable[..., Any]],
    *,
    resolver: FileReferenceResolver | None = None,
) -> dict[str, Any] | None:
    """Parse one case file; return case metadata, prompt, criteria, and evaluator hooks."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _LOGGER.warning("Case file %s: cannot read (%s).", path, exc)
        return None
    parts = _SECTION_RE.split(raw)
    if len(parts) < 4:
        _LOGGER.warning(
            "Case file %s: expected three lines containing only --- (frontmatter, prompt block, criteria block). "
            "Got %d segment(s) after split; need at least 4. Common mistake: escaped \\--- from an editor export — "
            "use a plain --- line.",
            path,
            len(parts),
        )
        return None
    fm = parse_simple_frontmatter(parts[1].strip())
    title = fm.get("title", path.stem)
    eval_names_raw = fm.get("code_evaluator", "").strip()
    result_field = fm.get("result_field", "message").strip() or "message"
    flags: set[str] = {f.strip() for f in fm.get("flags", "").split(",") if f.strip()}
    prompt = parts[2].strip()
    criteria = parts[3].strip()
    _resolver = resolver if resolver is not None else DefaultFileReferenceResolver()
    prompt = expand_file_refs(prompt, _resolver, base_dir=path.parent)
    code_evaluators: list[Callable[..., Any]] = []
    for eval_name in [n.strip() for n in eval_names_raw.split(",") if n.strip()]:
        fn = evaluator_registry.get(eval_name)
        if fn is not None and callable(fn):
            code_evaluators.append(fn)
        else:
            _LOGGER.warning(
                "Case file %s: frontmatter code_evaluator=%r is not registered on this initializer module.",
                path,
                eval_name,
            )
    return {
        "title": title,
        "prompt": prompt,
        "evaluation_criteria": criteria,
        "code_evaluators": code_evaluators,
        "result_field": result_field,
        "flags": flags,
    }


class MarkdownCaseLoader:
    """Discover ``*.md`` cases under ``base_dir`` with a glob; cache invalidates on path/mtime changes."""

    def __init__(
        self,
        base_dir: Path,
        glob_pattern: str,
        evaluator_registry: Mapping[str, Callable[..., Any]] | None = None,
        *,
        resolver: FileReferenceResolver | None = None,
    ) -> None:
        self._base = base_dir.resolve()
        self._glob = glob_pattern
        self._reg: Mapping[str, Callable[..., Any]] = evaluator_registry or {}
        self._resolver = resolver
        self._cache: list[dict[str, Any]] | None = None
        self._cache_key: frozenset[tuple[str, float]] | None = None

    def get_test_cases(self) -> list[dict[str, Any]]:
        files = sorted(self._base.glob(self._glob))
        key = frozenset((str(p.resolve()), p.stat().st_mtime) for p in files)
        if self._cache is not None and key == self._cache_key:
            return self._cache
        parsed: list[dict[str, Any]] = []
        for f in files:
            row = parse_case_markdown_file(f, self._reg, resolver=self._resolver)
            if row is not None:
                parsed.append(row)
        self._cache = parsed
        self._cache_key = key
        return self._cache
