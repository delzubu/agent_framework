# Agent framework — host embedding reference

This reference covers running the `agent_framework` host from Python and the CLI, wiring sub-agent dispatch, file-reference injection, `AgentBehavior` extensibility, and common host-integration patterns.

## Running via CLI

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
