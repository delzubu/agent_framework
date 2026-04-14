# Extension Points & Hooks

> This document is part of the `agent_framework` architecture reference.
> See also: [Overview](./overview.md) · [Agent Runtime](./agent-runtime.md) · [Host & Orchestration](./host-orchestration.md) · [Model Abstraction](./model-abstraction.md) · [Interface Specifications](./interfaces.md)

---

## 1. Overview

The framework provides four extension mechanisms, ordered from most to least invasive:

| Mechanism | Invasiveness | Scope |
|-----------|-------------|-------|
| `AgentBehavior` subclass | Low — Python module loaded dynamically | Per-agent behavior customization |
| `SequentialHook` callbacks (`+=`) | Low — subscribe to existing hooks | Observation and intervention at lifecycle points |
| Custom `Tool` | None to agent code | New capabilities accessible to agents |
| Custom `ModelDriver` | None to agent code | New LLM provider support |
| `RuntimeTracer` / `TraceSubscriber` | Low — subscribe or replace `host.runtime_tracer` | Unified trace pipeline (JSONL, debugger UI, custom sinks) |

All extension points are designed so that agent Markdown definitions and the core loop in `Agent.run()` need not be modified.

---

## 2. `AgentBehavior` — Per-Agent Behavior Extension

`AgentBehavior` (`src/agent_framework/agents/agent_behavior.py`) is a base class for agent-scoped customizations. Instances are loaded dynamically from Python modules at agent load time and receive lifecycle callbacks throughout the agent's run.

### 2.1 Contract

```python
class AgentBehavior:
    def attach(self, agent: "Agent") -> None:
        raise NotImplementedError

    def before_run(
        self, agent: "Agent", host: AgentHostProtocol,
        *, run: AgentRun, caller_id: str | None
    ) -> "AgentHookDecision | None":
        return None

    def respond_to_callback(
        self, agent: "Agent", host: AgentHostProtocol,
        *, callee_id: str, prompt: str
    ) -> str | None:
        return None

    def after_run(
        self, agent: "Agent", host: AgentHostProtocol,
        *, run: AgentRun, caller_id: str | None, result: AgentResult
    ) -> "AgentEndHookDecision | AgentResult | None":
        return None
```

### 2.2 `attach(agent)`

Called once when the behavior is loaded, immediately after instantiation. The behavior should use `attach()` to subscribe to agent hooks:

```python
def attach(self, agent: Agent) -> None:
    agent.onPreTool += self._on_pre_tool
    agent.onPostTool += self._on_post_tool
    agent.onPreSubagent += self._on_pre_subagent

def _on_pre_tool(self, event: ToolStartEvent) -> ToolHookDecision | None:
    ...
```

`attach()` is the correct place to subscribe to `SequentialHook` instances. Do not subscribe in `__init__` — the agent object is not yet available.

### 2.3 `before_run(agent, host, *, run, caller_id)`

Called after `_create_run()` and `refresh_parameter_state()` but before the main loop. The run state is fully initialized at this point.

**Inspection available:** `run.parameter_values`, `run.missing_parameters`, `run.invalid_parameters`, `run.prompt_fragments`.

**Can call:** `agent.refresh_parameter_state(run)` to re-extract parameters after modifying fragments.

**Return values:**

| Return | Effect |
|--------|--------|
| `None` | Continue normally |
| `AgentHookDecision(continue_run=True)` | Continue (same as None) |
| `AgentHookDecision(continue_run=False)` | Skip remaining behaviors in chain; do not short-circuit run |
| `AgentHookDecision(final_result=AgentResult(...))` | **Short-circuit**: return this result immediately without entering the loop |
| `AgentHookDecision(system_message="...")` | Append a `<system_message>` fragment before starting the loop |

### 2.4 `respond_to_callback(agent, host, *, callee_id, prompt)`

Called by `AgentHost.resolve_callback()` when a subagent sends a callback to this agent (as caller). The behavior can resolve the callback without running the full agent loop.

| Return | Effect |
|--------|--------|
| `None` | Behavior cannot resolve; try next behavior or fall through to agent run |
| `str` | Use this string as the callback response; do not run the agent |

### 2.5 `after_run(agent, host, *, run, caller_id, result)`

Called after each agent run iteration when a result is produced (by `_run_post_agent_hooks()`). Can override the result or request another loop iteration.

| Return | Effect |
|--------|--------|
| `None` | Use the current result |
| `AgentResult(...)` | **Replace** the result with this value |
| `AgentEndHookDecision(continue_run=False)` | Use `final_result` if set, else use current result |
| `AgentEndHookDecision(continue_run=True, ...)` | **Request another loop iteration**: the agent will run again |

