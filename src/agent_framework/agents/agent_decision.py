"""Normalized model decision."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_framework.model import ModelResponse


def _optional_text(value: object) -> str | None:
    """Return a stripped string value or `None` if empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True, slots=True)
class AgentDecision:
    """Normalized decision emitted by the model for one loop iteration."""

    kind: str
    message: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    subagent_id: str | None = None
    tool_name: str | None = None
    callback_intent: str | None = None

    @classmethod
    def from_model_response(cls, response: ModelResponse) -> "AgentDecision":
        """Create an `AgentDecision` from a normalized model response."""
        payload = response.payload
        if "kind" not in payload:
            return cls(kind="final_message", message=response.raw_text)
        raw_kind = str(payload.get("kind", "")).strip()
        callback_intent = _optional_text(payload.get("intent"))
        normalized_kind = raw_kind
        callback_kinds = {
            "information_request",
            "proposal_review",
            "execution_recovery",
            "delegation_return",
            "policy_or_approval",
            "guardrail_trip",
        }
        if raw_kind in {"request_parameter", "request_user_input", "callback_to_caller"}:
            normalized_kind = "callback"
            callback_intent = "information_request"
        elif raw_kind in callback_kinds:
            normalized_kind = "callback"
            callback_intent = raw_kind
        return cls(
            kind=normalized_kind,
            message=str(payload.get("message", "")).strip(),
            parameters=dict(payload.get("parameters", {}) or {}),
            subagent_id=_optional_text(payload.get("subagent_id")),
            tool_name=_optional_text(payload.get("tool_name")),
            callback_intent=callback_intent,
        )

__all__ = ["AgentDecision"]
