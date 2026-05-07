# Agent Prompt Organization

This document describes a reusable structure for organizing `agent_framework`
agent system prompts. The goal is to make prompts easier for models to follow,
easier for humans to review, and easier for evaluators to validate.

Use this structure when an agent must produce a reliable structured decision,
call tools or callbacks, obey boundaries, or apply domain-specific parsing and
resolution rules.

## Why This Structure

Agent prompts often degrade when they become a mixed list of instructions,
schemas, warnings, and examples. Models can then latch onto the easiest valid
output shape, miss a routing rule, or apply a later edge-case example before
the general workflow.

This structure separates concerns:

- responsibilities define what the agent owns;
- boundaries define what the agent must not do;
- workflow defines the order of reasoning and routing decisions;
- output shape defines the exact runtime contract;
- specific rules and examples define domain behavior after the model
  understands its general job.

The most important idea is that decision routing should appear before the
schema examples that could distract the model. For example, if ambiguity must
produce a callback, the workflow should say that before the prompt shows the
normal `final_message` payload.

## Recommended Section Order

### 1. Responsibilities

State what the agent is and what work it owns. Keep this section short and
positive. It should answer: "What is this agent responsible for producing?"

Include:

- the agent's role in one or two sentences;
- the primary output or decision it owns;
- the main inputs it uses;
- the kind of work that should not be pushed to deterministic code.

Avoid:

- detailed schema fields;
- long edge-case rules;
- examples.

Example:

```markdown
## Responsibilities

You are an internal intent parser. Convert one player message into structured
`DeclaredIntent` records for downstream adjudication.

You are responsible for:

- identifying the player's intended action or actions;
- resolving visible/player-authorized targets and references;
- asking for clarification through a framework callback when a fair parse would
  require guessing.
```

### 2. Boundaries

Define hard limits and safety rules. These rules should be unambiguous and
should use framework terms when possible.

Include:

- what the agent must not infer, reveal, mutate, or decide;
- when the agent must use callback instead of plain text;
- fields or legacy behaviors that are forbidden;
- scope boundaries between this agent and other agents or deterministic code.

Avoid:

- soft guidance such as "try to";
- mixing exception-heavy domain rules into this section;
- long examples.

Example:

```markdown
## Boundaries

- Capture intent only. Do not decide success, failure, damage, detection, or
  state changes.
- Use only the supplied player-visible state and retrieved authorized context.
- Do not ask questions in plain text. Clarification must be a top-level
  `callback` decision with `intent: "information_request"` and the question in
  `message`.
- Do not return deprecated fields such as `parameters.clarifying_question`.
```

### 3. Workflow

Describe the sequence the model should follow to produce the output. This is
the main performance section: it turns scattered rules into an ordered decision
tree.

Include:

- the first routing checks the agent must perform;
- the order of resolution steps;
- when to callback, block, call a tool, or return a normal result;
- any decomposition or planning steps;
- the final emission step.

Place ambiguity, callback, and blocked/ready routing here before showing output
schemas. This helps prevent the model from choosing a normal result shape when
a callback is required.

Example:

```markdown
## Workflow

1. Read the player message and split it into action clauses.
2. If a required noun, target, destination, or tool is generic or missing,
   return a `callback` clarification.
3. Resolve each target against visible ids and reference markers.
4. Resolve additional involved entities as `metadata.references`.
5. For unresolved named items, decide whether visible or retrieved context makes
   the item plausible. If plausible, create a virtual entity; otherwise block.
6. Decompose sequential and conditional actions into separate intents.
7. Emit exactly one framework decision object.
```

### 4. Output Shape

Define the exact framework decision contract and the agent-specific payload
schema. Keep this section mechanical and copy-ready.

Include:

- allowed top-level framework decisions;
- the normal success or blocked payload;
- the callback payload, if callbacks are allowed;
- nested schema objects;
- id conventions and deprecated fields;
- formatting constraints, such as "JSON only" and "no markdown fences."

Avoid:

- explaining all domain rules here;
- embedding many examples that compete with the schema;
- using different field names in examples than in the schema.

Example:

