# agent_framework — Tool Authoring Reference

Use this reference before creating or editing a custom tool.

---

## Tool shape

A custom tool is a Markdown contract plus a sibling Python implementation:

```text
tools/
  send_email.md
  send_email.py
```

Rules:

- the filename stem must match the tool id
- the Python module must export `build_tool(definition) -> Tool`
- the returned object must be an instance of `Tool`
- the returned tool id must match the markdown `id`

Loader path:

- Markdown is loaded first into `ToolDefinition`
- Python is imported second
- the framework calls `build_tool(definition)`

---

## Markdown contract

Example:

```markdown
---
id: send_email
description: Send an email message.
parameters:
  to_address:
    description: Recipient email address.
    required: true
    type: string
  subject:
    description: Subject line.
    required: true
    type: string
  body:
    description: Plain-text message body.
    required: true
    type: string
---
Send an email message using the configured mail backend.
```

What the Markdown file defines:

- stable tool id
- caller-visible description
- flat parameter contract
- optional documentation body

Use the documentation body for:

- usage notes
- edge cases
- important constraints the model should know

---

## Python implementation

Minimal implementation:

```python
from __future__ import annotations

from typing import Any

from agent_framework.tool import Tool, ToolDefinition


def build_tool(definition: ToolDefinition) -> Tool:
    return SendEmailTool(definition=definition)


class SendEmailTool(Tool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        to_address = arguments["to_address"]
        subject = arguments["subject"]
        body = arguments["body"]
        return f"Queued email to {to_address} with subject {subject!r}."
```

Required runtime signature:

```python
def build_tool(definition: ToolDefinition) -> Tool: ...
def invoke(self, arguments: dict[str, Any], host: AgentHost) -> str: ...
```

Return value:

- return a plain string result
- the runtime injects that result back into the conversation

Do not:

- return arbitrary custom objects
- depend on prompt-side parsing of undocumented output
- silently ignore missing required arguments

---

## Public tool types

Core types in `src/agent_framework/tool.py`:

- `ToolParameter`
- `ToolDefinition`
- `Tool`

Important `ToolDefinition` fields:

| Field | Meaning |
|---|---|
| `tool_id` | Stable runtime/model-visible tool id |
| `description` | Tool summary shown to the model |
| `parameters` | Flat parameter list |
| `documentation` | Markdown body text |
| `parameters_schema` | Optional full JSON Schema override |

Use `parameters_schema` when you need nested argument objects or arrays that are awkward to express with the flat frontmatter contract.

---

## Flat parameters vs full JSON Schema

### Flat frontmatter parameters

Good default for most tools:

- few scalar fields
- shallow object shape
- simple `string` / `integer` / `number` / `boolean`

### Full `parameters_schema`

Use when you need:

- nested objects
- arrays of structured items
- more exact JSON Schema control for provider-native tool calling

That is a Python-side concern on `ToolDefinition`, not something the Markdown loader currently builds automatically from frontmatter.

---

## Registering and allowing a tool

Making a tool file exist is not enough. The agent must also allow it.

1. Put the tool in `TOOLS_DIRECTORY`
2. Reference it from the agent's `tools:` list

Example:

```yaml
tools:
  - Read
  - send_email
```

Missing-tool behavior depends on:

```env
MISSING_TOOL_POLICY=graceful
```

Modes:

- `graceful`: skip unloadable tools and continue, with trace/log visibility
- `strict`: fail the run when a declared tool cannot be loaded

---

## Permission-gated tools

If the tool performs a sensitive action, ask the host for permission.

Use:

- `PermissionRequest`
- `host._run_user_comm_coro(host.user_comm.request_permission(...))`

Example:

```python
from agent_framework.tool import Tool, ToolDefinition
from agent_framework.user_communication import PermissionRequest


class SendEmailTool(Tool):
    def invoke(self, arguments, host):
        request = PermissionRequest(
            tool_name=self.name,
            action="network",
            resource=arguments["to_address"],
            summary=f"Send email to {arguments['to_address']}",
            details={"subject": arguments.get("subject", "")},
        )
        decision = host._run_user_comm_coro(host.user_comm.request_permission(request))
        if not decision.allowed:
            return "Permission denied."
        return "Email sent."
```

Action values:

- `write`
- `execute`
- `network`
- `delete`
- `other`

Good candidates for permission gating:

- filesystem mutation
- shell execution
- network calls
- destructive API operations

---

## Host access patterns inside tools

The `host` argument is available so tools can use runtime services.

Common uses:

- read config from `host.config`
- access registries
- call memory helpers such as `host.create_memory(...)` or `host.get_memory(...)`
- request user permission
- emit deterministic side effects that belong outside the prompt

Prefer host APIs over reaching into unrelated globals.

---

## Memory-aware tools

If a tool may produce very large output, consider storing the bulk artifact in memory and returning a smaller summary plus a `mem://...` ref.

Good pattern:

- create/store memory entry
- return a compact response containing the ref

Example use cases:

- OCR / parsing tools
- deck/document normalization
- batch search results

Do not move prompt text into memory from a tool unless that tool is explicitly designed to create runtime artifacts.

---

## Testing guidance

For tool tests:

- instantiate the tool directly with a `ToolDefinition`
- pass a fake or mocked host
- assert on the returned string
- cover permission denied / error paths

Good things to test:

- required argument handling
- permission-gated branches
- deterministic output formatting
- memory interaction if the tool writes refs

Relevant examples:

- `tests/test_builtin_tools.py`
- `tests/test_missing_tool_policy.py`
- `tests/test_tool_parameters_schema.py`

---

## Common mistakes

- mismatched markdown id, filename stem, and Python return id
- returning a non-string result
- forgetting to list the tool in the agent's `tools:` allow-list
- putting complex nested schema only in frontmatter and expecting full JSON Schema behavior
- bypassing permission checks for destructive actions
- teaching the model one parameter shape while the Python code expects another

---

## Companion references

- `references/framework-usage.md` for runtime contracts
- `references/agent-usage.md` for when tool logic should really be a behavior instead
- `references/env-reference.md` for `TOOLS_DIRECTORY`, `WORLD_DIRECTORY`, and related config
