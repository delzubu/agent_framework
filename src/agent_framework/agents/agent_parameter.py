"""Declared agent parameter contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentParameter:
    """Declared invocation parameter for an agent."""

    name: str
    description: str
    required: bool = True
    value_type: str = "string"
    default: Any = None
    schema_path: Path | None = None

__all__ = ["AgentParameter"]
