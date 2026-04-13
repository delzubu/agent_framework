from __future__ import annotations

import importlib.util
from pathlib import Path


def load_setup_module(path: Path):
    spec = importlib.util.spec_from_file_location("agent_eval_setup", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load setup module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
