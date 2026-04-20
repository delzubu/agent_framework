---
title: Agent
layout: default
sdk_page: true
---


# `Agent`

Module: [`agent_framework.agents.agent`](../agent.html)

## API Summary

```python
class Agent
```

Markdown-defined runnable agent.

Attributes:
    agent_id: Stable runtime identifier for the agent.
    role: Human-readable role name.
    description: Caller-facing summary of what the agent does.
    system_prompt: Stable instruction block loaded from the Markdown file.
    user_prompt_template: Template rendered with invocation parameters.
    parameters: Declared invocation contract loaded from frontmatter.
    provider_name: Model provider selected for this agent.
    model_names: Ordered model list for this agent (first = highest priority).
    temperature: Sampling temperature passed to the model driver.
    allowed_tools: Tool names this agent may call.
    allowed_child_agents: Child agent ids this agent may invoke.
    allowed_skills: Future capability ids this agent may reference.
    can_query_caller: Whether the agent may request information from its
        caller at runtime.
    can_use_host_interaction: Whether the agent may ask the host for user
        input at runtime.
    on_pre_agent: Sequential callbacks executed before the agent run starts.
    on_post_agent: Sequential callbacks executed after the agent run ends.
    on_pre_tool: Sequential callbacks executed before a tool call.
    on_post_tool: Sequential callbacks executed after a tool call.
    on_pre_subagent: Sequential callbacks executed before a child-agent call.
    on_post_subagent: Sequential callbacks executed after a child-agent call.
    on_pre_skill: Sequential callbacks executed before a skill invocation.
    on_post_skill: Sequential callbacks executed after a skill invocation.
    behavior_ids: Optional ordered runtime behavior ids resolved from sidecar JSON.
    source_path: Source Markdown path used to load the agent definition.

## Attributes

- `agent_id`
- `allowed_child_agents`
- `allowed_skills`
- `allowed_tools`
- `behavior_ids`
- `behaviors`
- `can_query_caller`
- `can_use_host_interaction`
- `description`
- `model_names`
- `on_post_agent`
- `on_post_model`
- `on_post_skill`
- `on_post_subagent`
- `on_post_tool`
- `on_pre_agent`
- `on_pre_model`
- `on_pre_skill`
- `on_pre_subagent`
- `on_pre_tool`
- `parameters`
- `parameters_injection`
- `provider_name`
- `role`
- `source_path`
- `system_prompt`
- `temperature`
- `terminal_tools`
- `user_prompt_template`

## Methods

### `from_markdown`

```python
def from_markdown(cls, path: str | Path, *, default_provider: str, default_model: tuple[str, ...], model_override: tuple[str, ...] | None = None) -> 'Agent'
```

Load an agent definition from the Markdown file format.

### `get_parameter_spec`

```python
def get_parameter_spec(self) -> tuple[AgentParameter, ...]
```

Expose the declared invocation contract for callers and tests.

### `validate_parameters`

```python
def validate_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]
```

Validate and normalize invocation parameters against the contract.

### `render_user_prompt`

```python
def render_user_prompt(self, parameters: dict[str, Any]) -> str
```

Render the user prompt template using validated parameters.

### `try_parse_prompt_input`

```python
def try_parse_prompt_input(self, prompt: str) -> dict[str, Any] | None
```

Try to recover declared parameter values from a composed prompt string.

This is primarily used by the root host path so a user can pass the
exact prompt text they want to send to the model while still allowing
the runtime to validate extracted structured values.

### `run`

```python
def run(self, *, host: 'AgentHostProtocol', parameters: dict[str, Any] | None = None, caller_id: str | None = None, parent_run_id: str | None = None, rendered_prompt_override: str | None = None, conversation_messages: tuple[dict[str, str], ...] | None = None, prompt_fragments: tuple[str, ...] | None = None, run_id: str | None = None, in_parallel_batch: bool = False) -> AgentResult
```

Execute the agent loop for one invocation.

Args:
    host: Runtime host supplying model access, I/O, tool calls, and
        subagent resolution.
    parameters: Optional structured seed parameters. These are helper
        values only; the prompt remains the authoritative invocation
        contract for extraction and validation.
    caller_id: Optional caller identifier used for callback requests.
    parent_run_id: When this run is a subagent, the parent agent's ``run_id``
        (used for trace/UI nesting; omit for root invocations).

Returns:
    An `AgentResult` describing the completed invocation.

### `should_continue`

```python
def should_continue(self, run: AgentRun) -> bool
```

Return whether another loop iteration should execute.

### `before_iteration`

```python
def before_iteration(self, run: AgentRun) -> None
```

Hook executed before each model decision step.

### `resolve_runtime_decision`

```python
def resolve_runtime_decision(self, *, run: AgentRun) -> AgentDecision | None
```

Return an internal runtime decision before consulting the model.

### `build_context`

```python
def build_context(self, *, host: 'AgentHostProtocol', run: AgentRun) -> ModelContext
```

Assemble the provider-facing model context for the current run.

### `decide`

```python
def decide(self, *, host: 'AgentHostProtocol', run: AgentRun, context: ModelContext) -> AgentDecision
```

Request and normalize the next decision from the configured model.

### `dispatch_decision`

```python
def dispatch_decision(self, *, host: 'AgentHostProtocol', run: AgentRun, decision: AgentDecision, caller_id: str | None) -> AgentResult | None
```

Dispatch a normalized decision to the appropriate handler.

### `after_iteration`

```python
def after_iteration(self, run: AgentRun) -> None
```

Hook executed after each loop iteration.

### `complete_without_result`

```python
def complete_without_result(self, run: AgentRun) -> AgentResult
```

Produce a fallback result if the loop exits without a final message.

### `handle_final_message`

```python
def handle_final_message(self, *, host: 'AgentHostProtocol', run: AgentRun, decision: AgentDecision, caller_id: str | None) -> AgentResult
```

Return a completed result for a `final_message` decision.

### `handle_callback`

```python
def handle_callback(self, *, host: 'AgentHostProtocol', run: AgentRun, decision: AgentDecision, caller_id: str | None) -> AgentResult | None
```

Handle all callback-style requests through one unified transport.

### `handle_subagent_call`

```python
def handle_subagent_call(self, *, host: 'AgentHostProtocol', run: AgentRun, decision: AgentDecision, caller_id: str | None) -> AgentResult | None
```

Handle a child-agent call and merge its result into this run.

### `handle_subagent_calls`

```python
def handle_subagent_calls(self, *, host: 'AgentHostProtocol', run: AgentRun, decision: AgentDecision, caller_id: str | None) -> AgentResult | None
```

Handle a call_subagents batch decision (parallel or sequential).

### `handle_skill_invocation`

```python
def handle_skill_invocation(self, *, host: 'AgentHostProtocol', run: AgentRun, decision: AgentDecision, caller_id: str | None) -> AgentResult | None
```

Load and inject skill content into the conversation, then continue the loop.

### `handle_tool_call`

```python
def handle_tool_call(self, *, host: 'AgentHostProtocol', run: AgentRun, decision: AgentDecision, caller_id: str | None) -> AgentResult | None
```

Handle a tool call and append the tool output as an augmentation.

### `refresh_parameter_state`

```python
def refresh_parameter_state(self, run: AgentRun) -> None
```

Extract and validate parameter values from the current prompt state.

### `respond_to_callback`

```python
def respond_to_callback(self, host: 'AgentHostProtocol', *, callee_id: str, prompt: str) -> str | None
```

Return an agent-specific callback response if any behavior provides one.