**`AgentEndHookDecision` fields for loop continuation:**

| Field | Type | Description |
|-------|------|-------------|
| `continue_run` | `bool` | `True` to request another iteration |
| `prompt_fragments` | `tuple[str, ...]` | Replace run's fragments by tag name (upsert semantics) |
| `append_prompt_fragments` | `tuple[str, ...]` | Always append to run's fragments (no deduplication) |
| `final_result` | `AgentResult \| None` | Override result (used when `continue_run=False`) |

### 2.6 Behavior Loading Convention

A behavior module must export a `build_behavior()` factory function:

```python
# my_behavior.py
from agent_framework.agents import AgentBehavior, AgentHookDecision, AgentRun

class MyBehavior(AgentBehavior):
    def attach(self, agent) -> None:
        agent.onPreTool += self._guard_tool_call

    def before_run(self, agent, host, *, run, caller_id):
        if run.missing_parameters:
            # inject a prompt fragment to help the model find the values
            run.prompt_fragments.append(
                f"<hint>Required parameters: {run.missing_parameters}</hint>"
            )
        return None

    def _guard_tool_call(self, event):
        from agent_framework.agents import ToolHookDecision
        if "dangerous" in event.tool_name:
            return ToolHookDecision(
                continue_run=False,
                final_result=AgentResult(status="stopped", message="Tool not allowed")
            )
        return None

def build_behavior() -> AgentBehavior:
    return MyBehavior()
```

### 2.7 Behavior Path Resolution

For an agent at `agents/my_agent.md` with `behavior: my_behavior` in its sidecar JSON:

1. `agents/my_behavior.py` — agent-local (same directory as the `.md` file)
2. `behaviors/my_behavior.py` — shared library at the **parent of the agent directory** (e.g. project root when agents live under `agents/`)

Multiple behaviors are loaded in declaration order and receive lifecycle calls in that order.

### 2.8 Example: `TraceLoggingBehavior`

`src/agent_framework/trace_logging.py` provides `TraceLoggingBehavior`, the reference behavior implementation:

```python
class TraceLoggingBehavior(AgentBehavior):
    def attach(self, agent: Agent) -> None:
        agent.onPreAgent += self._on_pre_agent
        agent.onPostAgent += self._on_post_agent
        agent.onPreTool += self._on_pre_tool
        agent.onPostTool += self._on_post_tool
        agent.onPreSubagent += self._on_pre_subagent
        agent.onPostSubagent += self._on_post_subagent

    def _on_pre_agent(self, event: AgentStartEvent):
        print(f"\033[34m[AGENT START]\033[0m {event.invocation.agent_id}")

    # ... similar for all 6 hooks
```

### 2.9 `RuntimeTraceBehavior` (host-attached, not sidecar-loaded)

`RuntimeTraceBehavior` (`runtime_trace_behavior.py`) is **not** registered through an agent’s behaviors list. When **`AgentHost.runtime_tracer`** is not **`NullRuntimeTracer`**, **`run_agent`** / **`call_subagent`** clone the agent’s **`SequentialHook`** instances, append this behavior, and call **`attach()`** so structured **`runtime.*`** **`TraceEvent`** records are published. See [Host & Orchestration](./host-orchestration.md) §4.4 and [Tracing & Evaluation](./tracing-evaluation.md).

---

## 3. `SequentialHook` System

`SequentialHook` (`src/agent_framework/agents/sequential_hook.py`) is a minimal ordered callback collection:

```python
class SequentialHook:
    _callbacks: list[Callable]

    def __iadd__(self, callback: Callable) -> "SequentialHook":   # +=
    def __isub__(self, callback: Callable) -> "SequentialHook":   # -=
    def __iter__(self) -> Iterator[Callable]:
```

Callbacks are stored in order and fired in insertion order. Removing (`-=`) removes the first matching callback by identity (`is`).

### 3.1 Agent-Level Hooks (10 total)

| Hook | Event Type | When Fired | Decision Type |
|------|-----------|------------|---------------|
| `onPreAgent` | `AgentStartEvent` | Before the main loop | `AgentHookDecision \| None` |
| `onPostAgent` | `AgentEndEvent` | After a result is produced | `AgentEndHookDecision \| AgentResult \| None` |
| `onPreModel` | `ModelStartEvent` | Before each model call | `None` (observation only) |
| `onPostModel` | `ModelEndEvent` | After each model call | `None` (observation only) |
| `onPreTool` | `ToolStartEvent` | Before each tool execution | `ToolHookDecision \| None` |
| `onPostTool` | `ToolEndEvent` | After each tool execution | `None` (observation only) |
| `onPreSubagent` | `SubagentStartEvent` | Before each subagent call | `SubagentHookDecision \| None` |
| `onPostSubagent` | `SubagentEndEvent` | After each subagent call | `None` (observation only) |
| `onPreSkill` | `SkillStartEvent` | Before skill content is loaded | `None` (observation only) |
| `onPostSkill` | `SkillEndEvent` | After skill content is injected | `None` (observation only) |

