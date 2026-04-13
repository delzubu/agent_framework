from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SessionContext:
    session_id: str
    agent_id: str
    env_path: Path
    setup_path: Path | None = None
    state: dict[str, Any] = field(default_factory=dict)
