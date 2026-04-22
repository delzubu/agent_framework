# agent_framework — Agent Usage Reference

Use this reference when building or modifying an agent for `agent_framework`, especially from coding assistants such as Claude Code, Cursor, GitHub Copilot, or Codex CLI.

---

## Choose the implementation surface

| Need | Use |
|---|---|
| Change instructions, parameters, routing, tools, subagents, or output contract | Agent `.md` only |
| Change runtime metadata such as model/provider/temperature or attach behaviors | Adjacent agent `.json` |
| Add deterministic validation, prompt augmentation, memory preload, sanitization, or verification loops | Python `AgentBehavior` |
| Add new host/tool/storage/runtime capabilities | Python framework code |

Rule of thumb:

- keep agent intent in Markdown
- keep runtime knobs in the adjacent `.json`
- put deterministic logic in Python, not in the prompt

---

## Agent file layout

An agent is primarily a Markdown file with three regions:

```markdown
---
id: deck_reviewer
role: Slide deck reviewer
description: Reviews slide decks for clarity and structure.
parameters:
  instruction:
    type: string
    required: true
tools:
  - Read
subagents:
  - slide_layout_reviewer
terminal_tools: []
response_mode: decision
---
You are a slide deck reviewer.
...
---
{{instruction}}
```

The runtime expects:

- frontmatter
- system prompt
- user prompt template

The file must use the normal `---` section delimiters described in `references/framework-usage.md`.

---

## When `.md` alone is enough

Use only the agent Markdown file when all of the following are true:

- behavior is fully prompt-driven
- inputs already arrive in the right shape
- no deterministic preprocessing is needed
- no post-run repair, validation, or retry loop is needed
- no host-side I/O or memory preload is needed

Good fits:

- router agents
- summarizers
- extractors whose contract is enforced only through prompt + response schema
- subagents that consume already-normalized parameters

Typical changes that belong in `.md`:

- parameter schema
- tool allow-list
- subagent allow-list
- skills allow-list
- `terminal_tools`
- `response_mode`
- system prompt workflow and examples
- user prompt template

---

## When to add the adjacent `.json`

The runtime also loads an adjacent sidecar JSON file with the same stem as the Markdown file:

```text
agents/deck_reviewer.md
agents/deck_reviewer.json
```

Use the `.json` when you need runtime metadata without changing framework code.

Current supported keys:

| Key | Effect |
|---|---|
| `model` | Override model list for this agent |
| `provider` | Override provider for this agent |
| `temperature` | Override sampling temperature |
| `behavior` | Attach one behavior id |
| `behaviors` | Attach multiple behavior ids in order |
| `can_query_caller` | Allow/disallow callbacks to the caller agent |
| `can_use_host_interaction` | Allow/disallow direct host/user callbacks |

Example:

```json
{
  "model": "gpt-4.1,gpt-4o-mini",
  "provider": "openai",
  "temperature": 0.1,
  "behaviors": [
    "deck_review.input_guard",
    "deck_review.output_verifier"
  ],
  "can_query_caller": true,
  "can_use_host_interaction": true
}
```

Use `.json` when:

- one agent needs a different model than the repo default
- a deterministic behavior must be attached
- a child agent should not directly query the host/user

Do not put these in `.json`:

- agent parameter definitions
- tools
- subagents
- skills
- system prompt text
- user prompt template

Those belong in the `.md`.

---

## When Python is required

Add Python code when the behavior must be deterministic rather than prompt-only.

Common cases:

- validate inputs before the first model call
- normalize or enrich parameters
- preload memory and replace large payloads with refs
- call other runtime services before or after the run
- sanitize or validate the final output
- trigger a verification loop and rerun the model with feedback

The main extension seam is `AgentBehavior` in `src/agent_framework/agents/agent_behavior.py`.

Lifecycle:

- `before_run(...) -> AgentHookDecision | None`
- `respond_to_callback(...) -> str | None`
- `after_run(...) -> AgentEndHookDecision | AgentResult | None`

---

## Pre-run behavior patterns

### 1. Validate inputs before spending tokens

Use `before_run` when inputs can be checked deterministically.

Examples:

- required fields missing from a structured payload
- invalid enum / malformed identifier
- unsupported file type
- deck JSON lacks slides after preprocessing

Good outcomes:

- stop early with a deterministic `final_result`
- add a `system_message` fragment and continue so the agent asks for clarification on the first model step

### 2. Parse and extend input

Use `before_run` to derive extra structured state from incoming parameters.

Examples:

- expand shorthand IDs into canonical objects
- parse a deck manifest and inject normalized counts / metadata
- derive helper prompt fragments from validated input

If the behavior mutates prompt state or seed input, call `agent.refresh_parameter_state(run)` so parameter extraction and validation are recalculated.

### 3. Preload memory

This is the clean place to:

- store large payloads in memory
- replace inline parameters with `*_ref` values
- inject a small `<system_message>` fragment that explains what was preloaded

Use pre-run logic for parameters only. Do not move prompt text into memory.

---

## Post-run behavior patterns

### 1. Sanitize the result

Use `after_run` to clean or normalize deterministic parts of the result.

Examples:

- strip forbidden wrapper text
- normalize field casing or ids
- move large returned artifacts into memory and replace them with refs
- reject outputs that violate a strict machine-readable contract

If sanitization can be done without another model call, return a replacement `AgentResult`.

### 2. Enforce a stricter output contract

Use `after_run` when the model is allowed to try once, but deterministic code is the final authority.

Examples:

- output must be valid JSON with specific keys
- output must omit prohibited fields
- output must fit a product-specific schema not fully expressible in the base prompt

If invalid:

- replace the result directly, or
- request one more loop with feedback

### 3. Trigger a verification loop

`after_run` can request another model iteration by returning:

```python
AgentEndHookDecision(
    continue_run=True,
    prompt_fragments=(
        "<verification_feedback>Missing evidence for slide 4 claim.</verification_feedback>",
    ),
)
```

This is the right pattern when you want:

- deterministic validation followed by one corrective rerun
- a verifier subagent to review the draft result
- a post-processor to feed explicit feedback back into the same agent run

Possible flow:

1. main agent produces draft output
2. `after_run` validates it deterministically or calls a verifier subagent
3. if validation fails, `after_run` returns `AgentEndHookDecision(continue_run=True, ...)`
4. the agent runs one more loop with the feedback fragment in its augmentations

This is preferable to silent repair because the model sees the failure reason and must produce a new valid decision/result.

---

## Callback handling

Use `respond_to_callback` when a child agent may ask its parent for clarification and the answer can be provided deterministically.

Good fits:

- resolve an identifier from parent-owned context
- answer a child question from already-known state
- deny or rewrite a child request before it reaches the human

Return:

- a string to answer the callback
- `None` to let it escalate normally

Use the `.json` flags to control whether an agent may query its caller or the host directly:

- `can_query_caller`
- `can_use_host_interaction`

---

## Use-case suggestions

### Input guard

Use `.md` + `.json` + `before_run` when the agent should refuse low-quality input quickly.

Examples:

- reject a review request if no deck payload or `deck_ref` is present
- require a locale or house style before a publishing agent runs

### Memory-backed review agent

Use `before_run` when the incoming request contains a large deck/document payload.

Pattern:

- validate payload
- store it in memory
- replace it with `deck_ref`
- let the prompt and subagents operate on the ref

### Output normalization agent

Use `after_run` when downstream systems need strict shape.

Pattern:

- validate result structure
- normalize deterministic fields
- move oversized artifacts to memory
- return a cleaned `AgentResult`

### Verification loop agent

Use `after_run` with `continue_run=True` when quality matters more than single-pass latency.

Pattern:

- run the main agent once
- validate the result, or ask a verifier subagent to critique it
- rerun the same agent with precise feedback fragments

This works well for:

- deck reviews
- policy-heavy extractors
- synthesis agents that must cite evidence

---

## Recommended companion references

Load these alongside this file as needed:

- `references/framework-usage.md` for full runtime contracts
- `references/agent-prompt-patterns.md` before editing a system prompt
- `references/memory-usage.md` when the agent handles large/shared payloads
- `references/evaluator-usage.md` when adding cases or regression tests
