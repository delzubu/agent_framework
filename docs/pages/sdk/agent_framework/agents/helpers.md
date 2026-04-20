---
title: agent_framework.agents.helpers
layout: default
sdk_page: true
---


# `agent_framework.agents.helpers`

## API Summary

Shared helper functions for agent loading and prompt parsing.

## Source

`src/agent_framework/agents/helpers.py`

## Classes

- [`AgentMarkdownError`](helpers/AgentMarkdownError.html)

## Functions

### `split_markdown_sections`

```python
def split_markdown_sections(raw_text: str, *, source_path: Path) -> tuple[str, str, str]
```

Split the Markdown file into frontmatter, system prompt, and user template.

Supported layouts:

* **Three delimiters** (classic): ``---`` / YAML / ``---`` / system / ``---`` / user.
* **Two delimiters**: YAML from the start of the file (no leading ``---``), then ``---``,
  system prompt, ``---``, user template. Two separator lines still produce three regions.

### `optional_text`

```python
def optional_text(value: object) -> str | None
```

Return a stripped string value or `None` if the result is empty.

### `stringify_parameter_value`

```python
def stringify_parameter_value(value: Any) -> str
```

Render structured parameter values into prompt-safe strings.

### `apply_runtime_placeholders`

```python
def apply_runtime_placeholders(template: str, values: dict[str, str]) -> str
```

Replace simple `{name}` placeholders in runtime prompt text.

### `coerce_parameter_value`

```python
def coerce_parameter_value(spec: AgentParameter, raw_value: str) -> Any
```

Coerce XML/tag-extracted text into the declared parameter type.

### `extract_prompt_value`

```python
def extract_prompt_value(spec: AgentParameter, prompt: str) -> Any | None
```

Extract one parameter value from tagged prompt content.

### `resolve_schema_path`

```python
def resolve_schema_path(source_path: Path, raw_path: object) -> Path | None
```

Resolve an optional schema path declared in frontmatter.

### `load_runtime_metadata`

```python
def load_runtime_metadata(source_path: Path) -> dict[str, object]
```

Load runtime-sidecar JSON metadata next to an agent markdown file.

### `decision_to_dict`

```python
def decision_to_dict(decision: AgentDecision) -> dict[str, object]
```

Convert one normalized decision into a serializable dictionary.

### `parse_behavior_ids`

```python
def parse_behavior_ids(runtime_metadata: dict[str, object]) -> tuple[str, ...]
```

Parse ordered behavior ids from runtime metadata.

### `parse_allowed_tool_names`

```python
def parse_allowed_tool_names(raw_tools: object) -> tuple[str, ...]
```

Parse agent frontmatter tool references into a stable allow-list.

### `agent_to_capability_definition`

```python
def agent_to_capability_definition(agent: 'Agent') -> CapabilityDefinition
```

Convert an agent definition into model-facing subagent metadata.
