"""Typed tool contracts and loader for markdown-defined tools.

Tools follow the same split as agents:
- Markdown defines the caller-visible contract.
- A sibling Python module defines the implementation.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING
from uuid import uuid4

import yaml

if TYPE_CHECKING:
    from agent_framework.host import AgentHost


@dataclass(frozen=True, slots=True)
class ToolParameter:
    """Declared invocation parameter for a tool."""

    name: str
    description: str
    required: bool = True
    value_type: str = "string"
    default: Any = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Caller-visible tool contract loaded from Markdown."""

    tool_id: str
    description: str
    parameters: tuple[ToolParameter, ...] = ()
    source_path: Path | None = None
    documentation: str = ""
    parameters_schema: dict[str, Any] | None = field(default=None)
    """When set, use as the full JSON Schema ``parameters`` object for the provider.

    Supersedes the flat ``parameters`` tuple (needed for nested tool argument shapes).
    """

    def to_model_payload(self) -> dict[str, object]:
        """Convert the definition to the model-facing tool shape."""
        if self.parameters_schema is not None:
            return {
                "name": self.tool_id,
                "description": self.description,
                "parameters": dict(self.parameters_schema),
            }
        properties: dict[str, object] = {}
        required: list[str] = []
        for item in self.parameters:
            properties[item.name] = {
                "type": item.value_type,
                "description": item.description,
            }
            if item.required:
                required.append(item.name)
        payload: dict[str, object] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            payload["required"] = required
        return {
            "name": self.tool_id,
            "description": self.description,
            "parameters": payload,
        }


@dataclass(slots=True)
class Tool:
    """Base class for concrete tools loaded from sibling Python modules."""

    definition: ToolDefinition
    source_path: Path | None = None

    @property
    def name(self) -> str:
        """Return the stable tool identifier."""
        return self.definition.tool_id

    @property
    def description(self) -> str:
        """Return the caller-visible tool description."""
        return self.definition.description

    def model_definition(self) -> ToolDefinition:
        """Return the model-visible tool definition."""
        return self.definition

    def invoke(self, arguments: dict[str, Any], host: "AgentHost") -> str:
        """Execute the tool with validated arguments."""
        raise NotImplementedError

    @classmethod
    def from_name(cls, name: str, tools_directory: str | Path) -> "Tool":
        """Load a tool from `<tools_directory>/<name>.md` and `.py`."""
        root = Path(tools_directory).resolve()
        markdown_path = (root / f"{name}.md").resolve()
        python_path = (root / f"{name}.py").resolve()
        if not markdown_path.exists():
            raise KeyError(f"Unknown tool '{name}': missing {markdown_path}.")
        if not python_path.exists():
            raise KeyError(f"Unknown tool '{name}': missing {python_path}.")

        definition = _load_tool_definition(markdown_path)
        module_name = f"agent_tool_{name}_{uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, python_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load tool module from {python_path}.")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        build_tool = getattr(module, "build_tool", None)
        if not callable(build_tool):
            raise ValueError(f"Tool module {python_path} must export callable 'build_tool'.")

        tool = build_tool(definition)
        if not isinstance(tool, Tool):
            raise ValueError(f"Tool module {python_path} returned {type(tool).__name__}, expected Tool.")
        if tool.name != definition.tool_id:
            raise ValueError(
                f"Tool module {python_path} returned id '{tool.name}', expected '{definition.tool_id}'."
            )
        tool.source_path = python_path
        return tool


def _load_tool_definition(path: Path) -> ToolDefinition:
    """Load a tool definition from Markdown frontmatter and optional body text."""
    raw_text = path.read_text(encoding="utf-8")
    if not raw_text.startswith("---"):
        raise ValueError(f"Tool markdown {path} must start with frontmatter.")

    parts = raw_text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Tool markdown {path} must contain a closing frontmatter delimiter.")
    metadata = yaml.safe_load(parts[1]) or {}
    documentation = parts[2].strip()
    parameter_map = metadata.get("parameters", {}) or {}
    if not isinstance(parameter_map, dict):
        raise ValueError(f"Tool markdown {path} parameters must be a mapping.")

    parameters = tuple(
        ToolParameter(
            name=name,
            description=str(spec.get("description", "")).strip(),
            required=bool(spec.get("required", True)),
            value_type=str(spec.get("type", "string")).strip(),
            default=spec.get("default"),
        )
        for name, spec in parameter_map.items()
    )
    tool_id = str(metadata.get("id", path.stem)).strip()
    if not tool_id:
        raise ValueError(f"Tool markdown {path} must declare a non-empty id.")
    return ToolDefinition(
        tool_id=tool_id,
        description=str(metadata.get("description", "")).strip(),
        parameters=parameters,
        source_path=path,
        documentation=documentation,
    )


__all__ = ["Tool", "ToolDefinition", "ToolParameter"]
