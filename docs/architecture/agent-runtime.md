# Agent Runtime Specification

> This document is part of the `agent_framework` architecture reference.
> See also: [Overview](./overview.md) · [Model Abstraction](./model-abstraction.md) · [Host & Orchestration](./host-orchestration.md) · [Extension Points](./extension-points.md) · [Interface Specifications](./interfaces.md)

---

## 1. Overview

The `Agent` class (`src/agent_framework/agents/agent.py`, ~1289 lines) is the heart of the framework. It implements the complete decision loop, prompt composition, parameter system, hook integration, and behavior loading. Each agent instance corresponds to a Markdown definition file and executes as a stateless class — all per-invocation mutable state is isolated in an `AgentRun` object.

The run loop follows the **Template Method** pattern: `Agent.run()` is the final orchestration method that calls a sequence of overridable step methods. Subclasses and behaviors extend specific steps without modifying the loop itself.

---

## 2. Agent Definition Format

### 2.1 Markdown File Structure

Each agent is defined in a Markdown file with exactly three sections separated by `---` delimiters:

```markdown
---
id: my_agent
role: My Agent Role
description: What this agent does for callers.
parameters:
  instruction:
    description: The task instruction
    required: true
    type: string
tools:
  - search_tool
subagents:
  - summarizer_agent
---
You are an agent that [system prompt instructions here].
Never invent information.
---
Please {{ instruction }} using the available tools.
```

The `split_markdown_sections()` function in `helpers.py` splits on `^---\s*$` (multiline regex). Exactly 4 resulting sections are required (3 delimiters): frontmatter YAML, system prompt, and user prompt template.

### 2.2 YAML Frontmatter Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `str` | yes | Stable agent identifier. Used in registry, `allowed_child_agents` lists, subagent calls. |
| `role` | `str` | yes | Human-readable role label (e.g., `"Search Agent"`). |
| `description` | `str` | no | Caller-facing summary. Exposed as `CapabilityDefinition.description` to parent agents. |
| `parameters` | mapping | no | Declared parameter specifications (see Section 2.3). |
| `tools` | list/dict | no | Allowed tool names. Formats: list of strings, list of `{name: str}` dicts, or dict with name keys. Parsed by `parse_allowed_tool_names()`. |
| `subagents` | list | no | Allowed child agent IDs. |
| `allowed_skills` | list | no | Allowed skill names. An empty value (omitted or empty list) means all discovered skills are available. A non-empty list restricts invocation to the named skills only. |

### 2.2.1 Skill Frontmatter Fields

Each SKILL.md file can include additional frontmatter fields recognized by the skill loader:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `priority` | `int` | `0` | Controls catalog ordering when `build_skills_catalog()` must truncate to fit `SKILLS_CATALOG_MAX_TOKENS`. Lower-priority skills are dropped first. Higher values are preserved preferentially. |

### 2.3 Parameter Declarations

Each entry under `parameters:` declares an `AgentParameter`:

```yaml
parameters:
  instruction:
    description: "The main task instruction"
    required: true         # default: true
    type: string           # string | integer | number | boolean | object | array
    default: null          # optional default value
  # schema_path can be set programmatically via AgentParameter.schema_path
```

