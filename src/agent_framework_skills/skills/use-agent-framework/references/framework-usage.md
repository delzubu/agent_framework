# agent_framework — Framework Usage Reference

## Running the framework

```bash
# Interactive console
python -m agent_framework --console --env .env

# One-shot
python -m agent_framework --env .env --instruction "What is X?"

# Specific agent
python -m agent_framework --env .env --agent summariser --instruction "..."

# With LLM request/response tracing
python -m agent_framework --env .env --instruction "..." --llm-trace console
python -m agent_framework --env .env --instruction "..." --llm-trace file
python -m agent_framework --env .env --instruction "..." --llm-trace both

# Unified TraceEvent JSONL
python -m agent_framework --env .env --instruction "..." --runtime-trace-jsonl trace.jsonl

# Regression evaluation (XML harness)
python -m agent_framework --env .env --evaluate path/to/evaluation.xml
```

---

## Agent `.md` file structure

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

**`{{ param }}`** — double-brace substitution in the user prompt template. `{{instruction}}` is the default root-agent parameter injected by the CLI.

**`terminal_tools`** — when the model calls one of these, the loop exits immediately. The tool is NOT executed. `parameters` from the decision become the `AgentResult.message` (JSON-serialised). Used for structured-output agents.

### Adjacent runtime `.json` sidecar

Runtime metadata is loaded from a JSON file next to the agent Markdown file:

```text
agents/my_agent.md
agents/my_agent.json
```

Supported keys:

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

Use the sidecar for runtime metadata only. Keep parameters, tools, subagents, response mode, and prompts in the `.md`.

---

## Decision loop — all 6 kinds

Every model call produces exactly one decision. The framework parses and dispatches it, then loops.

### `final_message` — done, return result
```json
{
  "kind": "final_message",
  "message": "The answer is 42.",
  "parameters": {}
}
```

### `call_tool` — invoke a registered tool
```json
{
  "kind": "call_tool",
  "tool_name": "Read",
  "parameters": {
    "file_path": "/project/config.yaml"
  },
  "message": "Reading config."
}
```
Tool result is injected back into the conversation as a user message. Loop continues.

### `call_subagent` — delegate to one child agent
```json
{
  "kind": "call_subagent",
  "subagent_id": "order_lookup",
  "parameters": {
    "order_id": "ORD-12345"
  },
  "message": "Looking up the order."
}
```
Child runs to completion. Its `AgentResult.message` is injected back. Loop continues.

### `call_subagents` — batch dispatch (parallel or sequential)
```json
{
  "kind": "call_subagents",
  "mode": "parallel",
  "timeout_seconds": 120,
  "calls": [
    {"subagent_id": "researcher", "parameters": {"topic": "X"}, "output_key": "research"},
    {"subagent_id": "critic",     "parameters": {"topic": "X"}, "output_key": "critique"}
  ]
}
```
- `mode`: `"parallel"` | `"sequential"` — **required**, no default.
- `timeout_seconds`: optional; falls back to `SUBAGENT_BATCH_TIMEOUT_SECONDS` env var (default 300).
- `calls`: non-empty list; each requires `subagent_id`; `parameters` defaults to `{}`; `output_key` defaults to `call_<index>`.
- **Parallel children must not emit `callback`** — if they do, that child returns `status="blocked"` and siblings continue normally.
- All results are injected as one `<subagent_results>` block. Loop continues.

### `callback` — ask a question / escalate
```json
{
  "kind": "callback",
  "intent": "information_request",
  "message": "What is the preferred shipping address?",
  "parameters": {
    "parameter_name": "shipping_address"
  }
}
```
The model may also emit the intent name directly as `kind` (e.g. `"kind": "information_request"`); the framework normalises both forms.

**Callback intents:**
| Intent | Use |
|--------|-----|
| `information_request` | Agent needs a value from the user |
| `proposal_review` | Agent proposes an action and wants approval |
| `execution_recovery` | Something went wrong; agent needs guidance |
| `delegation_return` | Child returning to parent with result/question |
| `policy_or_approval` | Agent needs authorisation for a sensitive action |
| `guardrail_trip` | Policy violation detected; agent stops to report |

### `invoke_skill` — load a named skill
```json
{
  "kind": "invoke_skill",
  "skill_name": "refund_policy",
  "message": "Loading refund policy.",
  "parameters": {}
}
```
The skill's full file content is injected as a user message. Loop continues.

