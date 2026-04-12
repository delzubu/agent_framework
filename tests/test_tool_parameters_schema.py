"""ToolDefinition.parameters_schema for nested DIAL/OpenAI tool JSON."""

from __future__ import annotations

from agent_framework.drivers.dial import _build_tools
from agent_framework.tool import ToolDefinition


def test_build_tools_uses_parameters_schema_when_set() -> None:
    nested = {
        "type": "object",
        "required": ["items"],
        "properties": {"items": {"type": "array", "items": {"type": "string"}}},
    }
    t = ToolDefinition(
        tool_id="nested_tool",
        description="d",
        parameters_schema=nested,
    )
    aidial_tools = _build_tools((t,))
    assert aidial_tools is not None
    fn = aidial_tools[0].function
    assert fn.name == "nested_tool"
    assert fn.parameters == nested
