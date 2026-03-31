"""Call context model."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(slots=True)
class CallContext:
    """Represents one active or completed call edge in the runtime."""

    context_id: str
    caller_id: str
    callee_id: str
    kind: str
    status: str = "open"
    correlation_id: str = field(default_factory=lambda: str(uuid4()))

__all__ = ["CallContext"]
