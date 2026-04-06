"""Model driver abstractions and provider-backed implementations.

The runtime depends on the `ModelDriver` protocol instead of any provider SDK
types so agents can remain SDK-agnostic and tests can inject deterministic
fakes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from openai import OpenAI

from agent_framework.tool import ToolDefinition

_SYSTEM_TEMPLATE_PATH = Path(__file__).with_name("agents") / "system.md"
_SYSTEM_TEMPLATE = _SYSTEM_TEMPLATE_PATH.read_text(encoding="utf-8")
_SYSTEM_DECISION_TEMPLATE_PATH = Path(__file__).with_name("agents") / "system.decision.md"
_SYSTEM_DECISION_TEMPLATE = _SYSTEM_DECISION_TEMPLATE_PATH.read_text(encoding="utf-8")
_SYSTEM_TEXT_TEMPLATE_PATH = Path(__file__).with_name("agents") / "system.text.md"
_SYSTEM_TEXT_TEMPLATE = _SYSTEM_TEXT_TEMPLATE_PATH.read_text(encoding="utf-8")
_SYSTEM_JSON_OBJECT_TEMPLATE_PATH = Path(__file__).with_name("agents") / "system.json_object.md"
_SYSTEM_JSON_OBJECT_TEMPLATE = _SYSTEM_JSON_OBJECT_TEMPLATE_PATH.read_text(encoding="utf-8")


def runtime_prompt_source_paths(response_mode: str) -> tuple[Path, ...]:
    """Return the system prompt source files used for the given response mode."""
    mode_map = {
        "decision": _SYSTEM_DECISION_TEMPLATE_PATH,
        "text": _SYSTEM_TEXT_TEMPLATE_PATH,
        "json_object": _SYSTEM_JSON_OBJECT_TEMPLATE_PATH,
    }
    return (_SYSTEM_TEMPLATE_PATH, mode_map.get(response_mode, _SYSTEM_JSON_OBJECT_TEMPLATE_PATH))


def assemble_system_prompt(context: "ModelContext") -> str:
    """Return the full system prompt assembled for a provider call."""
    capability_message = OpenAiModelDriver._capability_prompt(context)
    combined_system_prompt = context.system_prompt.strip()
    if capability_message:
        combined_system_prompt = f"{combined_system_prompt}\n\n{capability_message.strip()}".strip()
    return combined_system_prompt


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
        system_prompt: Stable role instructions owned by the agent definition.
        user_prompt: Rendered invocation prompt plus dynamic augmentations.
        messages: Structured conversation history for providers that support
            message-array inputs.
        response_mode: Runtime-level response contract for this model call.
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
    response_mode: str = "json_object"
    exact_input_payload: Any | None = None
    tools: tuple[ToolDefinition, ...] = ()
    subagents: tuple[CapabilityDefinition, ...] = ()
    skills: tuple[CapabilityDefinition, ...] = ()
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalized model response returned by a `ModelDriver`.

    Attributes:
        payload: Parsed structured payload consumed by the agent runtime.
        raw_text: Original model text before runtime normalization.
    """

    payload: dict[str, object]
    raw_text: str


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
    run_id: str | None = None