````markdown
## Output Shape

For ready or blocked parser results, return:

```json
{
  "kind": "final_message",
  "message": "",
  "parameters": {
    "status": "ready | blocked",
    "declared_intents": [],
    "assumptions": []
  }
}
```

For clarification, return:

```json
{
  "kind": "callback",
  "intent": "information_request",
  "message": "What specific item do you want to throw into the cave?",
  "parameters": {
    "reason": "The object to throw is ambiguous."
  }
}
```
````

When ids can refer to canonical and virtual entities, prefer one universal id
field:

```markdown
Id rules:

- Positive hierarchical ids such as `"1"`, `"1.1"`, and `"6"` refer to
  canonical visible world entities.
- Negative hierarchical ids such as `"-1"` and `"-1.1"` refer to parser-created
  virtual entities.
- Do not emit separate `entity_id`, `virtual_id`, or `handle` fields unless the
  application truly distinguishes those concepts.
```

### 5. Specific Rules And Examples

Put domain-specific behavior after the general contract. This lets the model
first learn the job and output shape, then apply specialized rules.

Organize this section by topic. Each topic should explain one recurring problem
and include small examples.

Useful topic types:

- target vs. references;
- known item resolution;
- unknown or unavailable item handling;
- ambiguity and callback routing;
- tool usage;
- virtual or temporary entities;
- multi-action decomposition;
- conditional/reaction behavior;
- hidden-state or privacy constraints;
- negative examples for common mistakes.

Examples should be aligned with the schema from the Output Shape section. Avoid
examples that use obsolete fields or alternate shapes unless they are explicitly
marked as wrong.

Example:

````markdown
### Ambiguity And Clarification

- Generic placeholders such as "something", "somewhere", or "one of these
  things" are ambiguity when the action requires that missing choice.
- Do not choose a default object for the player.
- Do not return `blocked` for ambiguity if the player could answer the missing
  choice.

Correct:

```json
{
  "kind": "callback",
  "intent": "information_request",
  "message": "What specific item do you want to throw into the cave?",
  "parameters": {
    "reason": "The player said 'something', which does not identify the thrown object."
  }
}
```

Wrong:

```json
{
  "kind": "final_message",
  "message": "",
  "parameters": {
    "status": "blocked",
    "declared_intents": [],
    "assumptions": []
  }
}
```
````

## Copy-Ready Template

````markdown
## Responsibilities

[State the agent role and the concrete output it owns.]

You are responsible for:

- [responsibility 1]
- [responsibility 2]
- [responsibility 3]

## Boundaries

- [Hard limit: what the agent must not infer, reveal, mutate, or decide.]
- [Scope limit: what belongs to other agents or deterministic code.]
- [Framework limit: when to use callback/tool/subagent/final_message.]
- [Deprecated fields or invalid behaviors.]

## Workflow

1. [First routing or safety check.]
2. [Resolution or retrieval step.]
3. [Decision/decomposition step.]
4. [Callback/block/ready routing.]
5. [Final emission step.]

## Output Shape

[Define allowed framework decision envelopes.]

```json
{
  "kind": "final_message",
  "message": "",
  "parameters": {}
}
```

```json
{
  "kind": "callback",
  "intent": "information_request",
  "message": "Question for the caller.",
  "parameters": {}
}
```

[Define agent-specific payload schema.]

```json
{
  "field": "shape"
}
```

## Specific Rules And Examples

### [Topic 1]

[Rule explanation.]

Correct:

```json
{}
```

Wrong:

```json
{}
```

### [Topic 2]

[Rule explanation.]
````

## Review Checklist

Before using a prompt organized this way, check:

- Responsibilities are short and do not contain schema details.
- Boundaries use clear "do not" and "must" statements for hard constraints.
- Workflow routes callback/block/ready before the normal output schema.
- Output Shape uses exactly the same field names as examples.
- Deprecated fields are listed once in the Output Shape section.
- Specific examples are grouped by topic and do not contradict the workflow.
- Negative examples target common real failures.
- The prompt does not rely on deterministic code to infer semantic intent that
  the agent was supposed to produce.
