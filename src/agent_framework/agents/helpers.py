"""Shared helper functions for agent loading and prompt parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_framework.model import CapabilityDefinition, CapabilityParameter

from .agent_decision import AgentDecision
from .agent_parameter import AgentParameter

if TYPE_CHECKING:
    from .agent import Agent

PLACEHOLDER_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
SECTION_PATTERN = re.compile(r"^---\s*$", re.MULTILINE)


def split_markdown_sections(raw_text: str) -> tuple[str, str, str]:
    """Split the Markdown file into frontmatter, system prompt, and template."""
    matches = list(SECTION_PATTERN.finditer(raw_text))
    if len(matches) < 3:
        raise ValueError("Agent markdown must contain frontmatter, system prompt, and user prompt template.")
    frontmatter = raw_text[matches[0].end():matches[1].start()]
    system_prompt = raw_text[matches[1].end():matches[2].start()]
    user_prompt_template = raw_text[matches[2].end():]
    return frontmatter, system_prompt, user_prompt_template


def optional_text(value: object) -> str | None:
    """Return a stripped string value or `None` if the result is empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def stringify_parameter_value(value: Any) -> str:
    """Render structured parameter values into prompt-safe strings."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, sort_keys=True)
    return str(value)


def apply_runtime_placeholders(template: str, values: dict[str, str]) -> str:
    """Replace simple `{name}` placeholders in runtime prompt text."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", value)
    return rendered


def coerce_parameter_value(spec: AgentParameter, raw_value: str) -> Any:
    """Coerce XML/tag-extracted text into the declared parameter type."""
    if spec.value_type == "string":
        stripped = raw_value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return stripped
        return stripped
    if spec.value_type == "integer":
        return int(raw_value.strip())
    if spec.value_type == "number":
        return float(raw_value.strip())
    if spec.value_type == "boolean":
        normalized = raw_value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        raise ValueError("Expected boolean text.")
    if spec.value_type in {"object", "array"}:
        return json.loads(raw_value)
    return raw_value


def extract_prompt_value(spec: AgentParameter, prompt: str) -> Any | None:
    """Extract one parameter value from tagged prompt content."""
    matches = list(
        re.finditer(
            rf"<{re.escape(spec.name)}>(.*?)</{re.escape(spec.name)}>",
            prompt,
            flags=re.DOTALL | re.IGNORECASE,
        )
    )
    for match in reversed(matches):
        raw_value = match.group(1).strip()
        if PLACEHOLDER_PATTERN.fullmatch(raw_value):
            continue
        return coerce_parameter_value(spec, raw_value)

    if spec.name == "difficulty_class":
        dc_match = re.search(r"\b(?:dc|difficulty class)\s*(\d+)\b", prompt, flags=re.IGNORECASE)
        if dc_match is not None:
            return int(dc_match.group(1))

    if spec.name == "skill_name":
        skill_match = re.search(
            r"\b(?:run|make|perform|resolve)\s+(?:an?\s+)?([a-z][a-z ]*?)\s+check\b",
            prompt,
            flags=re.IGNORECASE,
        )
        if skill_match is not None:
            return skill_match.group(1).strip().title()
    return None


def resolve_schema_path(source_path: Path, raw_path: object) -> Path | None:
    """Resolve an optional schema path declared in frontmatter."""
    text = optional_text(raw_path)
    if text is None:
        return None
    path = Path(text)
    return path if path.is_absolute() else (source_path.parent.parent / path).resolve()


def load_runtime_metadata(source_path: Path) -> dict[str, object]:
    """Load runtime-sidecar JSON metadata next to an agent markdown file."""
    config_path = source_path.with_suffix(".json")
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def decision_to_dict(decision: AgentDecision) -> dict[str, object]:
    """Convert one normalized decision into a serializable dictionary."""
    payload: dict[str, object] = {
        "kind": decision.kind,
        "message": decision.message,
        "parameters": decision.parameters,
    }
    if decision.callback_intent is not None:
        payload["intent"] = decision.callback_intent
    if decision.subagent_id is not None:
        payload["subagent_id"] = decision.subagent_id
    if decision.tool_name is not None:
        payload["tool_name"] = decision.tool_name
    return payload


def parse_behavior_ids(runtime_metadata: dict[str, object]) -> tuple[str, ...]:
    """Parse ordered behavior ids from runtime metadata."""
    raw_behaviors = runtime_metadata.get("behaviors")
    if raw_behaviors in (None, "", (), []):
        raw_behavior = optional_text(runtime_metadata.get("behavior"))
        return (raw_behavior,) if raw_behavior else ()
    if not isinstance(raw_behaviors, list):
        raise ValueError("Agent behaviors must be declared as a list of behavior ids.")
    behavior_ids: list[str] = []
    for item in raw_behaviors:
        behavior_id = optional_text(item)
        if behavior_id is None:
            raise ValueError("Agent behavior ids must be non-empty strings.")
        behavior_ids.append(behavior_id)
    return tuple(behavior_ids)


def parse_allowed_tool_names(raw_tools: object) -> tuple[str, ...]:
    """Parse agent frontmatter tool references into a stable allow-list."""
    if raw_tools in (None, "", (), []):
        return ()
    if isinstance(raw_tools, dict):
        return tuple(str(name).strip() for name in raw_tools if str(name).strip())
    if isinstance(raw_tools, list):
        names: list[str] = []
        for item in raw_tools:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
                continue
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                if name:
                    names.append(name)
                    continue
            raise ValueError("Agent tools must be declared as tool names.")
        return tuple(names)
    raise ValueError("Agent tools must be declared as a list or mapping.")


def agent_to_capability_definition(agent: "Agent") -> CapabilityDefinition:
    """Convert an agent definition into model-facing subagent metadata."""
    return CapabilityDefinition(
        capability_id=agent.agent_id,
        description=agent.description,
        parameters=tuple(
            CapabilityParameter(
                name=item.name,
                description=item.description,
                required=item.required,
                value_type=item.value_type,
            )
            for item in agent.parameters
        ),
    )