**`AgentParameter` fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | — | Parameter name. Must match `{{ name }}` placeholders in the template. |
| `description` | `str` | — | Human-readable description. Shown to parent agents as capability parameter description. |
| `required` | `bool` | `True` | Whether the parameter must be present for the agent to run. |
| `value_type` | `str` | `"string"` | Type for coercion: `string`, `integer`, `number`, `boolean`, `object`, `array`. |
| `default` | `Any` | `None` | Value used when not present in invocation or extractable from prompt. |
| `schema_path` | `Path \| None` | `None` | Optional JSON Schema file for deep validation (resolved relative to agent's parent directory). |

### 2.4 User Prompt Template

The user prompt template uses `{{ parameter_name }}` (Mustache-style double-brace) placeholders:

```
Please {{ instruction }} in the context of {{ setting }}.
```

The regex `PLACEHOLDER_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")` matches placeholders. `_validate_template_contract()` is called during `from_markdown()` and verifies every `{{ }}` name corresponds to a declared parameter — failures are caught at load time.

At runtime, `render_user_prompt(parameter_values)` performs the substitution. Unresolved placeholders (where the parameter value is missing) are left as `{{ name }}` so that `extract_prompt_value()` can detect them later.

### 2.5 Sidecar JSON Runtime Metadata

An optional `<agent_filename>.json` file next to the `.md` file holds runtime configuration. Loaded by `load_runtime_metadata(source_path)` in `helpers.py`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | `str` | `config.default_provider` | LLM provider name (e.g., `"openai"`). |
| `model` | `str` | `config.default_model` | Model ID (e.g., `"gpt-4o-mini"`). |
| `temperature` | `float` | `0.2` | Sampling temperature. |
| `can_query_caller` | `bool` | `True` | Whether this agent can escalate callbacks to its caller. |
| `can_use_host_interaction` | `bool` | `True` | Whether this agent can use console I/O for callbacks. |
| `callback_policy` | `object` | `{}` | Default routing policy for caller-mediated callbacks. Keys: `passthrough_child_callbacks`, `max_bubble_hops`, `fallback_target`. |
| `behavior` | `str` | — | Single behavior module name (alternative to `behaviors` list). |
| `behaviors` | `list[str]` | `[]` | List of behavior module names to load and attach. |
| `workflow-compose` | `object` | — | Prompt projection metadata used by `PromptRef("agent:<id>#workflow")`. Supports `pre-load-skills`, `include-sections`, `exclude-sections`, and `append`. |

A `model_override` passed to `from_markdown()` (from `HostConfig.model_for(agent_id)`) takes precedence over the sidecar's `model` field.

Example:

```json
{
  "model": "gpt-4.1,gpt-4o-mini",
  "can_query_caller": true,
  "can_use_host_interaction": true,
  "callback_policy": {
    "passthrough_child_callbacks": false,
    "max_bubble_hops": 2,
    "fallback_target": "user"
  }
}
```

`workflow-compose` lets a workflow phase reuse a projected standalone agent
prompt through `PromptRef("agent:axis_audience#workflow")`. The default agent
prompt resolver loads the referenced agent markdown and sidecar, parses
markdown headings from the standalone agent system prompt, includes configured
section subtrees in source order, applies excludes afterward, and appends any
workflow adapter text. Full heading paths such as `/Agent/Rubric` disambiguate
duplicate titles; shorthand titles fail when ambiguous. Resolution emits
`prompt_reference_resolved` audit metadata containing the source agent,
projection, included and excluded sections, preloaded skills, and a token
estimate.

---

## 3. The Run Loop — Template Method Pattern

`Agent.run()` is the final orchestration method. It must not be overridden — override the step methods instead.

### 3.1 Invocation Signature

```python
def run(
    self,
    *,
    host: AgentHostProtocol,
    parameters: dict[str, Any] | None = None,
    caller_id: str | None = None,
    rendered_prompt_override: str | None = None,
    conversation_messages: list[dict] | None = None,
    prompt_fragments: list[str] | None = None,
) -> AgentResult
```

| Parameter | Description |
|-----------|-------------|
| `host` | The `AgentHostProtocol` implementation — provides model driver, agent registry, tool execution, callback resolution. |
| `parameters` | Seed parameter values, validated against declared specs. `None` means empty dict. |
| `caller_id` | The invoking agent's ID for callback routing. `None` for root agent calls. |
| `rendered_prompt_override` | Bypass template rendering — use this pre-rendered string as the user prompt base. Used when a parent agent has already composed the prompt. |
| `conversation_messages` | Initial conversation history (list of `{"role": ..., "content": ...}` dicts). Enables continuation from a prior conversation. |
| `prompt_fragments` | Initial prompt fragments (XML-tagged strings). Used when the host wants to inject context before the first iteration. |

### 3.2 Run Loop — Complete Flow

```
run(host, parameters, caller_id, ...)
│
├─ 1. _create_run(parameters, rendered_prompt_override, conversation_messages, prompt_fragments)
│      → AgentRun(run_id=uuid4, rendered_prompt, seed_parameters, ...)
│      Note: render_user_prompt() OR rendered_prompt_override
│
├─ 2. refresh_parameter_state(run)
│      For each declared parameter:
│        try: extract_prompt_value(spec, rendered_prompt + fragments)  [XML tag extraction]
│        fallback: seed_parameters[name]
│        fallback: spec.default
│      → run.parameter_values, run.missing_parameters, run.invalid_parameters
│
├─ 3. [audit tracer] start_agent_call(run_id, caller_id, agent_id, system_prompt, ...)
│
├─ 4. _run_pre_agent_hooks(run, caller_id)
│      a. For each behavior:
│           decision = behavior.before_run(agent, host, run=run, caller_id=caller_id)
│           → AgentHookDecision.final_result → RETURN IMMEDIATELY
│           → AgentHookDecision.continue_run=False → stop behavior loop
│      b. onPreAgent callbacks (fires AgentStartEvent):
│           → AgentHookDecision.final_result → RETURN IMMEDIATELY
│
└─ 5. MAIN LOOP: while self.should_continue(run):
       │
       ├─ before_iteration(run)
       │    - run.history.append("before_iteration:{agent_id}")
       │    - refresh_parameter_state(run)
       │
       ├─ runtime_decision = self.resolve_runtime_decision(run)  [returns None by default]
       │
       ├─ If runtime_decision is None:
       │    context = self.build_context(host, run)       [Section 4]
       │    decision = self.decide(host, run, context)    [Section 5]
       │  Else:
       │    decision = runtime_decision
       │
       ├─ result = self.dispatch_decision(host, run, decision, caller_id)  [Section 6]
       │
       ├─ after_iteration(run)
       │    - run.history.append("after_iteration:{agent_id}")
       │
       └─ If result is not None:
            (result, continue_run) = _run_post_agent_hooks(run, caller_id, result)
            If continue_run: loop again
            Else: RETURN result

       If loop exits without result:
         result = complete_without_result(run)  → AgentResult(status="stopped")

[finally] audit_tracer.finish_agent_call(run_id=run.run_id)
```

### 3.3 Overridable Step Methods

| Method | Default Behavior | Override Purpose |
|--------|-----------------|-----------------|
| `should_continue(run)` | `return True` | Add iteration limit, cost guard, or convergence check. |
| `before_iteration(run)` | Appends to history; refreshes parameter state. | Add per-iteration setup. |
| `after_iteration(run)` | Appends to history. | Add per-iteration teardown. |
| `resolve_runtime_decision(run)` | `return None` | Inject a synthetic decision before calling the model (e.g., for testing or deterministic routing). |
| `build_context(host, run)` | Full prompt assembly (see Section 4). | Customize prompt construction. |
| `decide(host, run, context)` | Calls model driver, fires pre/post hooks, normalizes decision. | Override model interaction. |
| `dispatch_decision(host, run, decision, caller_id)` | Routes by `decision.kind` to handler. | Add cross-cutting decision processing. |
| `complete_without_result(run)` | `AgentResult(status="stopped", message="")` | Customize the fallback result. |

---

## 4. `build_context()` — Prompt Assembly

```python
def build_context(self, host: AgentHostProtocol, run: AgentRun) -> ModelContext
```

Assembles the complete model context for one model call. Steps:

**1. System prompt:**
```python
system_prompt = apply_runtime_placeholders(self.system_prompt, run.placeholder_values)
```
`apply_runtime_placeholders()` substitutes single-brace `{name}` patterns using `run.placeholder_values`. The assembled `ModelContext` is then passed through `merge_runtime_system_into_messages()` in `model.py`, which appends shared runtime instructions (`system.md` + response-mode template via `ModelDriverBase`) into the first system message so provider drivers receive a fully merged `messages` list.

**2. Tool definitions:**
For each name in `self.allowed_tools`:
```python
tool_def = host.get_tool(name).model_definition()  # returns ToolDefinition
```

**3. Subagent definitions:**
For each id in `self.allowed_child_agents`:
```python
agent = host.get_agent(id, base_dir=self.source_path.parent if self.source_path else None)
subagent_def = agent_to_capability_definition(agent)  # helpers.py
```

**4. User prompt with augmentations:**
```python
user_prompt = self.render_user_prompt(run.parameter_values)
if run.prompt_fragments:
    user_prompt += "\n<augmentations>\n" + "\n".join(run.prompt_fragments) + "\n</augmentations>"
```

**5. Returns:**
```python
ModelContext(
    system_prompt=system_prompt,   # assembled further in model.py
    user_prompt=user_prompt,
    messages=tuple(run.conversation_messages),
    response_mode="json_object",   # hardcoded; model layer selects actual mode
    tools=tuple(tool_definitions),
    subagents=tuple(subagent_defs),
    skills=tuple(
        CapabilityDefinition(capability_id=d.name, description=d.description, priority=d.priority)
        for d in skill_defs
    ),
    run_id=run.run_id,
)
```

---

## 5. `decide()` — Model Invocation

```python
def decide(self, host: AgentHostProtocol, run: AgentRun, context: ModelContext) -> AgentDecision
```

Steps:

1. Build `AgentInvocation` payload (for events): `run_id`, `agent_id`, `caller_id`, `parameter_values`, `rendered_prompt`.

2. **Pre-model hooks:**
   - Fire `ModelStartEvent(invocation, context)` via `onPreModel` SequentialHook callbacks.
   - Call `host.run_pre_model_hooks(event)` — host-level pre-model hook.

3. **Model call:**
   ```python
   driver = host.get_model_driver(self)
   response = driver.decide(
       agent_id=self.agent_id,
       provider_name=self.provider_name,
       model_name=self.model_name,
       temperature=self.temperature,
       context=context,
   )
   ```

4. **Audit recording:** If `host.audit_tracer` is set, calls `audit_tracer.record_llm_response(run_id=run.run_id, raw_text=response.raw_text, parsed_payload=response.payload)`.

5. **Post-model hooks:**
   - Fire `ModelEndEvent(invocation, context, response)` via `onPostModel` SequentialHook callbacks.
   - Call `host.run_post_model_hooks(event)` — host-level post-model hook.

6. **Conversation history update:** Appends `{"role": "assistant", "content": response.raw_text}` to `run.conversation_messages`.

7. **Decision normalization:** `return AgentDecision.from_model_response(response)`.

---

## 6. `dispatch_decision()` — Decision Routing

```python
def dispatch_decision(
    self, host: AgentHostProtocol, run: AgentRun,
    decision: AgentDecision, caller_id: str | None
) -> AgentResult | None
```

**Pre-processing:** `decision = self._normalize_decision_capabilities(decision)` — repairs slot confusion (see Section 7.2).

Routes by `decision.kind`:

### 6.1 `"final_message"` → `handle_final_message()`

```python
return AgentResult(
    status="completed",
    message=decision.message,
    decision=decision,
    prompt=run.rendered_prompt,
)
```

Also appends `{"role": "assistant", "content": decision.message}` to `run.conversation_messages` and updates `run.transcript_entries`.

### 6.2 Interaction decisions → `handle_callback()`

The runtime now treats callback-style interaction as a family of distinct routing modes, all handled through `handle_callback()`:

| `decision.kind` | Meaning |
|-----------------|---------|
| `"callback"` | Generic callback. Used mainly for intent-style kinds normalized from `information_request`, `proposal_review`, etc. |
| `"callback_to_caller"` | Ask the caller side to try resolving first. |
| `"request_user_input"` | Open direct host/user interaction for the requesting run. |
| `"request_resolution"` | Resolve through agents/tools/memory only. No host/user fallback. |

Common steps:

1. Open context: `ctx = host.open_context(caller_id=self.agent_id, callee_id=caller_id or "host", kind=f"callback:{decision.callback_intent}")`
2. Record audit/tracing events for the callback request.
3. Merge any per-agent and per-decision routing policy:
   - sidecar `callback_policy`
   - `can_query_caller`
   - `can_use_host_interaction`
   - decision parameters such as `bubble_hops`, `max_bubble_hops`, `fallback_target`, `passthrough_agents`, `resolvable_by`
4. Resolve according to `decision.kind`:
   ```python
   if decision.kind == "request_user_input":
       response = host.request_user_input(...)
   elif routes_to_caller and not passthrough and not reached_hop_limit:
       response = host.resolve_callback(...)
   elif decision.kind == "request_resolution":
       return AgentResult(status="failed", ...)
   elif self.can_use_host_interaction:
       response = host.request_user_input(...)
   else:
       response = ""
   ```
5. Set `ctx.status = "resolved"` when a response is obtained.
6. Append response:
   - To `run.conversation_messages`: `{"role": "user", "content": response}`
   - To `run.prompt_fragments`: `<callback_response intent="{intent}">{response}</callback_response>` (via `_upsert_prompt_fragment`)
7. `return None` — loop continues after the answer is injected.

Important behavior:

- Sequential sub-agents may ask the user directly.
- Parallel batch children still must not synchronously block on direct user interaction.
- Caller bubbling can skip orchestration layers via host-side run lineage plus `passthrough_agents` / `resolvable_by`.
- `request_resolution` fails instead of silently falling through to host interaction.

### 6.3 `"call_tool"` → `handle_tool_call()`

1. Validate: `decision.tool_name` must be in `self.allowed_tools`. Raises `ValueError` if not.
2. **Pre-tool hooks:** Fire `ToolStartEvent(invocation, tool_call_id, tool_name, tool_input, decision)` via `onPreTool` callbacks.
   - `ToolHookDecision.final_result` → return early with that result.
   - `ToolHookDecision.continue_run=False` → break hook chain.
   - `ToolHookDecision.updated_tool_input` → use modified parameters for the call.
   - `ToolHookDecision.system_message` → append as `<system_message>` fragment.
3. Call tool: `result = host.execute_tool(decision.tool_name, effective_parameters)`
4. **Post-tool hooks:** Fire `ToolEndEvent(invocation, tool_call_id, tool_name, tool_input, result)` via `onPostTool` callbacks.
5. Append: `self._upsert_prompt_fragment(run, f'<tool_result tool="{tool_name}">{result}</tool_result>')`
6. `return None` — loop continues.

### 6.4 `"call_subagent"` → `handle_subagent_call()`

1. Validate: `decision.subagent_id` must be in `self.allowed_child_agents`. Raises `ValueError` if not.
2. **Pre-subagent hooks:** Fire `SubagentStartEvent(invocation, subagent_call_id, subagent_id, subagent_input, decision)` via `onPreSubagent` callbacks.
   - `SubagentHookDecision.updated_subagent_id` → redirect to a different agent.
   - `SubagentHookDecision.updated_subagent_input` → modify the parameters.
   - `SubagentHookDecision.final_result` → return early.
   - `SubagentHookDecision.system_message` → append as `<system_message>` fragment.
3. Call subagent: `result = host.call_subagent(caller=self, callee_id=effective_subagent_id, parameters=effective_parameters)`
4. **Post-subagent hooks:** Fire `SubagentEndEvent(invocation, subagent_call_id, subagent_id, subagent_input, result)` via `onPostSubagent` callbacks.
5. Append: `self._upsert_prompt_fragment(run, f'<subagent_result subagent="{subagent_id}">{result.message}</subagent_result>')`
6. `return None` — loop continues.

### 6.5 `"invoke_skill"` → `handle_skill_invocation()`

1. **Resolve definition:** Looks up `decision.skill_name` in the `SkillRegistry` via `host.get_skill_registry()`.
2. **Validate allowed:** If the agent has a non-empty `allowed_skills` list, `decision.skill_name` must be in it. Raises `ValueError` if not.
3. **Pre-skill hooks:** Fire `SkillStartEvent(invocation, skill_name, parameters)` via `onPreSkill` callbacks.
4. **Load content:** `SkillLoader` loads the skill body and builds the file inventory (list of resource paths, not content).
5. **Inject skill fragment:** `handle_skill_invocation()` injects the skill content into `run.conversation_messages` as a `{"role": "user", "content": ...}` message wrapped in a `<skill_invocation_result>` XML element. This is the **only** injection point — skill content never enters `system_prompt` or `prompt_fragments`. The injected message has the form:

   ```
   <skill_invocation_result name="<skill-name>">
   <body text>
   Base directory: /abs/path/to/skill/dir

   <skill_files>
   - relative/file.md
   </skill_files>
   </skill_invocation_result>
   ```
6. **Audit trace:** Records a `SkillInvocationRecord` on the current `AgentCallAuditRecord`.
7. **Post-skill hooks:** Fire `SkillEndEvent(invocation, skill_name, parameters, content)` via `onPostSkill` callbacks.
8. `return None` — loop continues.

**No tool cleanup required:** The `Agent.run()` `finally` block does not perform any skill-related tool cleanup. There is no dynamically-registered resource tool to unregister — resource files are made accessible to the model via the injected base directory path, not through a tool.

**`onPreSkill` / `onPostSkill` hooks** are `SequentialHook` instances on `Agent`, consistent with the existing pre/post hook pattern for tools and subagents.

---

## 7. Decision Normalization

### 7.1 `AgentDecision.from_model_response(response: ModelResponse) -> AgentDecision`

Normalizes raw model output into a structured `AgentDecision`. Called from `decide()`.

**No `kind` key in payload:**
→ `AgentDecision(kind="final_message", message=response.raw_text)`

**Legacy kind normalization** (minimal compatibility):

| Raw `kind` | Normalized `kind` | `callback_intent` |
|------------|------------------|--------------------|
| `"request_parameter"` | `"request_user_input"` | `"information_request"` |

The explicit interaction kinds are preserved:

- `"callback_to_caller"` stays `"callback_to_caller"`
- `"request_user_input"` stays `"request_user_input"`
- `"request_resolution"` stays `"request_resolution"`

**Callback intent kind normalization:**

| Raw `kind` | Normalized `kind` | `callback_intent` |
|------------|------------------|--------------------|
| `"information_request"` | `"callback"` | `"information_request"` |
| `"proposal_review"` | `"callback"` | `"proposal_review"` |
| `"execution_recovery"` | `"callback"` | `"execution_recovery"` |
| `"delegation_return"` | `"callback"` | `"delegation_return"` |
| `"policy_or_approval"` | `"callback"` | `"policy_or_approval"` |
| `"guardrail_trip"` | `"callback"` | `"guardrail_trip"` |

### 7.2 `_normalize_decision_capabilities(decision) -> AgentDecision`

Repairs common model confusion patterns where tool names and subagent IDs land in the wrong slot. The repair logic applies to the callback family (`callback`, `callback_to_caller`, `request_user_input`, `request_resolution`) when those kinds accidentally carry a tool name or child agent id instead of a real interaction request.

| Case | Detection | Repair |
|------|-----------|--------|
| 1 | `kind="callback"`, `subagent_id` is in `allowed_child_agents` | Reclassify as `call_subagent` |
| 2 | `kind="callback"`, `tool_name` is in `allowed_tools` | Reclassify as `call_tool` |
| 3 | `kind="call_tool"`, `tool_name=None`, `subagent_id` is in `allowed_child_agents` | Reclassify as `call_subagent` |
| 4 | `kind="call_subagent"`, `subagent_id=None`, `tool_name` is in `allowed_tools` | Reclassify as `call_tool` |
| 5 | `kind="call_subagent"`, `subagent_id` is actually a tool name | Swap: `call_tool` |
| 6 | `kind="call_tool"`, `tool_name` is actually a subagent ID | Swap: `call_subagent` |

---

## 8. The Parameter System

### 8.1 Parameter Extraction from Prompt

`refresh_parameter_state(run)` extracts parameter values from the combined prompt text on every iteration. The extraction source is `_prompt_for_parameter_extraction(run)` = `run.rendered_prompt` + all joined `run.prompt_fragments`.

`extract_prompt_value(spec, prompt)` (in `helpers.py`):
- Builds regex: `<{spec.name}>(.*?)</{spec.name}>` (with `re.DOTALL`)
- **Last match wins** — the most recent fragment's value takes precedence
- Skips unresolved placeholders (values that still contain `{{ }}`)
- **Special heuristic extractors:**
  - `difficulty_class`: matches patterns like "DC 15", "Difficulty Class 20", "DC-15"
  - `skill_name`: matches patterns like "make a Perception check", "an Athletics roll", "using Stealth"

### 8.2 Type Coercion

`coerce_parameter_value(spec, raw_value)` coerces extracted string values to declared types:

| `value_type` | Coercion |
|-------------|----------|
| `"string"` | If starts with `{` or `[`: attempt `json.loads()`; else `str()` |
| `"integer"` | `int()` |
| `"number"` | `float()` |
| `"boolean"` | `"true"/"yes"/"1"` → `True`; `"false"/"no"/"0"` → `False` |
| `"object"` | `json.loads()` → `dict` |
| `"array"` | `json.loads()` → `list` |

### 8.3 JSON Schema Validation

If `AgentParameter.schema_path` is set, `_validate_parameter_value(spec, value)` calls `jsonschema.validate(value, schema)`. Schema paths are resolved relative to `source_path.parent.parent` by `resolve_schema_path()`. Validation failures are recorded in `run.invalid_parameters[name] = error_message` — they do not raise exceptions, allowing the agent to proceed with a callback to request a valid value.

### 8.4 Parameter Resolution Priority

1. XML tag extraction from latest prompt fragments (highest priority — most recent fragment wins)
2. `run.seed_parameters[name]` (from the invocation `parameters` dict)
3. `AgentParameter.default` (from the declaration)
4. `None` → recorded in `run.missing_parameters` (triggers `information_request` callback if required)

---

## 9. Prompt Fragment Management

### 9.1 The `prompt_fragments` List

`AgentRun.prompt_fragments: list[str]` accumulates XML-tagged content across loop iterations. Before each model call, all fragments are joined and wrapped:

```
<augmentations>
<tool_result tool="search_tool">Results here...</tool_result>
<subagent_result subagent="summarizer">Summary...</subagent_result>
<callback_response intent="information_request">User's answer</callback_response>
</augmentations>
```

This augmentations block is appended to the user prompt before passing to the model.

Workflow-local LLM phases use a different default. `WorkflowModelStep` appends
the initial rendered prompt once, then appends each phase prompt to
`conversation_messages` as a user turn and projects phase results back into the
same shared conversation. It does not add phase prompts or results to
`<augmentations>` unless `prompt_fragment_mode` opts into
`prompt_fragment_only` or `both`.
Phase prompts are durable by default for compatibility. A step can set
`prompt_history_policy="ephemeral"` to keep the active phase prompt visible for
that call, then remove it from LLM-visible `conversation_messages` and
`prompt_fragments` after the phase result projection is appended. The
transcript and audit stream still preserve the removed prompt.
For workflows that contain a default chat-history `WorkflowModelStep`,
deterministic workflow outputs and action-loop results from transform steps,
tools, subagents, callback answers, and skill invocations follow the same rule:
they are appended after the existing conversation prefix and are not inserted
through `run.prompt_fragments`. This preserves provider prompt-cache stability
because each later model call has the previous call's messages as an exact
prefix.

### 9.2 Upsert Semantics

`_upsert_prompt_fragment(run, fragment)` applies replace-by-tag-name semantics:

1. `_fragment_tag_name(fragment)` extracts the leading XML tag name via regex.
2. If an existing fragment with the same tag name is found: **replace** it (last-write wins per tag).
3. If no match: **append** to the list.

This prevents context bloat: multiple calls to the same tool always produce a single `<tool_result tool="name">` fragment containing the most recent result.

### 9.3 Fragment Control from Behaviors

`AgentEndHookDecision` (returned by `AgentBehavior.after_run()`) has two fragment fields:

- `prompt_fragments: tuple[str, ...]` — each fragment is upserted (replace-by-tag-name).
- `append_prompt_fragments: tuple[str, ...]` — each fragment is **always appended** (no deduplication). Used for accumulating history, rounds, or sequential log entries.

---

## 10. Behavior Loading

`_attach_behaviors(source_path)` is called during `from_markdown()`:

```python
for behavior_id in self.behavior_ids:
    path = self._resolve_behavior_path(behavior_id)
    spec = importlib.util.spec_from_file_location(behavior_id, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    behavior = module.build_behavior()   # required export
    behavior.attach(self)                # behavior subscribes to hooks
    self.behaviors = (*self.behaviors, behavior)
```

**`_resolve_behavior_path(behavior_id) -> Path`** resolution order:
1. Agent-local: `source_path.parent / f"{behavior_id}.py"` — behavior is colocated with the agent
2. Shared: `source_path.parent / "behaviors" / f"{behavior_id}.py"` — shared behavior library

The `build_behavior()` factory function is the **only required export** from a behavior module. It returns an `AgentBehavior` instance. See [Extension Points](./extension-points.md) for the full behavior contract.

---

## 11. Agent Loading (`Agent.from_markdown()`)

```python
@classmethod
def from_markdown(
    cls,
    path: Path,
    *,
    default_provider: str,
    default_model: str,
    model_override: str | None = None,
) -> "Agent"
```

Complete loading sequence:

1. Read file content.
2. `split_markdown_sections(content)` → `(frontmatter_yaml, system_prompt, user_prompt_template)`.
3. `yaml.safe_load(frontmatter_yaml)` → extract `id`, `role`, `description`, `parameters`, `tools`, `subagents`, `skills`.
4. `parse_allowed_tool_names(raw_tools)` — normalize list/dict formats.
5. `load_runtime_metadata(path)` — load sidecar `.json` if present.
6. Apply `model_override` if provided (from `HostConfig.agent_models`).
7. Parse `AgentParameter` instances for each declared parameter.
8. `_validate_template_contract()` — verify all `{{ name }}` placeholders in the template correspond to declared parameters. Raises `ValueError` on mismatch.
9. `_attach_behaviors(path)` — load and attach behavior modules.
10. Construct and return `Agent(...)` dataclass.
