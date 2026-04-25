"""Host-managed interactive prompt state.

This module models user-facing interaction requests as first-class runtime
objects. The host uses these records to associate one pending prompt with the
exact run and agent that raised it, which is especially important for web
transports that route responses by prompt identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class PendingInteraction:
    """A single pending request for user input.

    The host creates one record per blocking user prompt and removes it when a
    response is received. `prompt_id` is the stable routing key used by web
    transports and trace events.
    """

    prompt_id: str
    session_id: str | None
    prompt: str
    intent: str
    run_id: str
    agent_id: str
    caller_id: str | None
    parent_run_id: str | None
    interaction_kind: str
    blocking: bool
    created_at: datetime

    def metadata(self) -> dict[str, object]:
        """Return transport-safe interaction metadata."""
        return {
            "prompt_id": self.prompt_id,
            "session_id": self.session_id,
            "prompt": self.prompt,
            "intent": self.intent,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "caller_id": self.caller_id,
            "parent_run_id": self.parent_run_id,
            "interaction_kind": self.interaction_kind,
            "blocking": self.blocking,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
        }


__all__ = ["PendingInteraction"]
