# agent_framework — Agent Design Guide

> **Status:** Placeholder. The sections below are TODOs. Load `framework-usage.md` for the full technical reference.

---

## TODO: Single-agent vs multi-agent

- When does a single agent with tools suffice?
- When should work be delegated to sub-agents?
- Decision criteria: context isolation, parallelism, specialization

---

## TODO: Decomposing a task into agents

- Identifying agent boundaries
- Passing context through parameters (not conversation history)
- Avoiding tight coupling between agents
- How to use `call_subagent` vs `call_subagents` (sequential vs parallel)

---

## TODO: Callback design

- When to use `callback` vs a `terminal_tool`
- Designing callback intents for clean escalation paths
- Implementing `respond_to_callback` in `AgentBehavior`
- Parent/child communication patterns

---

## TODO: Tool design

- When to build a custom tool vs. using built-in tools
- Permission-gating: which operations require `request_permission`
- Returning structured output from tools
- Tool granularity (one action per tool vs. composite)

---

## TODO: Prompt engineering for the decision loop

- Writing system prompts that produce consistent JSON decisions
- Guiding the model toward `final_message` at the right time
- Preventing runaway loops
- Structured output via `response_mode: json_object`

---

## TODO: State management

- Agents are stateless between invocations
- Passing state via parameters and `output_key` in `call_subagents`
- Using the `before_run` hook to inject context
- World directory for file-based state

---

## TODO: Error handling and recovery

- Using `execution_recovery` callback intent
- Designing retry policies at the agent level
- `MISSING_TOOL_POLICY=graceful` vs `strict`

---

## TODO: Testing agents

- Writing evaluator case files
- Choosing meaningful `evaluation_criteria`
- Using `no_callbacks` mode for automated runs
- Balancing LLM evaluators vs code evaluators

---

## TODO: Performance and cost

- Minimising context size passed to sub-agents
- Choosing models per agent (`AGENT_MODELS` env var)
- Parallel sub-agents with `call_subagents mode=parallel`
- `SUBAGENT_MAX_PARALLELISM` and `SUBAGENT_BATCH_TIMEOUT_SECONDS`