---

## Tools

### Built-in tools (auto-registered)
| Tool | Description |
|------|-------------|
| `Read` | Read a file |
| `Write` | Write a file (permission-gated) |
| `Edit` | Edit a file in-place (permission-gated) |
| `Bash` | Run a shell command (permission-gated) |
| `Glob` | Find files by glob pattern |
| `Grep` | Search file contents |
| `WebFetch` | Fetch a URL (permission-gated) |

### Custom tool structure

```
tools/
  my_tool/
    my_tool.md    ← tool definition (frontmatter + description)
    my_tool.py    ← Python module exporting build_tool()
```

**`my_tool.md`:**
```markdown
---
id: my_tool
description: Does something useful.
parameters:
  input_text:
    description: The text to process.
    required: true
    type: string
---
This tool processes text and returns a result.
```

**`my_tool.py`:**
```python
from agent_framework.tool import Tool, ToolDefinition, ToolResult

def build_tool(definition: ToolDefinition) -> Tool:
    class MyTool(Tool):
        def invoke(self, parameters: dict) -> ToolResult:
            text = parameters["input_text"]
            return ToolResult(output=f"Processed: {text}")
    return MyTool(definition)
```

### Permission-gated tools

Call `host.user_comm.request_permission(summary, details)` before taking action. The user sees a `[y/n/a/d]` prompt. `a` = allow all for the session, `d` = deny all for the session.

```python
class MyWriteTool(Tool):
    def invoke(self, parameters: dict) -> ToolResult:
        import asyncio
        granted = asyncio.get_event_loop().run_until_complete(
            self.host.user_comm.request_permission(
                summary=f"Write to {parameters['path']}",
                details=parameters.get("content", "")[:200],
            )
        )
        if not granted:
            return ToolResult(output="Permission denied.")
        ...
```

---

## Sub-agents in detail

### Single sub-agent call
Use `call_subagent` when you need to delegate to exactly one child and wait for its result before continuing.

### Batch dispatch patterns

```
# a → b → c  (sequential)
{"kind": "call_subagents", "mode": "sequential", "calls": [
  {"subagent_id": "a", "output_key": "a_result"},
  {"subagent_id": "b", "output_key": "b_result"},
  {"subagent_id": "c", "output_key": "c_result"}
]}

# a ‖ b ‖ c  (parallel fan-out)
{"kind": "call_subagents", "mode": "parallel", "calls": [
  {"subagent_id": "a", "output_key": "a_result"},
  {"subagent_id": "b", "output_key": "b_result"},
  {"subagent_id": "c", "output_key": "c_result"}
]}

# (a ‖ b) → c  (two decisions)
# Decision 1:
{"kind": "call_subagents", "mode": "parallel", "calls": [
  {"subagent_id": "a", "output_key": "a_result"},
  {"subagent_id": "b", "output_key": "b_result"}
]}
# Decision 2 (after parallel batch returns):
{"kind": "call_subagent", "subagent_id": "c", "parameters": {"a": "...", "b": "..."}}
```

Context isolation: each child starts with a fresh conversation — no parent history is leaked.

---

## File reference injection

Use `@filename` or `@"path with spaces.ext"` tokens in any prompt string. The framework expands them to file contents before the agent sees the prompt.

- **Text files** → `<file name="note.txt">\n...content...\n</file>`
- **Binary files** → `<file name="deck.pptx" encoding="base64">\n...base64...\n</file>`
- **Missing file** → token left unchanged (no error)

```
# In a user prompt or case file:
Review the following deck: @"q1-review.pptx"
Analyse the config: @config.yaml
```

Custom resolver (in an initializer or host setup):
```python
from agent_framework.file_reference import FileReferenceResolver

class PptxTextResolver:
    def resolve(self, path) -> str:
        # extract text from pptx, return as string
        ...

host.file_ref_resolver = PptxTextResolver()
```

---

## Skills

Skills are `.md` files with YAML frontmatter (`id`, `description`, `priority`). The framework injects a catalog of skill names + descriptions into the conversation at session start. When the model emits `invoke_skill`, the full skill file is injected.

```
SKILLS_DIRECTORY=path/to/skills    # single directory
SKILLS_DIRECTORIES=path/a,path/b   # multiple directories
SKILLS_CATALOG_MAX_TOKENS=2000     # catalog budget (lower-priority skills dropped first)
```

---

## Configuration reference (`.env`)