### 3.2 Host-Level Hooks (2 total)

| Hook | Location | Event Type | Description |
|------|----------|-----------|-------------|
| `onPreModel` | `AgentHost` | `ModelStartEvent` | Cross-cutting pre-model interception (fires after agent-level pre-model hook) |
| `onPostModel` | `AgentHost` | `ModelEndEvent` | Cross-cutting post-model interception |

### 3.3 Hook Decision Processing

For hooks that return decisions, the runtime inspects the return value after each callback:

**`onPreAgent` / behaviors `before_run`:**
```python
for callback in self.onPreAgent:
    decision = callback(AgentStartEvent(invocation))
    if isinstance(decision, AgentHookDecision):
        if decision.final_result is not None:
            return decision.final_result   # short-circuit
        if not decision.continue_run:
            break                           # stop chain
```

**`onPreTool`:**
```python
for callback in self.onPreTool:
    decision = callback(ToolStartEvent(...))
    if isinstance(decision, ToolHookDecision):
        if decision.final_result is not None:
            return decision.final_result
        if not decision.continue_run:
            break
        if decision.updated_tool_input is not None:
            effective_input = decision.updated_tool_input
        if decision.system_message is not None:
            run.prompt_fragments.append(f"<system_message>{decision.system_message}</system_message>")
```

**`onPreSubagent`:** Same pattern as `onPreTool`, with `updated_subagent_id` and `updated_subagent_input` fields.

**`onPostAgent`:** Returns `(result, continue_run)` pair. If any callback returns `AgentEndHookDecision(continue_run=True)`, the agent loop runs again.

### 3.4 Subscribing to Hooks Programmatically

```python
host = AgentHost.from_env_console(".env")
root = host.get_root_agent()

# Log all model calls at the host level
def log_model_call(event: ModelStartEvent):
    print(f"[MODEL] agent={event.invocation.agent_id} mode={event.context.response_mode}")

host.onPreModel += log_model_call

# Guard tool calls on a specific agent
def guard_tools(event: ToolStartEvent) -> ToolHookDecision | None:
    if event.tool_name == "delete_file":
        print(f"[BLOCKED] delete_file")
        from agent_framework.agents import ToolHookDecision, AgentResult
        return ToolHookDecision(
            continue_run=False,
            final_result=AgentResult(status="stopped", message="delete_file is not allowed")
        )

root.onPreTool += guard_tools
```

---

## 4. Event Types

All events are `@dataclass(frozen=True, slots=True)` and carry `invocation: AgentInvocation` as their first field.

### `AgentInvocation` — Shared Context

```python
@dataclass(frozen=True, slots=True)
class AgentInvocation:
    run_id: str
    agent_id: str
    caller_id: str | None
    parameters: dict[str, Any]
    rendered_prompt: str
```

### Event Reference

| Event | File | Additional Fields | Fired By |
|-------|------|------------------|---------|
| `AgentStartEvent` | `agent_start_event.py` | — | `_run_pre_agent_hooks()` via `onPreAgent` |
| `AgentEndEvent` | `agent_end_event.py` | `result: AgentResult` | `_run_post_agent_hooks()` via `onPostAgent` |
| `ModelStartEvent` | `model_start_event.py` | `context: ModelContext` | `decide()` via `onPreModel` |
| `ModelEndEvent` | `model_end_event.py` | `context: ModelContext`, `response: ModelResponse` | `decide()` via `onPostModel` |
| `ToolStartEvent` | `tool_start_event.py` | `tool_call_id: str`, `tool_name: str`, `tool_input: dict`, `decision: AgentDecision` | `handle_tool_call()` via `onPreTool` |
| `ToolEndEvent` | `tool_end_event.py` | `tool_call_id: str`, `tool_name: str`, `tool_input: dict`, `result: str` | `handle_tool_call()` via `onPostTool` |
| `SubagentStartEvent` | `subagent_start_event.py` | `subagent_call_id: str`, `subagent_id: str`, `subagent_input: dict`, `decision: AgentDecision` | `handle_subagent_call()` via `onPreSubagent` |
| `SubagentEndEvent` | `subagent_end_event.py` | `subagent_call_id: str`, `subagent_id: str`, `subagent_input: dict`, `result: AgentResult` | `handle_subagent_call()` via `onPostSubagent` |
| `SkillStartEvent` | `skill_start_event.py` | `skill_name: str`, `parameters: dict` | `handle_skill_invocation()` via `onPreSkill` |
| `SkillEndEvent` | `skill_end_event.py` | `skill_name: str`, `parameters: dict`, `content: SkillContent` | `handle_skill_invocation()` via `onPostSkill` |

