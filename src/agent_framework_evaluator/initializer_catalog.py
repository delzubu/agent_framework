"""Discover and resolve agent-eval initializer modules (setup + callbacks, prompt defaults)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_framework.config import read_optional_path_relative_to_env_file
from agent_framework_evaluator.runtime.setup_loader import load_setup_module


def resolve_env_path(env_path: str | Path) -> Path:
    """Resolve a user-supplied ``.env`` path the same way on server and client expectations.

    Relative paths are resolved against the **current working directory** (the process
    that runs Uvicorn). Use an absolute ``env_path`` in the UI if the server cwd is not
    your project root.
    """
    p = Path(env_path).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    else:
        p = p.resolve()
    return p


def evaluator_initializer_root(env_file: Path) -> Path | None:
    """Return ``AGENT_EVAL_INITIALIZER_DIR`` from ``env_file``, or ``None``."""
    return read_optional_path_relative_to_env_file(env_file, "AGENT_EVAL_INITIALIZER_DIR")


def list_initializer_scripts(env_file: Path) -> list[str]:
    """Relative paths (posix) of ``*.py`` files under the configured initializer directory."""
    root = evaluator_initializer_root(env_file)
    if root is None or not root.is_dir():
        return []
    root = root.resolve()
    out: list[str] = []
    for p in sorted(root.rglob("*.py")):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        out.append(rel.as_posix())
    return out


def resolve_initializer_path(env_file: Path, initializer_ref: str) -> Path | None:
    """Resolve ``initializer_ref`` to a readable ``.py`` under the initializer root."""
    root = evaluator_initializer_root(env_file)
    if root is None:
        return None
    root = root.resolve()
    if not root.is_dir():
        return None
    ref = initializer_ref.strip()
    if not ref:
        return None
    p = Path(ref.replace("\\", "/"))
    if p.is_absolute():
        candidate = p.resolve()
    else:
        candidate = (root / p).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.suffix != ".py" or not candidate.is_file():
        return None
    return candidate


def _unique_py_under_tree(root: Path, basename: str) -> Path | None:
    """If exactly one ``*.py`` file with ``basename`` exists under ``root``, return it."""
    if not basename.endswith(".py"):
        return None
    root = root.resolve()
    if not root.is_dir():
        return None
    matches = [p for p in root.rglob(basename) if p.is_file() and p.name == basename]
    if len(matches) == 1:
        return matches[0].resolve()
    return None


def resolve_setup_path_for_run(env_file: Path, ref: str | None) -> Path | None:
    """Resolve UI/CLI initializer field to a ``setup_path`` for :class:`SessionRunner`.

    Order: path under ``AGENT_EVAL_INITIALIZER_DIR``, then absolute ``.py``, then
    ``.py`` relative to cwd, then a **unique** basename match under the initializer
    tree (so ``deck-review.py`` finds ``…/scripts/eval/deck-review.py`` when unambiguous).
    """
    if not ref or not str(ref).strip():
        return None
    s = str(ref).strip()
    inner = resolve_initializer_path(env_file, s)
    if inner is not None:
        return inner
    p = Path(s).expanduser()
    if p.is_absolute() and p.suffix == ".py" and p.is_file():
        return p.resolve()
    cand = (Path.cwd() / s).resolve()
    if cand.suffix == ".py" and cand.is_file():
        return cand
    init_root = evaluator_initializer_root(env_file)
    if init_root is not None:
        init_root = init_root.resolve()
        norm = s.replace("\\", "/").strip()
        # Bare filename (no directory): e.g. deck-review.py → unique rglob under initializer dir
        if norm and "/" not in norm and not norm.startswith(".."):
            found = _unique_py_under_tree(init_root, Path(norm).name)
            if found is not None:
                return found
    return None


def load_initializer_default_prompt(env_file: Path, initializer_ref: str) -> str:
    """Load initializer/setup module and return its default prompt text."""
    path = resolve_setup_path_for_run(env_file, initializer_ref)
    if path is None or not path.is_file():
        return ""
    module = load_setup_module(path)
    if hasattr(module, "get_test_cases"):
        raw = module.get_test_cases()
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            p = raw[0].get("prompt")
            if isinstance(p, str) and p.strip():
                return p
    text = getattr(module, "PROMPT_TEMPLATE", None)
    if text is None and hasattr(module, "get_prompt_template"):
        gt = module.get_prompt_template()
        text = json.dumps(gt) if isinstance(gt, dict) else (gt or "")
    return text if isinstance(text, str) else ""


def load_initializer_default_evaluator_criteria(env_file: Path, initializer_ref: str) -> str:
    """Load initializer/setup module and return default evaluator criteria text, if any."""
    path = resolve_setup_path_for_run(env_file, initializer_ref)
    if path is None or not path.is_file():
        return ""
    module = load_setup_module(path)
    if hasattr(module, "get_test_cases"):
        raw = module.get_test_cases()
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            c0 = raw[0].get("evaluation_criteria", raw[0].get("criteria"))
            if isinstance(c0, str) and c0.strip():
                return c0
    text = getattr(module, "EVALUATOR_CRITERIA", None)
    if text is None and hasattr(module, "get_evaluator_criteria"):
        text = module.get_evaluator_criteria()
    return text if isinstance(text, str) else ""


def load_initializer_default_agent(env_file: Path, initializer_ref: str) -> str:
    """Load initializer/setup module and return default agent id, if any."""
    path = resolve_setup_path_for_run(env_file, initializer_ref)
    if path is None or not path.is_file():
        return ""
    module = load_setup_module(path)
    raw = getattr(module, "DEFAULT_AGENT", None)
    if raw is not None and isinstance(raw, str) and raw.strip():
        return raw.strip()
    if hasattr(module, "get_default_agent"):
        g = module.get_default_agent()
        if isinstance(g, str) and g.strip():
            return g.strip()
    return ""


def load_initializer_default_eval_model(env_file: Path, initializer_ref: str) -> str:
    """Return preferred evaluator model(s) from ``DEFAULT_EVAL_MODEL`` / ``get_default_eval_model()``."""
    path = resolve_setup_path_for_run(env_file, initializer_ref)
    if path is None or not path.is_file():
        return ""
    module = load_setup_module(path)
    raw = getattr(module, "DEFAULT_EVAL_MODEL", None)
    if raw is not None and isinstance(raw, str) and raw.strip():
        return raw.strip()
    if hasattr(module, "get_default_eval_model"):
        g = module.get_default_eval_model()
        if isinstance(g, str) and g.strip():
            return g.strip()
    return ""


def load_raw_test_cases(env_file: Path, initializer_ref: str) -> list[dict[str, Any]]:
    """Load test case dicts from initializer (includes ``code_evaluator`` callables when present)."""
    path = resolve_setup_path_for_run(env_file, initializer_ref)
    if path is None or not path.is_file():
        return []
    module = load_setup_module(path)
    # Test cases come only from get_test_cases() (e.g. markdown files via MarkdownCaseLoader).
    # There is no PROMPT_TEMPLATE fallback for the case list — use get_test_cases() or [].
    if not hasattr(module, "get_test_cases"):
        return []
    raw = module.get_test_cases()
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", f"Case {len(out)}"))
        prompt = str(item.get("prompt", ""))
        crit = item.get("evaluation_criteria", item.get("criteria", ""))
        criteria = str(crit) if crit is not None else ""
        ce = item.get("code_evaluator")
        out.append(
            {
                "title": title,
                "prompt": prompt,
                "evaluation_criteria": criteria,
                "code_evaluator": ce if callable(ce) else None,
            }
        )
    return out


def serialize_test_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """API-safe rows (no callables)."""
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(cases):
        ce = c.get("code_evaluator")
        rows.append(
            {
                "index": i,
                "title": str(c.get("title", f"Case {i}")),
                "prompt": str(c.get("prompt", "")),
                "criteria": str(c.get("evaluation_criteria", c.get("criteria", ""))),
                "has_code_evaluator": bool(callable(ce)),
            }
        )
    return rows


def load_test_cases(env_file: Path, initializer_ref: str) -> list[dict[str, Any]]:
    """Serializable test cases for ``GET /api/initializer-cases``."""
    return serialize_test_cases(load_raw_test_cases(env_file, initializer_ref))
