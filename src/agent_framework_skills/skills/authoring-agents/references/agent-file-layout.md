# Agent file layout — .md and .json

Use this reference before creating or editing any agent `.md` or adjacent `.json` sidecar file.

---

## Agent `.md` file structure

An agent Markdown file has three sections separated by `---` lines:

```markdown
---
id: my_agent              # Required. Must match filename stem (my_agent.md).
role: My agent role       # Human-readable role shown in traces.
description: |            # Shown to parent agents as capability description.
  What this agent does.

parameters:               # Parameters callers must supply per invocation.
  ticket_text:
    description: The customer ticket text.
    required: true
    type: string           # string | integer | number | boolean | object | array
  customer_tier:
    description: Customer tier.
    required: false
    type: string
    default: standard

tools:                    # Tool ids this agent may call (must exist in TOOLS_DIRECTORY).
  - Read
  - WebFetch

subagents:                # Child agent ids this agent may delegate to.
  - order_lookup

skills:                   # Skill names this agent may invoke.
  - refund_policy

terminal_tools: []        # Tool names that exit the loop immediately without executing.
                          # Result: AgentResult(status="completed", message=json.dumps(parameters))

model: gpt-4o             # Optional model override for this agent.
response_mode: decision   # decision (default) | text | json_object
---
System prompt goes here.
The agent sees this on every turn as the system message.
---
{{instruction}}
```

The runtime expects exactly three sections in this order:

1. **Frontmatter** — YAML between the first `---` pair
2. **System prompt** — text between the second and third `---`
3. **User prompt template** — text after the third `---`

### Frontmatter field notes

| Field | Notes |
|-------|-------|
| `id` | Must exactly match the filename stem. Required. |
| `role` | Human-readable; appears in traces and logs. |
| `description` | Shown to parent agents when they choose subagents. |
| `parameters` | Defines the agent's input contract. Each parameter has `description`, `required`, `type`, optional `default`. |
| `tools` | Allow-list of tool ids. Tools not listed here cannot be called even if they exist in `TOOLS_DIRECTORY`. |
| `subagents` | Allow-list of child agent ids. |
| `skills` | Allow-list of skill names the agent may invoke via `invoke_skill`. |
| `terminal_tools` | When the model calls one of these, the loop exits immediately. The tool is NOT executed. `parameters` from the decision become `AgentResult.message` (JSON-serialised). Used for structured-output agents. |
| `model` | Optional per-agent model override; comma-separated for fallback chain (e.g. `gpt-4o,gpt-4o-mini`). |
| `response_mode` | `decision` (default, typed JSON decisions), `text` (prose), `json_object` (structured JSON with callback patterns). |

### `{{ param }}` substitution

Double-brace tokens in the user prompt template are replaced at runtime with the corresponding parameter value. `{{instruction}}` is the default root-agent parameter injected by the CLI.

Example:
```
{{ticket_text}}
```
is replaced with the value of the `ticket_text` parameter before the agent sees the prompt.

---

## Adjacent runtime `.json` sidecar

Place a JSON file with the same stem next to the agent Markdown file to supply runtime metadata:

```text
agents/my_agent.md
agents/my_agent.json
```

### Supported keys

```json
{
  "model": "gpt-4.1,gpt-4o-mini",
  "provider": "openai",
  "temperature": 0.1,
  "behavior": "pkg.behaviors.InputGuard",
  "behaviors": ["pkg.behaviors.InputGuard", "pkg.behaviors.OutputVerifier"],
  "can_query_caller": true,
  "can_use_host_interaction": true
}
```

| Key | Effect |
|-----|--------|
| `model` | Override model list for this agent (comma-separated fallback chain) |
| `provider` | Override provider for this agent |
| `temperature` | Override sampling temperature |
| `behavior` | Attach one `AgentBehavior` by dotted Python path |
| `behaviors` | Attach multiple behaviors in order |
| `can_query_caller` | Allow/disallow callbacks to the caller agent |
| `can_use_host_interaction` | Allow/disallow direct host/user callbacks |

**Rule:** Use the sidecar for runtime metadata only. Keep parameters, tools, subagents, skills, response mode, and all prompt text in the `.md`.

Do not put these in the sidecar:
- agent parameter definitions
- tools or subagents allow-lists
- system prompt text
- user prompt template
