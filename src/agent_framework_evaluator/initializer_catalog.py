"""Discover and resolve agent-eval initializer modules (setup + callbacks, prompt defaults)."""

from __future__ import annotations

import json
from pathlib import Path

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


def resolve_setup_path_for_run(env_file: Path, ref: str | None) -> Path | None:
    """Resolve UI/CLI initializer field to a ``setup_path`` for :class:`SessionRunner`.

    Order: path under ``AGENT_EVAL_INITIALIZER_DIR``, then absolute ``.py``, then
    ``.py`` relative to cwd.
    """
    if not ref or not str(ref).strip():
        return None
    s = str(ref).strip()
    inner = resolve_initializer_path(env_file, s)
    if inner is not None:
        return inner
    p = Path(s)
    if p.is_absolute() and p.suffix == ".py" and p.is_file():
        return p.resolve()
    cand = (Path.cwd() / s).resolve()
    if cand.suffix == ".py" and cand.is_file():
        return cand
    return None


def load_initializer_default_prompt(env_file: Path, initializer_ref: str) -> str:
    """Load initializer/setup module and return its default prompt text."""
    path = resolve_setup_path_for_run(env_file, initializer_ref)
    if path is None or not path.is_file():
        return ""
    module = load_setup_module(path)
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
