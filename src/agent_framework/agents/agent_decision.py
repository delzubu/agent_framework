"""Normalized model decision."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Final, TYPE_CHECKING

from agent_framework.model import ModelResponse

if TYPE_CHECKING:
    from agent_framework.planning.plan_state import PlanStep

_LOGGER = logging.getLogger(__name__)

# After alias normalization (callback intents, legacy names → callback family).
_ALLOWED_DECISION_KINDS: Final[frozenset[str]] = frozenset(
    {
        "final_message",
        "call_tool",
        "call_subagent",
        "call_subagents",
        "callback",
        "callback_to_caller",
        "request_user_input",
        "request_resolution",
        "invoke_skill",
    }
)

# Planning-only kinds — only accepted when planning_active=True is passed to
# from_model_response. Emitting these from a non-planning agent is a hard error.
_PLANNING_DECISION_KINDS: Final[frozenset[str]] = frozenset(
    {"submit_plan", "amend_plan", "continue_plan"}
)

_STEP_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
_VALID_STEP_KINDS: Final[frozenset[str]] = frozenset(
    {"call_tool", "call_subagent", "invoke_skill", "callback"}
)


@dataclass(frozen=True, slots=True)
class SubagentCallSpec:
    """One entry in a call_subagents batch decision."""

    subagent_id: str
    parameters: dict[str, Any] = field(default_factory=dict)
    output_key: str = ""  # filled to "call_<i>" by parser when absent


def _parse_and_validate_plan(
    payload: dict[str, Any],
    *,
    completed_step_ids: frozenset[str] | None = None,
) -> "tuple[PlanStep, ...]":
    """Parse and validate a `submit_plan` / `amend_plan` payload into PlanSteps.

    Raises `ValueError` for any of the validation rules in ADR §7.3.
    """
    from agent_framework.planning.plan_state import PlanStep  # avoid circular import

    raw_plan = payload.get("plan")
    if not isinstance(raw_plan, list) or len(raw_plan) == 0:
        raise ValueError(
            "Invalid submit_plan: 'plan' must be a non-empty list of step objects."
        )

    seen_ids: dict[str, int] = {}  # id → index for forward-ref and dup detection
    steps: list[PlanStep] = []

    for i, raw_step in enumerate(raw_plan):
        if not isinstance(raw_step, dict):
            raise ValueError(
                f"Invalid submit_plan: step at index {i} must be a JSON object."
            )

        step_id = str(raw_step.get("id", "")).strip()
        if not step_id:
            raise ValueError(
                f"Invalid submit_plan: step at index {i} is missing 'id'."
            )
        if not _STEP_ID_RE.match(step_id):
            raise ValueError(
                f"Invalid submit_plan: step id {step_id!r} at index {i} does not match "
                r"^[a-zA-Z][a-zA-Z0-9_]*$."
            )
        if step_id in seen_ids:
            raise ValueError(
                f"Invalid submit_plan: duplicate step id {step_id!r} at index {i} "
                f"(first occurrence at index {seen_ids[step_id]})."
            )

        step_kind = str(raw_step.get("kind", "")).strip()
        if step_kind not in _VALID_STEP_KINDS:
            raise ValueError(
                f"Invalid submit_plan: step {step_id!r} has unsupported kind "
                f"{step_kind!r}. Must be one of: {sorted(_VALID_STEP_KINDS)}."
            )

        tool_name = _optional_text(raw_step.get("tool_name"))
        subagent_id = _optional_text(raw_step.get("subagent_id"))
        skill_name = _optional_text(raw_step.get("skill_name"))
        callback_intent = _optional_text(raw_step.get("callback_intent"))

        if tool_name is not None and subagent_id is not None:
            raise ValueError(
                f"Invalid submit_plan: step {step_id!r} has both 'tool_name' and "
                "'subagent_id' set; use exactly one."
            )

        # Kind-target consistency
        if step_kind == "call_tool" and not tool_name:
            raise ValueError(
                f"Invalid submit_plan: step {step_id!r} has kind 'call_tool' but "
                "is missing 'tool_name'."
            )
        if step_kind == "call_subagent" and not subagent_id:
            raise ValueError(
                f"Invalid submit_plan: step {step_id!r} has kind 'call_subagent' but "
                "is missing 'subagent_id'."
            )
        if step_kind == "invoke_skill" and not skill_name:
            raise ValueError(
                f"Invalid submit_plan: step {step_id!r} has kind 'invoke_skill' but "
                "is missing 'skill_name'."
            )
        if step_kind == "callback" and not callback_intent:
            raise ValueError(
                f"Invalid submit_plan: step {step_id!r} has kind 'callback' but "
                "is missing 'callback_intent'."
            )

        # depends_on: no forward refs (only IDs already seen)
        raw_deps = raw_step.get("depends_on") or []
        if not isinstance(raw_deps, list):
            raise ValueError(
                f"Invalid submit_plan: step {step_id!r} 'depends_on' must be a list."
            )
        deps: list[str] = []
        for dep in raw_deps:
            dep_id = str(dep).strip()
            if dep_id not in seen_ids:
                if completed_step_ids and dep_id in completed_step_ids:
                    raise ValueError(
                        f"Invalid submit_plan: step {step_id!r} depends_on {dep_id!r} "
                        "which is already completed from a previous plan. "
                        f"Remove it from depends_on — its result is still accessible as "
                        f"{{{{{dep_id}}}}} in parameters."
                    )
                raise ValueError(
                    f"Invalid submit_plan: step {step_id!r} depends_on {dep_id!r} which "
                    "is not defined in this plan. Only reference step IDs that appear "
                    "earlier in the same plan array (no forward references)."
                )
            deps.append(dep_id)

        parameters = dict(raw_step.get("parameters") or {})
        message = str(raw_step.get("message", "")).strip()

        seen_ids[step_id] = i
        steps.append(PlanStep(
            id=step_id,
            kind=step_kind,
            parameters=parameters,
            tool_name=tool_name,
            subagent_id=subagent_id,
            skill_name=skill_name,
            callback_intent=callback_intent,
            depends_on=tuple(deps),
            message=message,
        ))

    return tuple(steps)


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
    response: dict[str, Any] | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    subagent_id: str | None = None
    tool_name: str | None = None
    callback_intent: str | None = None
    skill_name: str | None = None
    # call_subagents fields (populated only when kind == "call_subagents")
    batch_mode: str | None = None
    batch_timeout_seconds: float | None = None
    subagent_calls: tuple[SubagentCallSpec, ...] = field(default_factory=tuple)
    # planning fields (populated only when kind in _PLANNING_DECISION_KINDS)
    plan: "tuple[PlanStep, ...]" = field(default_factory=tuple)

    @classmethod
    def from_model_response(
        cls,
        response: ModelResponse,
        *,
        planning_active: bool = False,
        completed_step_ids: frozenset[str] | None = None,
    ) -> "AgentDecision":
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
        if raw_kind == "request_parameter":
            normalized_kind = "request_user_input"
            callback_intent = "information_request"
            _LOGGER.info(
                "Decision kind alias: mapped top-level kind %r to %r (intent=%r)",
                raw_kind,
                normalized_kind,
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

        if normalized_kind in _PLANNING_DECISION_KINDS:
            if not planning_active:
                raise ValueError(
                    f"Invalid model decision JSON: decision kind {normalized_kind!r} is only "
                    "valid for planning-enabled agents. Enable planning via 'planning: enabled: true' "
                    "in the agent frontmatter or pass planning_override=True at call time."
                )
        elif normalized_kind not in _ALLOWED_DECISION_KINDS:
            allowed = ", ".join(sorted(_ALLOWED_DECISION_KINDS | _PLANNING_DECISION_KINDS))
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

        # Parse planning-specific fields.
        plan: tuple[PlanStep, ...] = ()
        if normalized_kind in ("submit_plan", "amend_plan"):
            plan = _parse_and_validate_plan(payload, completed_step_ids=completed_step_ids)

        raw_response = payload.get("response")
        if isinstance(raw_response, dict):
            decision_response: dict[str, Any] | None = raw_response
        else:
            if normalized_kind == "final_message" and payload.get("parameters"):
                raise ValueError(
                    "Invalid model decision JSON: final_message with structured output must use "
                    "'response' (a JSON object). Setting 'parameters' on a final_message result "
                    "is no longer supported. Move the structured payload to 'response'."
                )
            decision_response = None

        return cls(
            kind=normalized_kind,
            message=str(payload.get("message", "")).strip(),
            response=decision_response,
            parameters=dict(payload.get("parameters", {}) or {}),
            subagent_id=subagent_id,
            tool_name=tool_name,
            callback_intent=callback_intent,
            skill_name=_optional_text(payload.get("skill_name")),
            batch_mode=batch_mode,
            batch_timeout_seconds=batch_timeout_seconds,
            subagent_calls=subagent_calls,
            plan=plan,
        )


__all__ = ["AgentDecision", "SubagentCallSpec", "_PLANNING_DECISION_KINDS"]