**`SkillContent`** (carried by `SkillEndEvent`): a dataclass with `body: str` (the full text of `SKILL.md` minus frontmatter) and `inventory: tuple[SkillResource, ...]` (file paths and metadata, not file content). Behaviors observing `onPostSkill` can inspect what was loaded and log, audit, or react accordingly.

---

## 5. Hook Decision Types

### `AgentHookDecision`

```python
@dataclass(frozen=True, slots=True)
class AgentHookDecision:
    continue_run: bool = True
    system_message: str | None = None
    final_result: AgentResult | None = None
```

Returned by `onPreAgent` callbacks and `AgentBehavior.before_run()`.

| Scenario | Fields to set |
|----------|--------------|
| Pass through | `continue_run=True` (or return `None`) |
| Inject context | `system_message="<note>Extra context</note>"` |
| Short-circuit with result | `final_result=AgentResult(...)` |
| Stop hook chain only | `continue_run=False` |

### `AgentEndHookDecision`

```python
@dataclass(frozen=True, slots=True)
class AgentEndHookDecision:
    continue_run: bool = False
    prompt_fragments: tuple[str, ...] = ()
    append_prompt_fragments: tuple[str, ...] = ()
    final_result: AgentResult | None = None
```

Returned by `onPostAgent` callbacks and `AgentBehavior.after_run()`.

| Scenario | Fields to set |
|----------|--------------|
| Accept result | `continue_run=False` (or return `None`) |
| Override result | `continue_run=False, final_result=AgentResult(...)` |
| Request another iteration | `continue_run=True` |
| Inject fragments for next iteration | `continue_run=True, prompt_fragments=(...)` |
| Append history for next iteration | `continue_run=True, append_prompt_fragments=(...)` |

### `ToolHookDecision`

```python
@dataclass(frozen=True, slots=True)
class ToolHookDecision:
    continue_run: bool = True
    updated_tool_input: dict | None = None
    system_message: str | None = None
    final_result: AgentResult | None = None
```

Returned by `onPreTool` callbacks.

| Scenario | Fields to set |
|----------|--------------|
| Allow tool call | `continue_run=True` (or return `None`) |
| Modify parameters | `updated_tool_input={...}` |
| Block with message | `continue_run=False, system_message="Reason"` |
| Block with result | `continue_run=False, final_result=AgentResult(...)` |

### `SubagentHookDecision`

```python
@dataclass(frozen=True, slots=True)
class SubagentHookDecision:
    continue_run: bool = True
    updated_subagent_id: str | None = None
    updated_subagent_input: dict | None = None
    system_message: str | None = None
    final_result: AgentResult | None = None
```

Returned by `onPreSubagent` callbacks. Same semantics as `ToolHookDecision` plus agent redirection.

| Scenario | Fields to set |
|----------|--------------|
| Allow subagent call | `continue_run=True` (or return `None`) |
| Redirect to different agent | `updated_subagent_id="other_agent"` |
| Modify parameters | `updated_subagent_input={...}` |
| Block call | `continue_run=False, final_result=AgentResult(...)` |

---

## 6. Custom Tools

### 6.1 Tool Markdown Definition

Create `{name}.md` in the tools directory:

```markdown
---
id: my_tool
description: What this tool does.
parameters:
  query:
    description: The search query
    required: true
    type: string
  limit:
    description: Maximum results
    required: false
    type: integer
    default: 10
---
# My Tool

Extended documentation about this tool, usage examples, etc.
```

**`ToolDefinition` fields** (from YAML frontmatter):

| Field | Description |
|-------|-------------|
| `tool_id` | Tool identifier (matches `id` in frontmatter) |
| `description` | Human-readable description shown to the model |
| `parameters` | `tuple[ToolParameter, ...]` |
| `source_path` | Path to the `.md` file |
| `documentation` | Body text of the markdown file |

### 6.2 Tool Python Implementation

Create `{name}.py` alongside the `.md` file:

```python
# my_tool.py
from agent_framework.tool import Tool, ToolDefinition

class MyTool(Tool):
    def invoke(self, arguments: dict, host) -> str:
        query = arguments.get("query", "")
        limit = arguments.get("limit", 10)

        # Use host.resolve_world_path() for any file access
        # Use host.call_subagent() to invoke agents
        # Return a string result

        results = perform_search(query, limit)
        return f"Found {len(results)} results: {results}"

def build_tool(definition: ToolDefinition) -> Tool:
    return MyTool(definition=definition)
```