class ModelDriver(Protocol):
    """Provider-agnostic protocol for a single agent decision step."""

    def decide(
        self,
        *,
        agent_id: str | None,
        provider_name: str,
        model_name: str,
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


@dataclass(slots=True)
class OpenAiModelDriver:
    """OpenAI-backed model driver for the first draft runtime.

    Attributes:
        api_key: API key used to construct the OpenAI client lazily per call.
    """

    api_key: str
    on_request_trace: Any | None = None
    on_response_trace: Any | None = None

    def set_trace_callbacks(
        self,
        *,
        on_request: Any | None = None,
        on_response: Any | None = None,
    ) -> None:
        """Attach optional trace callbacks for exact provider I/O logging."""
        self.on_request_trace = on_request
        self.on_response_trace = on_response

    def decide(
        self,
        *,
        agent_id: str | None,
        provider_name: str,
        model_name: str,
        temperature: float,
        context: ModelContext,
    ) -> ModelResponse:
        """Request a structured decision from the OpenAI Responses API."""
        if provider_name != "openai":
            raise ValueError(f"Unsupported provider: {provider_name}")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI-backed agents.")

        client = OpenAI(api_key=self.api_key)
        if context.exact_input_payload is not None:
            model_input = context.exact_input_payload
        else:
            combined_system_prompt = assemble_system_prompt(context)
            if context.messages:
                model_input = list(context.messages)
                if model_input and model_input[0].get("role") == "system":
                    model_input[0] = {"role": "system", "content": combined_system_prompt}
                else:
                    model_input.insert(0, {"role": "system", "content": combined_system_prompt})
            else:
                model_input = [
                    {"role": "system", "content": combined_system_prompt},
                    {"role": "user", "content": context.user_prompt},
                ]
        if callable(self.on_request_trace):
            self.on_request_trace(
                ProviderRequestTrace(
                    agent_id=agent_id,
                    provider_name=provider_name,
                    model_name=model_name,
                    input_payload=model_input,
                    temperature=temperature,
                    run_id=context.run_id,
                )
            )
        response = client.responses.create(
            model=model_name,
            temperature=temperature,
            input=model_input,
        )
        raw_text = response.output_text.strip()
        if callable(self.on_response_trace):
            self.on_response_trace(
                ProviderResponseTrace(
                    agent_id=agent_id,
                    provider_name=provider_name,
                    model_name=model_name,
                    raw_text=raw_text,
                    parsed_payload=None,
                    run_id=context.run_id,
                )
            )
        if context.response_mode == "text":
            return ModelResponse(payload={"kind": "final_message", "message": raw_text}, raw_text=raw_text)
        normalized_text = _normalize_json_text(raw_text)
        payload = json.loads(normalized_text)
        return ModelResponse(payload=payload, raw_text=normalized_text)

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
        if skills:
            skills_list = json.dumps(
                [{"name": s.capability_id, "description": s.description} for s in skills],
                indent=2,
            )
            skills_section = (
                "## Skills\n\n"
                "<available_skills>\n"
                f"{skills_list}\n"
                "</available_skills>\n\n"
                "1. Review available skills and their descriptions to decide if a skill applies to the task.\n"
                "2. To invoke a skill, set `kind` to `invoke_skill` and `skill_name` to a valid skill name.\n"
                "3. After a skill is invoked, its full instructions will be injected into this conversation.\n"
                "   Follow those instructions to complete the task.\n"
                "4. You may need to read supporting files using the `read_skill_resource` tool — the skill\n"
                "   body will tell you when this is needed."
            )
        else:
            skills_section = ""
        return {
            "tools_json": tools_json,
            "subagents_json": subagents_json,
            "skills_section": skills_section,
        }

    @classmethod
    def _runtime_prompt(
        cls,
        context: ModelContext,
    ) -> str:
        """Build the shared and mode-specific runtime prompt block."""
        metadata = cls._capability_metadata(context.tools, context.subagents, context.skills)
        shared_prompt = _SYSTEM_TEMPLATE.format(**metadata).strip()
        mode_templates = {
            "decision": _SYSTEM_DECISION_TEMPLATE,
            "text": _SYSTEM_TEXT_TEMPLATE,
            "json_object": _SYSTEM_JSON_OBJECT_TEMPLATE,
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
                response_mode="json_object",
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

__all__ = [
    "CapabilityDefinition",
    "CapabilityParameter",
    "ModelContext",
    "ModelDriver",
    "ModelResponse",
    "OpenAiModelDriver",
    "ProviderRequestTrace",
    "ProviderResponseTrace",
    "ToolDefinition",
    "assemble_system_prompt",
    "runtime_prompt_source_paths",
]


def _normalize_json_text(raw_text: str) -> str:
    """Extract JSON text from plain or fenced model responses."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    return text
