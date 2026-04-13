from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RunnerHost:
    host: Any
    session_context: object
