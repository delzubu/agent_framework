"""Normalized model decision."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Final

from agent_framework.model import ModelResponse

_LOGGER = logging.getLogger(__name__)

# After alias normalization (callback intents, legacy names → callback).
_ALLOWED_DECISION_KINDS: Final[frozenset[str]] = frozenset(
    {
        "final_message",
        "call_tool",
        "call_subagent",
        "call_subagents",
        "callback",
        "invoke_skill",
    }
)


@dataclass(frozen=True, slots=True)
class SubagentCallSpec:
    """One entry in a call_subagents batch decision."""

    subagent_id: str
    parameters: dict[str, Any] = field(default_factory=dict)
    output_key: str = ""  # filled to "call_<i>" by parser when absent


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
    skill_name: str | None = None
    # call_subagents fields (populated only when kind == "call_subagents")
    batch_mode: str | None = None
    batch_timeout_seconds: float | None = None
    subagent_calls: tuple[SubagentCallSpec, ...] = field(default_factory=tuple)

    @classmethod
    def from_model_response(cls, response: ModelResponse) -> "AgentDecision":
        """Create an `AgentDecision` from a normalized model response."""
        payload = response.payload
        if not isinstance(payload, dict):
            raise ValueError(
                "Invalid model decision JSON: payload must be a JSON object with a top-level \"kind\" field."
            )
        if "kind" not in payload:
            raise ValueError(
                'Invalid model decision JSON: missing top-level "kind" field '
                "(json_object contract requires a structured decision object)."
            )

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
            _LOGGER.info(
                "Decision kind alias: mapped top-level kind %r to callback (intent=%r)",
                raw_kind,
                callback_intent,
            )
        elif raw_kind in callback_kinds:
            normalized_kind = "callback"
            callback_intent = raw_kind
            _LOGGER.info(
                "Decision kind alias: mapped top-level kind %r to callback (intent=%r)",
                raw_kind,
                callback_intent,
            )

        if normalized_kind not in _ALLOWED_DECISION_KINDS:
            allowed = ", ".join(sorted(_ALLOWED_DECISION_KINDS))
            raise ValueError(
                f"Invalid model decision JSON: unsupported 'kind' {raw_kind!r}. "
                f"Must be one of: {allowed}. "
                "Callback-style intents may also be used as top-level kind "
                "(see agents/system.decision.md). Do not invent other kinds."
            )

        subagent_id = _optional_text(payload.get("subagent_id"))
        tool_name = _optional_text(payload.get("tool_name"))
        if subagent_id is not None and tool_name is not None:
            raise ValueError(
                "Invalid model decision JSON: both subagent_id and tool_name are set; "
                "use exactly one target per decision."
            )

        # Parse call_subagents-specific fields.
        batch_mode: str | None = None
        batch_timeout_seconds: float | None = None
        subagent_calls: tuple[SubagentCallSpec, ...] = ()

        if normalized_kind == "call_subagents":
            raw_mode = payload.get("mode")
            if raw_mode not in ("parallel", "sequential"):
                raise ValueError(
                    "Invalid model decision JSON: call_subagents requires 'mode' set to "
                    f"'parallel' or 'sequential'; got {raw_mode!r}."
                )
            batch_mode = str(raw_mode)

            raw_timeout = payload.get("timeout_seconds")
            if raw_timeout is not None:
                try:
                    t = float(raw_timeout)
                except (TypeError, ValueError):
                    t = -1.0
                if t <= 0:
                    raise ValueError(
                        "Invalid model decision JSON: call_subagents 'timeout_seconds' "
                        f"must be a positive number; got {raw_timeout!r}."
                    )
                batch_timeout_seconds = t

            raw_calls = payload.get("calls")
            if not isinstance(raw_calls, list) or len(raw_calls) == 0:
                raise ValueError(
                    "Invalid model decision JSON: call_subagents 'calls' must be a "
                    "non-empty list."
                )
            specs: list[SubagentCallSpec] = []
            for i, entry in enumerate(raw_calls):
                if not isinstance(entry, dict):
                    raise ValueError(
                        f"Invalid model decision JSON: call_subagents 'calls[{i}]' "
                        "must be a JSON object."
                    )
                entry_subagent_id = _optional_text(entry.get("subagent_id"))
                if not entry_subagent_id:
                    raise ValueError(
                        f"Invalid model decision JSON: call_subagents 'calls[{i}]' "
                        "is missing 'subagent_id'."
                    )
                entry_tool_name = _optional_text(entry.get("tool_name"))
                if entry_tool_name is not None:
                    raise ValueError(
                        f"Invalid model decision JSON: call_subagents 'calls[{i}]' "
                        "must not set 'tool_name'; use 'subagent_id' only."
                    )
                entry_output_key = _optional_text(entry.get("output_key")) or f"call_{i}"
                specs.append(SubagentCallSpec(
                    subagent_id=entry_subagent_id,
                    parameters=dict(entry.get("parameters", {}) or {}),
                    output_key=entry_output_key,
                ))
            subagent_calls = tuple(specs)

        return cls(
            kind=normalized_kind,
            message=str(payload.get("message", "")).strip(),
            parameters=dict(payload.get("parameters", {}) or {}),
            subagent_id=subagent_id,
            tool_name=tool_name,
            callback_intent=callback_intent,
            skill_name=_optional_text(payload.get("skill_name")),
            batch_mode=batch_mode,
            batch_timeout_seconds=batch_timeout_seconds,
            subagent_calls=subagent_calls,
        )


__all__ = ["AgentDecision", "SubagentCallSpec"]