The `build_tool(definition)` factory is the **only required export**. The definition contains all declared parameters and the documentation body.

### 6.3 `ToolParameter` Fields

```python
@dataclass(frozen=True, slots=True)
class ToolParameter:
    name: str
    description: str
    required: bool = True
    value_type: str = "string"
    default: Any = None
```

`ToolDefinition.to_model_payload()` converts to OpenAI function-call schema format:

```json
{
  "name": "my_tool",
  "description": "What this tool does.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "The search query"},
      "limit": {"type": "integer", "description": "Maximum results"}
    },
    "required": ["query"]
  }
}
```

---

## 7. Custom Model Drivers

Implement the `ModelDriver` protocol (structural typing — no inheritance needed). See [Model Abstraction](./model-abstraction.md#10-implementing-a-new-provider-driver) for the complete implementation guide and skeleton code.

The key contract points:
1. `decide(*, agent_id, provider_name, model_name, temperature, context: ModelContext) -> ModelResponse`
2. `set_trace_callbacks(*, on_request=None, on_response=None) -> None`
3. Use `assemble_system_prompt(context)` from `model.py` for prompt construction
4. Handle `context.exact_input_payload` bypass
5. Handle all three `response_mode` values: `"decision"`, `"text"`, `"json_object"`
6. Fire trace callbacks before and after the API call

Register via: `AgentHost.from_env(".env", model_driver=MyDriver(...))`

---

## 8. Skills Extension Points

### 8.1 `onPreSkill` / `onPostSkill` Hooks

Subscribe to these hooks in `attach()` to observe every skill invocation:

```python
def attach(self, agent: Agent) -> None:
    agent.onPreSkill += self._on_pre_skill
    agent.onPostSkill += self._on_post_skill

def _on_pre_skill(self, event: SkillStartEvent) -> None:
    print(f"[SKILL] {event.skill_name} params={event.parameters}")

def _on_post_skill(self, event: SkillEndEvent) -> None:
    print(f"[SKILL DONE] {event.skill_name} body_len={len(event.content.body)} "
          f"resources={len(event.content.inventory)}")
```

Both hooks are currently observation-only (return `None`). The pre-hook fires after the allowed-skills validation but before `SkillLoader` runs; the post-hook fires after skill content has been injected into `conversation_messages`.

### 8.2 Future: `SkillDriver` Protocol

The current implementation uses **prompt injection** — skill body text is inserted as a user message in `conversation_messages`. This is provider-agnostic and requires no special API support.

A planned `SkillDriver` protocol will allow native API integration where providers such as Anthropic (system prompt caching) or OpenAI (file attachments, cached context) support more efficient skill delivery. A `SkillDriver` implementation would replace the `SkillLoader` + injection step with a provider-optimized delivery mechanism while leaving the rest of the invocation flow (hooks, audit tracing, base directory injection) unchanged.

---

## 9. Custom Agents

Beyond modifying the Markdown definition, agents can be extended by:

### 9.1 Subclassing `Agent`

Override any of the template method steps:

```python
from agent_framework.agents import Agent, AgentRun, AgentDecision

class MaxIterationAgent(Agent):
    MAX_ITERATIONS = 10

    def should_continue(self, run: AgentRun) -> bool:
        iteration_count = sum(1 for h in run.history if h.startswith("before_iteration"))
        return iteration_count < self.MAX_ITERATIONS

    def complete_without_result(self, run: AgentRun):
        from agent_framework.agents import AgentResult
        return AgentResult(
            status="iteration_limit",
            message=f"Agent stopped after {self.MAX_ITERATIONS} iterations",
            prompt=run.rendered_prompt,
        )
```

### 9.2 Recording Host for Testing (`RecordingAgentHost`)

`RecordingAgentHost` (in `evaluator.py`) extends `AgentHost` to intercept and record all interactions:

```python
class RecordingAgentHost(AgentHost):
    interactions: list[RecordedInteraction]
    auto_input_response: str | None

    @classmethod
    def from_host(cls, host: AgentHost) -> "RecordingAgentHost": ...
```

Overrides: `call_subagent`, `execute_tool`, `resolve_callback`, `request_user_input` — all record a `RecordedInteraction(kind, caller_id, callee_id, payload)` before delegating to the real implementation.

`auto_input_response`: if set, `request_user_input()` returns this string instead of prompting the console — enabling fully non-interactive evaluation runs.