```
# Provider
DEFAULT_PROVIDER=openai            # openai | dial
OPENAI_API_KEY=sk-...
DEFAULT_MODEL=gpt-4o-mini          # comma-separated fallback list: gpt-4o,gpt-4o-mini
AGENT_MODELS=agent1=m1,m2|agent2=m3   # per-agent model overrides

# DIAL provider
DIAL_BASE_URL=https://your-dial.example.com
DIAL_API_VERSION=2024-10-21
DIAL_API_KEY=...

# Directories
AGENT_DIRECTORY=agents
TOOLS_DIRECTORY=tools
WORLD_DIRECTORY=world              # sandbox for file operations
ROOT_AGENT=root                    # default agent for CLI

# Skills
SKILLS_DIRECTORY=path/to/skills
SKILLS_DIRECTORIES=path/a,path/b
SKILLS_CATALOG_MAX_TOKENS=2000

# Commands
COMMANDS_DIRECTORY=path/to/commands
COMMANDS_DIRECTORIES=path/a,path/b

# MCP
MCP_CONFIG_PATH=path/to/mcp.json   # explicit path (default: auto-discover upward)
MCP_ENABLED=true

# Parallel sub-agents
SUBAGENT_BATCH_TIMEOUT_SECONDS=300    # wall-clock deadline per call_subagents batch
SUBAGENT_MAX_PARALLELISM=8            # max calls per call_subagents decision
SUBAGENT_BATCH_MAX_CALLBACK_ROUNDS=5  # max callback-resolve-resume rounds per batch

# Reliability
MISSING_TOOL_POLICY=graceful          # graceful (skip + trace) | strict (fail run)
```

---

## AgentBehavior extensibility

Subclass `AgentBehavior` and register it via the agent's adjacent `.json` sidecar.

```python
from agent_framework.agents.agent_behavior import AgentBehavior
from agent_framework.agents.agent_end_hook_decision import AgentEndHookDecision
from agent_framework.agents.agent_hook_decision import AgentHookDecision


class MyBehavior(AgentBehavior):
    def attach(self, agent) -> None:
        self.agent = agent

    def before_run(self, agent, host, *, run, caller_id):
        if not run.parameter_values.get("instruction"):
            return AgentHookDecision(
                continue_run=True,
                system_message="Instruction is missing. Ask for clarification before proceeding.",
            )
        return None

    def respond_to_callback(self, agent, host, *, callee_id, prompt):
        return None

    def after_run(self, agent, host, *, run, caller_id, result):
        if result.status == "completed" and not result.message.strip():
            return AgentEndHookDecision(
                continue_run=True,
                prompt_fragments=(
                    "<verification_feedback>Your previous answer was empty. Return a concrete result.</verification_feedback>",
                ),
            )
        return None
```

**Sidecar file** (`agents/my_agent.json`):

```json
{
  "behaviors": [
    "my_module.MyBehavior"
  ]
}
```

Behavior guidance:

- `before_run()` runs after initial parameter state refresh
- if a behavior mutates prompt state or seed inputs, call `agent.refresh_parameter_state(run)` before returning
- `respond_to_callback()` can answer child callbacks deterministically
- `after_run()` may return a replacement `AgentResult`, or `AgentEndHookDecision(continue_run=True, ...)` to request one more loop iteration

---

## Common patterns

### Pass context to a sub-agent via parameters
```json
{
  "kind": "call_subagent",
  "subagent_id": "summariser",
  "parameters": {
    "document_text": "... full text here ...",
    "max_sentences": 3
  }
}
```

### Implement a tool that reads from the world directory
```python
import os
from pathlib import Path

class ReadWorldTool(Tool):
    def invoke(self, parameters: dict) -> ToolResult:
        world = Path(os.environ.get("WORLD_DIRECTORY", "."))
        path = world / parameters["filename"]
        return ToolResult(output=path.read_text(encoding="utf-8"))
```

### Write a callback-aware agent
Parent agent frontmatter declares the child:
```yaml
subagents:
  - data_collector
```

Parent's `respond_to_callback` behavior intercepts the child's question and answers it programmatically or re-escalates to the user.

### Structured-output agent using terminal tools
```yaml
terminal_tools:
  - submit_extraction
```
When the model calls `submit_extraction({"name": "Alice", "age": 30})`, the loop exits with `result.message = '{"name": "Alice", "age": 30}'`. Caller parses with `json.loads(result.message)`.
