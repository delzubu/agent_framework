"""Model driver abstractions and provider-backed implementations.

The runtime depends on the `ModelDriver` protocol instead of any provider SDK
types so agents can remain SDK-agnostic and tests can inject deterministic
fakes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Final, Protocol

from agent_framework.errors import ModelDriverError
from agent_framework.tool import ToolDefinition

_LOGGER = logging.getLogger(__name__)

# Default structured-output contract for agents and headless ``complete()`` calls.
DEFAULT_RESPONSE_MODE: Final[str] = "json_object"


def parse_json_object_model_output(
    raw_text: str,
    *,
    provider_label: str,
) -> tuple[dict[str, Any], str]:
    """Parse assistant text as one JSON object for structured / ``json_object`` modes.

    Used by **all** model drivers: applies fence stripping (via
    ``agent_framework.validation._normalize_json_text``), :func:`json.loads`, and
    requires a JSON **object** at the top level. Invalid text or non-objects
    raise :class:`~agent_framework.errors.ModelDriverError` with a short preview
    and an ``upstream_body`` excerpt for tracing.

    ``response_mode == \"text\"`` bypasses this at the call site and does not
    invoke this function.
    """
    from agent_framework.validation import _normalize_json_text as _norm

    preview = (
        (raw_text[:400] + ("…" if len(raw_text) > 400 else "")) if raw_text else ""
    )
    excerpt = raw_text[:2000] if len(raw_text) > 2000 else raw_text
    try:
        normalized = _norm(raw_text)
        value: Any = json.loads(normalized)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ModelDriverError(
            f"{provider_label} structured response is not valid JSON: {exc}. Preview: {preview!r}",
            status_code=None,
            upstream_body=excerpt,
        ) from exc
    if not isinstance(value, dict):
        raise ModelDriverError(
            f"{provider_label} structured response must be a JSON object at the top level.",
            status_code=None,
            upstream_body=excerpt,
        )
    return value, normalized

# ---------------------------------------------------------------------------
# Shared model fallback base
# ---------------------------------------------------------------------------


class _FallbackMixin:
    """Shared model fallback state and retry logic for LLM driver dataclasses.

    Subclasses must declare a dataclass field named ``_fallback_state``::

        _fallback_state: dict[tuple[str, ...], int] = field(default_factory=dict, repr=False)

    The fallback state maps each model-list tuple to the index of the last
    successfully used model.  Subsequent calls start from that index, skipping
    known-bad models.  Call ``reset_model_fallback()`` to restart from the
    beginning of the list.

    For JSON / structured output defaults shared across drivers, use
    :meth:`resolved_response_format_dict` (delegates to the module-level
    function of the same name, defined after :class:`ModelContext`).
    """

    __slots__ = ()

    @staticmethod
    def resolved_response_format_dict(context: "ModelContext") -> dict[str, Any] | None:
        """Effective chat-completions-style ``response_format`` dict for *context*.

        Same as the module-level :func:`resolved_response_format_dict`.
        """
        return resolved_response_format_dict(context)

    def reset_model_fallback(self) -> None:
        """Reset fallback memory so the next call starts from the first model."""
        self._fallback_state.clear()  # type: ignore[attr-defined]

    def _fallback_decide(
        self,
        model_names: tuple[str, ...],
        try_fn: Callable[[str], "ModelResponse"],
    ) -> "ModelResponse":
        """Try each model in ``model_names`` starting from the last known-good index.

        On success the successful index is persisted so future calls skip
        earlier failing models.  Each failure is logged at INFO level with the
        full error message.
        """
        state: dict[tuple[str, ...], int] = self._fallback_state  # type: ignore[attr-defined]
        start = state.get(model_names, 0)
        last_exc: Exception | None = None
        for i in range(len(model_names)):
            idx = (start + i) % len(model_names)
            model = model_names[idx]
            try:
                result = try_fn(model)
                state[model_names] = idx
                return result
            except Exception as exc:
                _LOGGER.info("Model %r not available: %s", model, exc)
                last_exc = exc
        raise last_exc  # type: ignore[misc]

    async def _fallback_decide_async(
        self,
        model_names: tuple[str, ...],
        try_fn: Callable[[str], Awaitable["ModelResponse"]],
    ) -> "ModelResponse":
        """Async counterpart of ``_fallback_decide``."""
        state: dict[tuple[str, ...], int] = self._fallback_state  # type: ignore[attr-defined]
        start = state.get(model_names, 0)
        last_exc: Exception | None = None
        for i in range(len(model_names)):
            idx = (start + i) % len(model_names)
            model = model_names[idx]
            try:
                result = await try_fn(model)
                state[model_names] = idx
                return result
            except Exception as exc:
                _LOGGER.info("Model %r not available: %s", model, exc)
                last_exc = exc
        raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Driver capability contract (G-15)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DriverCapabilities:
    """Declared capabilities of a model driver.

    Drivers expose a ``capabilities`` class attribute so callers can inspect
    what a driver supports before constructing a ``ModelContext`` or invoking
    ``decide``.  Use ``get_driver_capabilities()`` to query any driver safely.

    Attributes:
        is_async: True if the driver's ``decide`` method is a coroutine.
        supports_multimodal: True if the driver accepts image ``ContentPart``
            objects in ``ModelContext.messages``.
        supports_response_format: True if the driver forwards
            ``ModelContext.response_format`` to the provider.
        supports_tools: True if the driver forwards native tool definitions to
            the provider rather than embedding them in the system prompt.
        supports_streaming: True if the driver supports streaming responses.
    """

    is_async: bool = False
    supports_multimodal: bool = False
    supports_response_format: bool = False
    supports_tools: bool = False
    supports_streaming: bool = False


def get_driver_capabilities(driver: Any) -> DriverCapabilities:
    """Return the declared capabilities of a driver.

    Falls back to conservative defaults for legacy drivers that pre-date the
    capability contract.
    """
    caps = getattr(driver, "capabilities", None)
    if caps is None:
        return DriverCapabilities()
    return caps() if callable(caps) else caps

_SYSTEM_TEMPLATE_PATH = Path(__file__).with_name("agents") / "system.md"
_SYSTEM_TEMPLATE = _SYSTEM_TEMPLATE_PATH.read_text(encoding="utf-8")
_SYSTEM_DECISION_TEMPLATE_PATH = Path(__file__).with_name("agents") / "system.decision.md"
_SYSTEM_DECISION_TEMPLATE = _SYSTEM_DECISION_TEMPLATE_PATH.read_text(encoding="utf-8")
_SYSTEM_TEXT_TEMPLATE_PATH = Path(__file__).with_name("agents") / "system.text.md"
_SYSTEM_TEXT_TEMPLATE = _SYSTEM_TEXT_TEMPLATE_PATH.read_text(encoding="utf-8")
_SYSTEM_JSON_OBJECT_TEMPLATE_PATH = Path(__file__).with_name("agents") / "system.json_object.md"
_SYSTEM_JSON_OBJECT_TEMPLATE = _SYSTEM_JSON_OBJECT_TEMPLATE_PATH.read_text(encoding="utf-8")
_SYSTEM_PLAN_EXECUTE_TEMPLATE_PATH = Path(__file__).with_name("agents") / "system.plan_execute.md"
_SYSTEM_PLAN_EXECUTE_TEMPLATE = _SYSTEM_PLAN_EXECUTE_TEMPLATE_PATH.read_text(encoding="utf-8")


def build_skills_catalog(skills: "tuple[CapabilityDefinition, ...]", max_tokens: int = 2000) -> str:
    """Return a formatted skills catalog string, or empty string if no skills.

    Skills are sorted by priority descending (highest first). When the catalog
    exceeds ``max_tokens`` (estimated as ``len(text) // 4``), the lowest-priority
    skill is dropped and the catalog is rebuilt. At least one skill is always
    kept.
    """
    if not skills:
        return ""

    def _render(skill_list: list["CapabilityDefinition"]) -> str:
        skills_list = json.dumps(
            [{"name": s.capability_id, "description": s.description} for s in skill_list],
            indent=2,
        )
        return (
            "## Skills\n\n"
            "<available_skills>\n"
            f"{skills_list}\n"
            "</available_skills>\n\n"
            "1. Review available skills and their descriptions to decide if a skill applies to the task.\n"
            "2. To invoke a skill, set `kind` to `invoke_skill` and `skill_name` to a valid skill name.\n"
            "3. After a skill is invoked, its full instructions will be injected into this conversation.\n"
            "   Follow those instructions to complete the task.\n"
            "4. Skill files are accessible via the base directory path provided with each skill invocation."
        )

    # Sort by priority descending so lowest-priority is at the end
    working = sorted(skills, key=lambda s: s.priority, reverse=True)
    while working:
        text = _render(working)
        if len(text) // 4 <= max_tokens or len(working) == 1:
            return text
        working.pop()  # drop the lowest-priority skill (last in sorted list)

    return ""  # unreachable: loop always returns before working is exhausted


def runtime_prompt_source_paths(response_mode: str) -> tuple[Path, ...]:
    """Return the system prompt source files used for the given response mode."""
    mode_map = {
        "decision": _SYSTEM_DECISION_TEMPLATE_PATH,
        "text": _SYSTEM_TEXT_TEMPLATE_PATH,
        "plan_execute": _SYSTEM_PLAN_EXECUTE_TEMPLATE_PATH,
        DEFAULT_RESPONSE_MODE: _SYSTEM_JSON_OBJECT_TEMPLATE_PATH,
    }
    return (_SYSTEM_TEMPLATE_PATH, mode_map.get(response_mode, _SYSTEM_JSON_OBJECT_TEMPLATE_PATH))


class ModelDriverBase:
    """Shared agent-agnostic runtime prompt assembly for concrete model drivers.

    Holds capability metadata, mode templates, and merge helpers so derived
    drivers only implement transport.  Not abstract — subclass or use mixins.

    **Conversation store:** loading and persisting history for
    :meth:`agent_framework.host.AgentHost.complete` remains on the host
    (``conversation_id`` + store).  Optional driver-base hooks for other
    persistence shapes are a future extension (see architecture ADR).

    **Native tool callbacks:** when a provider executes tools inside its SDK,
    the runtime should delegate to the agent loop via an injectable bridge
    (planned extension; see ADR) rather than synthetic chat messages.
    """

    @staticmethod
    def _capability_metadata(
        tools: tuple[ToolDefinition, ...],
        subagents: tuple[CapabilityDefinition, ...],
        skills: tuple[CapabilityDefinition, ...],
    ) -> dict[str, str]:
        """Build shared capability metadata payloads for prompt injection."""
        tools_json = json.dumps(
            [
                {
                    "id": tool.tool_id,
                    "description": tool.description,
                    "parameters": [
                        {
                            "name": parameter.name,
                            "type": parameter.value_type,
                            "required": parameter.required,
                            "description": parameter.description,
                        }
                        for parameter in tool.parameters
                    ],
                }
                for tool in tools
            ],
            indent=2,
        )
        subagents_json = json.dumps(
            [
                {
                    "id": item.capability_id,
                    "description": item.description,
                    "parameters": [
                        {
                            "name": parameter.name,
                            "type": parameter.value_type,
                            "required": parameter.required,
                            "description": parameter.description,
                        }
                        for parameter in item.parameters
                    ],
                }
                for item in subagents
            ],
            indent=2,
        )
        return {
            "tools_json": tools_json,
            "subagents_json": subagents_json,
        }

    @classmethod
    def _runtime_prompt(cls, context: ModelContext) -> str:
        """Build the shared and mode-specific runtime prompt block."""
        metadata = cls._capability_metadata(context.tools, context.subagents, context.skills)
        shared_prompt = _SYSTEM_TEMPLATE.format(**metadata).strip()
        mode_templates = {
            "decision": _SYSTEM_DECISION_TEMPLATE,
            "text": _SYSTEM_TEXT_TEMPLATE,
            "plan_execute": _SYSTEM_PLAN_EXECUTE_TEMPLATE,
            DEFAULT_RESPONSE_MODE: _SYSTEM_JSON_OBJECT_TEMPLATE,
        }
        mode_prompt = mode_templates.get(context.response_mode, _SYSTEM_JSON_OBJECT_TEMPLATE).strip()
        return f"{shared_prompt}\n\n{mode_prompt}".strip()

    @classmethod
    def decision_instructions(
        cls,
        tools: tuple[ToolDefinition, ...],
        subagents: tuple[CapabilityDefinition, ...],
        skills: tuple[CapabilityDefinition, ...],
    ) -> str:
        """Return the generic decision envelope instructions as text."""
        return cls._runtime_prompt(
            ModelContext(
                system_prompt="",
                user_prompt="",
                tools=tools,
                subagents=subagents,
                skills=skills,
                run_id=None,
            )
        )

    @classmethod
    def _capability_prompt(cls, context: ModelContext) -> str:
        """Return the provider-side injected capability and mode guidance."""
        return cls._runtime_prompt(context)

    @classmethod
    def shared_instructions(
        cls,
        tools: tuple[ToolDefinition, ...],
        subagents: tuple[CapabilityDefinition, ...],
        skills: tuple[CapabilityDefinition, ...],
    ) -> str:
        """Return the shared runtime capability block without a mode suffix."""
        metadata = cls._capability_metadata(tools, subagents, skills)
        return _SYSTEM_TEMPLATE.format(**metadata)


def assemble_system_prompt(context: "ModelContext") -> str:
    """Return the full system prompt assembled for a provider call."""
    capability_message = ModelDriverBase._capability_prompt(context)
    combined_system_prompt = context.system_prompt.strip()
    if capability_message:
        combined_system_prompt = f"{combined_system_prompt}\n\n{capability_message.strip()}".strip()
    return combined_system_prompt


def merge_runtime_system_into_messages(context: "ModelContext") -> "ModelContext":
    """Merge runtime templates into the first system message and align ``system_prompt``.

    Call from :meth:`Agent.build_context` and :meth:`AgentHost.complete` so every
    driver receives identical ``ModelContext.messages`` (unless
    ``exact_input_payload`` bypasses normal assembly in :meth:`decide`).
    """
    combined = assemble_system_prompt(context)
    if context.messages:
        model_input = [dict(m) for m in context.messages]
        if model_input and model_input[0].get("role") == "system":
            model_input[0] = {"role": "system", "content": combined}
        else:
            model_input.insert(0, {"role": "system", "content": combined})
        return replace(
            context,
            system_prompt=combined,
            messages=tuple(model_input),
        )
    return replace(
        context,
        system_prompt=combined,
        messages=(
            {"role": "system", "content": combined},
            {"role": "user", "content": context.user_prompt},
        ),
    )


@dataclass(frozen=True, slots=True)
class CapabilityParameter:
    """Structured parameter description for subagents or skills."""

    name: str
    description: str
    required: bool = True
    value_type: str = "string"

    def to_model_payload(self) -> dict[str, object]:
        """Convert the parameter description to a serializable payload."""
        return {
            "name": self.name,
            "description": self.description,
            "required": self.required,
            "type": self.value_type,
        }


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    """Structured capability description injected by the runtime."""

    capability_id: str
    description: str
    parameters: tuple[CapabilityParameter, ...] = ()
    priority: int = 0

    def to_model_payload(self) -> dict[str, object]:
        """Return a serializable payload for model-facing capability injection."""
        return {
            "id": self.capability_id,
            "description": self.description,
            "parameters": [item.to_model_payload() for item in self.parameters],
        }


@dataclass(frozen=True, slots=True)
class ModelContext:
    """Model-facing prompt payload assembled for a single decision step.

    Attributes:
        system_prompt: After :func:`merge_runtime_system_into_messages`, the full
            assembled system text (agent block plus runtime templates). Before
            merge, agent flows use the agent definition only; headless
            ``complete()`` may use ``""`` until merge runs.
        user_prompt: Rendered invocation prompt plus dynamic augmentations.
        messages: Structured conversation history for providers that support
            message-array inputs.
        response_mode: Runtime-level response contract for this model call
            (default: :data:`DEFAULT_RESPONSE_MODE`).
        exact_input_payload: Exact provider-native input payload. When present,
            the adapter must forward it unchanged instead of composing prompt
            messages from the other context fields.
        tools: Tools available to the model for this decision step.
        subagents: Subagents available to the model for this decision step.
        skills: Skills or other future capabilities available to the model.
    """

    system_prompt: str
    user_prompt: str
    messages: tuple[dict[str, Any], ...] = ()
    response_mode: str = DEFAULT_RESPONSE_MODE
    exact_input_payload: Any | None = None
    tools: tuple[ToolDefinition, ...] = ()
    subagents: tuple[CapabilityDefinition, ...] = ()
    skills: tuple[CapabilityDefinition, ...] = ()
    run_id: str | None = None
    response_format: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """Normalized cross-provider token accounting for one model response."""

    input_tokens: int = 0
    input_cached_tokens: int = 0
    output_tokens: int = 0
    output_cached_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-serializable mapping."""
        return {
            "input_tokens": self.input_tokens,
            "input_cached_tokens": self.input_cached_tokens,
            "output_tokens": self.output_tokens,
            "output_cached_tokens": self.output_cached_tokens,
            "total_tokens": self.total_tokens,
        }


def _coerce_usage_int(value: Any) -> int:
    """Best-effort integer coercion for provider token fields."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_openai_usage(raw: Any) -> LlmUsage | None:
    """Normalize OpenAI Responses API usage into the shared contract."""
    if raw is None:
        return None
    if isinstance(raw, LlmUsage):
        return raw
    if isinstance(raw, dict):
        payload = dict(raw)
    elif hasattr(raw, "model_dump"):
        dumped = raw.model_dump()
        if not isinstance(dumped, dict):
            return None
        payload = dumped
    elif hasattr(raw, "to_dict"):
        dumped = raw.to_dict()
        if not isinstance(dumped, dict):
            return None
        payload = dumped
    else:
        payload = {}
        for name in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "input_tokens_details",
            "output_tokens_details",
        ):
            value = getattr(raw, name, None)
            if value is not None:
                payload[name] = value
    if not payload:
        return None

    input_details = payload.get("input_tokens_details")
    input_cached_tokens = (
        _coerce_usage_int(input_details.get("cached_tokens"))
        if isinstance(input_details, dict)
        else _coerce_usage_int(getattr(input_details, "cached_tokens", 0))
    )
    input_tokens = _coerce_usage_int(payload.get("input_tokens"))
    output_tokens = _coerce_usage_int(payload.get("output_tokens"))
    total_tokens = _coerce_usage_int(payload.get("total_tokens")) or (input_tokens + output_tokens)
    return LlmUsage(
        input_tokens=input_tokens,
        input_cached_tokens=input_cached_tokens,
        output_tokens=output_tokens,
        output_cached_tokens=0,
        total_tokens=total_tokens,
    )


def normalize_chat_completions_usage(raw: Any) -> LlmUsage | None:
    """Normalize chat-completions usage into the shared contract."""
    if raw is None:
        return None
    if isinstance(raw, LlmUsage):
        return raw
    if not isinstance(raw, dict):
        if hasattr(raw, "model_dump"):
            dumped = raw.model_dump()
            if not isinstance(dumped, dict):
                return None
            raw = dumped
        elif hasattr(raw, "to_dict"):
            dumped = raw.to_dict()
            if not isinstance(dumped, dict):
                return None
            raw = dumped
        else:
            raw = {
                "prompt_tokens": getattr(raw, "prompt_tokens", None),
                "completion_tokens": getattr(raw, "completion_tokens", None),
                "total_tokens": getattr(raw, "total_tokens", None),
                "prompt_tokens_details": getattr(raw, "prompt_tokens_details", None),
                "completion_tokens_details": getattr(raw, "completion_tokens_details", None),
            }
    payload = dict(raw)
    if not payload:
        return None
    input_details = payload.get("prompt_tokens_details")
    input_cached_tokens = (
        _coerce_usage_int(input_details.get("cached_tokens"))
        if isinstance(input_details, dict)
        else _coerce_usage_int(getattr(input_details, "cached_tokens", 0))
    )
    input_tokens = _coerce_usage_int(payload.get("prompt_tokens"))
    output_tokens = _coerce_usage_int(payload.get("completion_tokens"))
    total_tokens = _coerce_usage_int(payload.get("total_tokens")) or (input_tokens + output_tokens)
    return LlmUsage(
        input_tokens=input_tokens,
        input_cached_tokens=input_cached_tokens,
        output_tokens=output_tokens,
        output_cached_tokens=0,
        total_tokens=total_tokens,
    )


def resolved_response_format_dict(context: ModelContext) -> dict[str, Any] | None:
    """Return the effective chat-completions-style ``response_format`` dict.

    Drivers should use this (or :meth:`_FallbackMixin.resolved_response_format_dict`)
    to populate provider-native JSON mode when the caller omitted
    ``context.response_format``.

    * ``response_mode == \"text\"`` → ``None`` (plain text at the API).
    * Explicit ``context.response_format`` → shallow copy (non-text modes only;
      text mode still forces ``None``).
    * ``response_mode == DEFAULT_RESPONSE_MODE`` with no explicit format →
      ``{\"type\": \"json_object\"}``.
    * Other modes (e.g. ``\"decision\"``) → ``None`` unless the caller set
      ``response_format``.
    """
    if context.response_mode == "text":
        return None
    if context.response_format is not None:
        return dict(context.response_format)
    if context.response_mode == DEFAULT_RESPONSE_MODE:
        return {"type": "json_object"}
    return None


def openai_responses_text_format_field(fmt: dict[str, Any]) -> dict[str, Any]:
    """Map a chat-completions-style ``response_format`` dict to OpenAI Responses ``text.format``.

    Accepts ``{\"type\": \"json_object\"}`` or nested ``json_schema`` payloads
    (same shape as :func:`resolved_response_format_dict` / DIAL). Unknown shapes
    are shallow-copied.
    """
    t = fmt.get("type")
    if t == "json_object":
        return {"type": "json_object"}
    if t == "json_schema":
        inner = fmt.get("json_schema")
        if not isinstance(inner, dict):
            inner = {}
        out: dict[str, Any] = {
            "type": "json_schema",
            "name": str(inner.get("name", "response")),
            "schema": dict(inner.get("schema", {})),
        }
        desc = inner.get("description")
        if desc is not None:
            out["description"] = desc
        if "strict" in inner:
            out["strict"] = inner["strict"]
        return out
    return dict(fmt)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalized model response returned by a `ModelDriver`.

    Attributes:
        payload: Parsed structured payload consumed by the agent runtime.
        raw_text: Original model text before runtime normalization.
        tool_calls: Tool calls requested by the model (chat completions
            drivers), or None if not applicable.
        finish_reason: Stop reason reported by the provider (e.g. ``"stop"``,
            ``"tool_calls"``, ``"length"``).
        usage: Normalized token usage keyed by ``input_tokens``,
            ``input_cached_tokens``, ``output_tokens``,
            ``output_cached_tokens``, and ``total_tokens``.
        raw_usage: Provider-native usage payload preserved for audit/tracing.
    """

    payload: dict[str, object]
    raw_text: str
    tool_calls: tuple[dict[str, Any], ...] | None = None
    finish_reason: str | None = None
    usage: LlmUsage | None = None
    raw_usage: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProviderRequestTrace:
    """Exact provider request payload sent by a model adapter."""

    agent_id: str | None
    provider_name: str
    model_name: str
    input_payload: Any
    temperature: float
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponseTrace:
    """Exact provider response payload observed by a model adapter."""

    agent_id: str | None
    provider_name: str
    model_name: str
    raw_text: str
    parsed_payload: dict[str, object] | None = None
    usage: LlmUsage | None = None
    raw_usage: dict[str, Any] | None = None
    run_id: str | None = None


class ModelDriver(Protocol):
    """Provider-agnostic protocol for a single agent decision step."""

    def decide(
        self,
        *,
        agent_id: str | None,
        provider_name: str,
        model_names: tuple[str, ...],
        temperature: float,
        context: ModelContext,
    ) -> ModelResponse:
        """Return a normalized structured response."""

    def set_trace_callbacks(
        self,
        *,
        on_request: Any | None = None,
        on_response: Any | None = None,
    ) -> None:
        """Attach optional adapter-boundary trace callbacks."""


class AsyncModelDriver(Protocol):
    """Provider-agnostic protocol for an async single agent decision step.

    Implement this protocol for drivers that run on an ``asyncio`` event loop
    (e.g. DIAL, Anthropic).  The sync ``ModelDriver`` protocol continues to
    work unchanged; use ``SyncToAsyncAdapter`` or ``AsyncToSyncAdapter`` to
    bridge between the two when needed.
    """

    async def decide(
        self,
        *,
        agent_id: str | None,
        provider_name: str,
        model_names: tuple[str, ...],
        temperature: float,
        context: ModelContext,
    ) -> ModelResponse:
        """Return a normalized structured response (coroutine)."""

    def set_trace_callbacks(
        self,
        *,
        on_request: Any | None = None,
        on_response: Any | None = None,
    ) -> None:
        """Attach optional adapter-boundary trace callbacks."""


@dataclass(slots=True)
class SyncToAsyncAdapter:
    """Wrap a synchronous ``ModelDriver`` for async callers.

    Runs the blocking ``decide()`` call in a thread pool via
    ``asyncio.to_thread`` so it does not block the event loop.
    """

    _driver: ModelDriver

    async def decide(
        self,
        *,
        agent_id: str | None,
        provider_name: str,
        model_names: tuple[str, ...],
        temperature: float,
        context: ModelContext,
    ) -> ModelResponse:
        return await asyncio.to_thread(
            self._driver.decide,
            agent_id=agent_id,
            provider_name=provider_name,
            model_names=model_names,
            temperature=temperature,
            context=context,
        )

    def set_trace_callbacks(
        self,
        *,
        on_request: Any | None = None,
        on_response: Any | None = None,
    ) -> None:
        self._driver.set_trace_callbacks(on_request=on_request, on_response=on_response)


@dataclass(slots=True)
class AsyncToSyncAdapter:
    """Wrap an ``AsyncModelDriver`` for synchronous callers.

    Used by the existing sync agent loop when a caller configures an async
    driver (e.g. ``DialChatCompletionsDriver``) and then runs a markdown-
    defined agent via ``AgentHost.run_agent()``.  Uses ``asyncio.run()`` if no
    event loop is running, or ``asyncio.get_event_loop().run_until_complete()``
    as a fallback.
    """

    _driver: Any  # AsyncModelDriver — typed as Any to avoid circular issues

    def decide(
        self,
        *,
        agent_id: str | None,
        provider_name: str,
        model_names: tuple[str, ...],
        temperature: float,
        context: ModelContext,
    ) -> ModelResponse:
        coro = self._driver.decide(
            agent_id=agent_id,
            provider_name=provider_name,
            model_names=model_names,
            temperature=temperature,
            context=context,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # Running inside an existing event loop — use a new thread to avoid
        # "cannot run nested event loop" errors.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    def set_trace_callbacks(
        self,
        *,
        on_request: Any | None = None,
        on_response: Any | None = None,
    ) -> None:
        self._driver.set_trace_callbacks(on_request=on_request, on_response=on_response)


__all__ = [
    "AsyncModelDriver",
    "AsyncToSyncAdapter",
    "CapabilityDefinition",
    "CapabilityParameter",
    "DEFAULT_RESPONSE_MODE",
    "DriverCapabilities",
    "LlmUsage",
    "ModelContext",
    "ModelDriverBase",
    "merge_runtime_system_into_messages",
    "normalize_chat_completions_usage",
    "normalize_openai_usage",
    "openai_responses_text_format_field",
    "ModelDriver",
    "ModelResponse",
    "parse_json_object_model_output",
    "resolved_response_format_dict",
    "ProviderRequestTrace",
    "ProviderResponseTrace",
    "SyncToAsyncAdapter",
    "ToolDefinition",
    "_FallbackMixin",
    "assemble_system_prompt",
    "build_skills_catalog",
    "get_driver_capabilities",
    "runtime_prompt_source_paths",
]


def _normalize_json_text(raw_text: str) -> str:
    """Extract JSON text from plain or fenced model responses.

    Delegates to ``agent_framework.validation._normalize_json_text`` which is
    the canonical implementation.
    """
    from agent_framework.validation import _normalize_json_text as _impl

    return _impl(raw_text)
