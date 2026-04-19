# Agentic System-Prompt Design — Research Report

A survey of prompt-design patterns across major agentic frameworks, distilled for use with `agent_framework`. Focus: **system instructions that drive tool-use, decision, and delegation loops** — not general LLM prompt engineering.

---

## 1. Introduction

### What is an "agentic" system prompt?

A system prompt becomes *agentic* when it governs a **loop** rather than a single response. The model is expected to:
1. Observe state (conversation history, tool results, memory)
2. Decide what to do next (invoke a tool, delegate, ask a question, or stop)
3. Act and observe the result
4. Repeat until a terminal condition is reached

The system prompt is the only persistent instruction across all iterations of that loop. Everything the agent knows about its role, available tools, output format, and exit conditions must be derivable from the system prompt (plus any injected context).

### Why prompts carry so much weight in this runtime

In `agent_framework`, each agent is a `.md` file. The system prompt template controls the decision format (`system.decision.md` for typed JSON decisions, `system.text.md` for prose, `system.json_object.md` for structured output with callback patterns). There is no Python class to override; the prompt *is* the agent. Mistakes in prompt design produce silent failures: model output that looks plausible but violates the contract (wrong `kind`, missing fields, heuristic repair — explicitly prohibited by `CLAUDE.md`).

---

## 2. Cross-Framework Landscape

### 2.1 Anthropic (Claude)

**Sources:** [Claude Prompting Best Practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), [Writing Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents), [Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use), [Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills), [Claude Code Best Practices](https://code.claude.com/docs/en/best-practices)

**Core philosophy:** Clarity first, strict output contracts, no heuristic repair. Claude 4.x models calibrate response length to perceived task complexity; prompts that need a specific verbosity must state it explicitly.

**System-prompt design guidance:**
- Use XML tags (`<context>`, `<instructions>`, `<examples>`) to partition sections — Claude has been trained to attend to these boundaries
- State the *role and objective* in the opening sentences, before any tool descriptions
- For long tool libraries: do not enumerate all tools in the system prompt — use a **Tool Search tool** that discovers tools on demand, reducing token usage by ~85% while keeping access to the full library ([Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use))
- **Programmatic tool orchestration**: Claude can write code that calls tools and controls what enters context, avoiding intermediate result bloat
- **Tool Use Examples** in the system prompt (few-shot demonstrations of parameter combinations) improves accuracy from 72% to 90% on complex parameter structures

**Tool description patterns** ([Writing Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents)):
- Name tools with namespace prefixes (e.g., `asana_projects_search` not `search`) — namespacing has "non-trivial effects" on performance
- Use unambiguous parameter names (`user_id` not `user`)
- Return semantically meaningful identifiers (human-readable names, not raw UUIDs) — this "significantly improves Claude's precision in retrieval tasks"
- Provide actionable error messages with examples, not opaque codes
- Use `ResponseFormat` enum parameters (e.g., `"detailed"` vs `"concise"`) to let agents control context efficiency

**Agent Skills architecture** ([Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)):
Three-tier progressive disclosure:
1. Skill metadata (name + description) is injected into the system prompt at startup — the model uses this to recognise when a skill applies
2. Full `SKILL.md` content loads only when the skill is triggered
3. Referenced files (`references/`, `assets/`) load on-demand within the skill session

This mirrors a well-organised manual: table of contents → chapter → appendix. The model never pays context cost for information it does not need.

**Agentic safety patterns** (from Claude Code system prompt analysis, [Piebald-AI/claude-code-system-prompts](https://github.com/Piebald-AI/claude-code-system-prompts)):
- Position the agent as a *collaborative partner*, not an autonomous executor — defers to user judgement for scope questions
- Tool hierarchy in the prompt: preferred tools listed first, general-purpose fallbacks mentioned only for specific gaps
- Sub-agents receive **self-contained prompts** (Explore, Plan, Verify) — each gets exactly the context it needs and nothing more
- Guardrails use **positive framing with boundary conditions**: "avoid introducing security vulnerabilities" rather than an exhaustive deny-list

### 2.2 OpenAI

**Sources:** [Agents SDK](https://openai.github.io/openai-agents-python/agents/), [Practical Guide to Building Agents (PDF)](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf), [Prompt Engineering](https://developers.openai.com/api/docs/guides/prompt-engineering), [Prompt Guidance](https://developers.openai.com/api/docs/guides/prompt-guidance)

**SDK instructions model:** The `instructions` field in the Agents SDK is the system prompt. It can be a static string or a **dynamic callback** `(context, agent) -> str` that generates personalised instructions at invocation time (e.g., injecting the user's name or session state). This is the OpenAI equivalent of Anthropic's skills catalog injection.

**Recommended prompt skeleton** (from OpenAI SDK docs):
```
1. Role & Objective — who the agent is and what success looks like
2. Personality & Tone — voice and communication style
3. Context — retrieved information, relevant background
4. Tools — names, usage rules, preambles, when not to call
5. Instructions / Rules — do's, don'ts, decision approach
6. Conversation Flow — states, goals, transitions
7. Safety & Escalation — fallback behaviour and handoff logic
```

**Handoff design:** Delegation cues belong in the instructions themselves: *"If the user asks about booking, hand off to the booking agent."* The agent uses these instructions to decide when specialist agents should take over. Guardrails run **in parallel** to the agent (not sequentially), checking both input and output against constraints.

**Transparency guidance:** *"Before you call a tool, explain why you are calling it"* — making the reasoning visible at decision points. This prevents silent failures and makes traces debuggable.

**Structured output / decision format:** Use explicit output format constraints: *"Only output a single word with no additional formatting or commentary."* For tool-calling agents, reinforce with: *"Decompose the user query into required sub-requests and confirm each is completed."*

### 2.3 Google (Vertex AI Agent Builder / ADK)

**Sources:** [Agentic AI Overview](https://docs.cloud.google.com/architecture/agentic-ai-overview), [ADK Development](https://docs.cloud.google.com/agent-builder/agent-engine/develop/adk), [Agent Builder Overview](https://docs.cloud.google.com/agent-builder/overview), [LangGraph + Gemini Example](https://ai.google.dev/gemini-api/docs/langgraph-example)

**ADK instruction style:** System instructions follow the pattern:
> *"You are a [role name], designed to [primary purpose]. [Operational modes: action, retrieval, clarity, brevity constraints]."*

Example: *"You are a Vehicle Voice Agent, designed to assist users with information and in-vehicle actions."* Followed by specific directives: respond under 30 words, prefer action over information when both are possible.

**Memory architecture:**
- **PreloadMemoryTool**: agent decides *when* to retrieve memories and how they're incorporated into context
- Post-interaction memory generation via `async_add_session_to_memory`
- Session IDs for conversation continuity across turns
- Local vs. cloud-managed sessions with automatic synchronisation

**Agent taxonomy (7 agentic patterns identified by Google):**
1. Knowledge Assessment Agents — personalised learning, comprehension evaluation
2. Data Science Agents — multi-agent analytics and ML task automation
3. Classification Agents — multimodal data categorisation
4. Streaming Guidance Agents — real-time technical workflow guidance with safety monitoring
5. Knowledge Graph Agents — fragmented data consolidation
6. Enterprise System Orchestrators — cross-system coordination
7. Security Operations Agents — investigation and triage automation

**A2A (Agent-to-Agent) protocol:** Agents built on different frameworks can interoperate — dynamic capability discovery, cross-framework negotiation without system reconstruction.

### 2.4 LangChain / LangGraph

**Sources:** [Planning Agents](https://www.langchain.com/blog/planning-agents), [ReAct Agent Template](https://github.com/langchain-ai/react-agent), [LangChain Agents Docs](https://docs.langchain.com/oss/python/langchain/agents), [ReAct vs Plan-and-Execute Comparison](https://dev.to/jamesli/react-vs-plan-and-execute-a-practical-comparison-of-llm-agent-patterns-4gh9), [LangGraph + Gemini](https://ai.google.dev/gemini-api/docs/langgraph-example)

**ReAct system prompt template** (LangChain/LlamaIndex canonical form):
```
You are designed to help with a variety of tasks. You have access to a wide variety of
tools. You are responsible for using the tools in any sequence you deem appropriate to
complete the task at hand. This may require breaking the task into subtasks and using
different tools to complete each subtask.

## Tools
{tool_names_and_descriptions}

## Output Format
Thought: reason about what to do
Action: [tool_name]
Action Input: {"key": "value"}
Observation: [tool result]
... (repeat as needed)
Thought: I know the final answer
Final Answer: [answer]
```

**Plan-and-Execute prompt separation:**

*Planner prompt*: Instructs the model to generate a numbered multi-step plan for the whole task before any tool execution. *"Create a plan with the following format: 1. First step / 2. Second step..."*

*Executor prompt*: Receives the full plan plus the current step. Uses smaller, domain-specific models for sub-steps — the large model is called only for planning and re-planning.

*Re-planning trigger*: After execution of each step, a re-planning call decides whether the plan needs updating based on observed results.

**Advanced variants:**
- **ReWOO**: Variable assignment (`#E2 = tool_result`) in the plan to reduce context passed to re-planning calls
- **LLMCompiler**: DAG scheduling for parallel tool calls — claimed 3.6× speedup

**Empirical comparison** (from [dev.to analysis](https://dev.to/jamesli/react-vs-plan-and-execute-a-practical-comparison-of-llm-agent-patterns-4gh9)):

| Metric | ReAct | Plan-and-Execute |
|--------|-------|-----------------|
| Accuracy | 85% | 92% |
| Token usage | 2,000–3,000 | 3,000–4,500 |
| Cost per task | $0.06–0.09 | $0.09–0.14 |
| Response speed | Faster | Slower |

### 2.5 CrewAI

**Sources:** [CrewAI Agents Docs](https://docs.crewai.com/en/concepts/agents), [DigitalOcean CrewAI Guide](https://www.digitalocean.com/community/tutorials/crewai-crash-course-role-based-agent-orchestration)

**Core agent definition** — three required fields that compose into the system prompt at runtime:
- **`role`**: One-sentence job title and function ("Senior Python Developer", "Market Research Analyst")
- **`goal`**: Single, unambiguous objective. Keeping goals singular is a documented best practice — conflicting objectives reduce accuracy
- **`backstory`**: Domain expertise and persona grounding. Missing backstory = agents take unpredictable actions

**Prompt composition architecture:**
```python
system_template  = "You are {role}. {backstory}"
prompt_template  = "Your goal is: {goal}\nTask: {task_description}"
response_template = "{response}"
```
Templates are customisable; both `system_template` and `prompt_template` must be defined together for the composition to work.

**Tool invocation split:** `function_calling_llm` allows using a cheaper model just for tool calls, reserving the main LLM for reasoning. This is an explicit cost-efficiency pattern.

**Resource controls built into agent definition:**
- `max_iter` (default 20): Maximum reasoning cycles before a final answer is forced
- `respect_context_window` (default True): Automatic summarisation at window boundary
- `max_execution_time`: Hard timeout
- `allow_delegation`: Enables assigning tasks to other agents in the crew

**Crew orchestration pattern:** Roles in sequence — **Planner → Researcher → Critic** is a documented robust chain. Each agent has a clearly differentiated role; using the same prompt template across agents defeats the purpose of role specialisation.

### 2.6 AutoGen

**Sources:** [AutoGen Use Cases](https://microsoft.github.io/autogen/0.2/docs/Use-Cases/agent_chat/), [Conversation Patterns](https://microsoft.github.io/autogen/0.2/docs/tutorial/conversation-patterns/), [AutoGen Architecture Review](https://mgx.dev/insights/autogen-a-comprehensive-review-of-microsofts-multi-agent-conversational-framework-for-llms/8a620b4813ac4155a9f3868e954ebb11)

**ConversableAgent system_message pattern:**
Every agent's role is defined through a `system_message` parameter. Examples from documentation:
- Student: *"You are a student willing to learn"*
- Teacher: *"You are a math teacher"*
- Planner: role description + instruction to decompose tasks before delegation
- Critic: role description + instruction to evaluate and provide structured feedback on other agents' output

Unlike CrewAI's template system, AutoGen's system_message is a free-form string — structure is the developer's responsibility.

**Four conversation patterns:**

1. **Two-agent chat**: Simplest. One agent calls `initiate_chat()`. Summary method is configurable (`last_msg` or `reflection_with_llm`).

2. **Sequential chat**: Chains of two-agent chats. Each chat's summary is injected as *carryover context* into the next chat's initiating message — not into system prompts. This keeps individual agent roles stable while accumulating session state.

3. **Group chat**: `GroupChatManager` orchestrates N agents. Speaker selection strategies:
   - `round_robin` — deterministic, good for pipelines
   - `auto` (default) — LLM selects next speaker from agent names/descriptions
   - `manual` — human selects
   - `random` — stochastic exploration

4. **Nested chat**: Packages a whole workflow into a single agent. A trigger function (checking sender identity) activates nested sequential chats, returning a unified response. Prevents recursive loops.

**StateFlow pattern:** `allowed_or_disallowed_speaker_transitions` dictionary implements FSM-like control — each agent maps to the set of agents that can follow it. Enables deterministic pipelines within the AutoGen multi-agent model.

**human_input_mode:** `NEVER` (fully autonomous), `ALWAYS` (every step requires human input), with intermediate modes. This is the explicit human-in-the-loop control knob.

**AutoGen v0.4 (2025):** Re-designed architecture with modular components for memory, custom agents, and built-in debugging/monitoring support.

### 2.7 LlamaIndex

**Sources:** [ReActAgent Docs](https://developers.llamaindex.ai/python/examples/agent/react_agent/), [LlamaIndex Agents](https://docs.llamaindex.ai/en/stable/module_guides/deploying/agents/)

LlamaIndex implements the canonical ReAct prompt and allows custom system prompts to be appended on top. The default ReAct system prompt:

```
You are designed to help with a variety of tasks, from answering questions
to providing summaries to other types of analyses.

You have access to a wide variety of tools. You are responsible for using
the tools in any sequence you deem appropriate to complete the task at hand.

## Output Format
Thought: I need to use a tool to help me answer the question.
Action: tool name (one of {tool_names}) if using a tool.
Action Input: the input to the tool, in a JSON format representing the kwargs
  (e.g. {"input": "hello world", "num_beams": 5})

Please ALWAYS start with a Thought.
Please use a valid JSON format for the Action Input.
```

The `Thought:` prefix before every action is enforced — this is the ReAct pattern's key mechanism for making reasoning explicit and observable.

Separate `CodeActAgent` type uses a different prompting strategy (code execution as the action mechanism rather than structured JSON).

---

## 3. Core Agentic Patterns

### 3.1 ReAct (Reasoning and Acting)

**Origin:** [ReAct paper — arxiv.org/abs/2210.03629](https://arxiv.org/abs/2210.03629)

The fundamental agentic loop: **Thought → Action → Observation**, repeated until a final answer.

```
Thought: [reasoning about what to do and why]
Action: [tool name]
Action Input: [tool arguments as JSON]
Observation: [tool result]
... (repeat)
Thought: I have enough information to answer.
Final Answer: [response]
```

**Why it works:** Interleaving reasoning (`Thought`) with action forces the model to articulate its plan before acting, making errors visible in the trace. The `Observation` step injects tool results back into context before the next reasoning step.

**Prompt enablement:** The system prompt must:
1. List available tools with names, descriptions, and input schemas
2. Define the exact `Thought/Action/Action Input/Observation` output format
3. Specify the terminal condition (`Final Answer:` or equivalent)
4. Include a few-shot example of the full loop

**Trade-off:** One LLM call per tool invocation. For tasks requiring many tools, cost and latency scale linearly.

### 3.2 Plan-and-Execute

**Origin:** LangChain blog, [ReWOO](https://arxiv.org/abs/2305.18323), [LLMCompiler](https://arxiv.org/abs/2312.04511)

Separates the *planning LLM* (large, capable) from the *execution LLM* (smaller, cheaper). The planner sees the whole task upfront and produces a numbered plan. Executors handle individual steps.

**Planner prompt essence:**
```
You have access to the following tools: {tool_list}
Create a step-by-step plan to complete the following task.
Be explicit: name the tool to call and the argument for each step.
Format:
1. [Step description] — Tool: [tool_name], Input: [value]
2. ...
```

**Executor prompt essence:**
```
You are completing step {N} of a plan.
Plan: {full_plan}
Previous results: {prior_step_outputs}
Your task: {current_step}
Available tool: {tool_for_this_step}
```

**Re-planning:** After execution, a lightweight check: *"Has the plan been completed, or do we need to update it given the observed results?"* Re-planning is triggered only when prior steps produced unexpected outputs.

**When to choose Plan-and-Execute over ReAct:**
- Multiple tools needed (>3 tool calls expected)
- Task quality benefits from upfront decomposition (e.g., research pipelines)
- Cost reduction is important (sub-steps use smaller models)
- Tasks have predictable structure (e.g., ETL, report generation)

### 3.3 Reflexion / Self-Critique

**Origin:** [Reflexion paper — arxiv.org/abs/2303.11366](https://arxiv.org/abs/2303.11366), [DeepLearning.AI Reflection Pattern](https://www.deeplearning.ai/the-batch/agentic-design-patterns-part-2-reflection/)

Three components:
- **Actor**: Generates outputs (can use ReAct or Plan-and-Execute internally)
- **Evaluator**: Scores output quality — can be a second LLM, rule-based function, or external tool (unit tests, web search)
- **Self-Reflection**: Generates verbal feedback explaining *what went wrong and how to fix it*. Stored in long-term memory and injected into the next iteration

**Self-reflection prompt template:**
```
You previously attempted the following task and received this feedback:

Task: {task}
Your attempt: {previous_output}
Evaluation: {evaluator_feedback}

Reflect on what went wrong. Be specific about the error and what you would
do differently. Store your reflection as a short paragraph.
```

**Single-agent vs multi-agent:**
- Single-agent: the same model criticises its own output
- Multi-agent: a separate Critic agent is instantiated with a different role prompt, providing independent evaluation

**Verbal vs scalar feedback:** Reflexion's key contribution is *verbal* feedback (explaining the error) vs. scalar rewards (traditional RL). Verbal feedback is more specific, interpretable, and generalises better to novel error types.

**Performance:** Proven effective on sequential decision-making (AlfWorld), multi-step reasoning (HotPotQA), and code generation (HumanEval, MBPP).

### 3.4 Router / Supervisor + Worker Agents

**Used by:** CrewAI, AutoGen (GroupChatManager), OpenAI Agents SDK (handoffs)

A **Supervisor** agent reads incoming requests and delegates to specialised **Worker** agents. The supervisor does not execute tools directly; it only routes.

**Supervisor system prompt pattern:**
```
You are a triage agent. Your job is to understand the user's request and
decide which specialist should handle it.

Available specialists:
- booking_agent: handles reservations, cancellations, itinerary queries
- support_agent: handles complaints, refunds, account issues
- info_agent: handles general questions about products and services

Rules:
- Route to exactly one specialist per request
- If the request is ambiguous, ask one clarifying question before routing
- Never answer directly — always delegate
```

**Worker system prompt pattern:**
```
You are a [specialist role]. You handle [specific domain].
You receive delegated requests from a supervisor agent.
When you are done, summarise your result clearly for the supervisor.
```

**In `agent_framework`:** The supervisor pattern maps to a root agent using `call_subagent` or `call_subagents`. The router-style prompt on the root agent is exactly what makes the routing decision.

### 3.5 Tool Search / Progressive Disclosure

**Used by:** Anthropic (2026), `agent_framework` via Skills + MCP

When the tool library is large (>10 K tokens of tool descriptions), injecting all tools into context degrades performance and wastes tokens. The solution: a **Tool Search tool** that takes a natural-language query and returns the most relevant N tools.

**System prompt with tool search:**
```
You have access to a large library of tools. Before calling any tool,
search for it using the `tool_search(query)` function to retrieve its
definition. Do not assume tool names or parameters — always search first.

Available always: tool_search(query: str) -> list[ToolDefinition]
```

This reduces context usage by ~85% while maintaining access to the full library ([Anthropic Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use)).

**Skills catalog** (Anthropic pattern): Instead of tool search, skills are short name+description entries injected at startup. The model selects which skill to load via `invoke_skill`. The skill's full content is only loaded when selected.

### 3.6 Skill Invocation / Dynamic Instructions

**Used by:** Anthropic (Skills), OpenAI (dynamic instructions callback)

Rather than loading all expertise into the system prompt, agents maintain a **catalog of skills** and load them on demand. This separates:
- *What the agent can do* (catalog, always loaded, minimal tokens)
- *How to do it* (skill content, loaded only when relevant)

**`agent_framework` implementation:** Skills are `.md` files in a configured directory. A truncated catalog (names + descriptions) is injected as a conversation message. When the model emits `invoke_skill`, the skill's content is injected inline.

---

## 4. System-Prompt Anatomy

Synthesised from all frameworks above.

### 4.1 Role & Objective

Every agentic system prompt opens with a one-to-two sentence role statement. Key elements:
- **Who** the agent is (domain, title)
- **What success looks like** (terminal condition or goal)
- **Scope limits** (what the agent does NOT handle)

Bad: *"You are an AI assistant that helps users."*
Good: *"You are a data pipeline diagnosis agent. Your goal is to identify the root cause of a failing pipeline and produce a structured remediation plan. You do not execute remediation — only diagnose."*

The scope limit is as important as the goal. Without it, agents drift into tasks that belong to other specialists.

### 4.2 Persona / Tone

Optional but effective when the agent interacts directly with users. Relevant elements:
- Communication style (technical/conversational, verbose/terse)
- Response length calibration (explicit instruction needed for models like Claude Opus 4.7 that self-calibrate)
- Escalation style (how the agent asks clarifying questions)

For internal agents (called only by other agents), persona is usually omitted.

### 4.3 Context Injection (Memory, State, Retrieved Info)

How context enters the prompt varies by framework:
- **AutoGen sequential chat**: carryover mechanism — prior summary injected into the next chat's opening message
- **Anthropic ADK/Google ADK**: PreloadMemoryTool retrieves memories; injected before or alongside the user message
- **CrewAI**: `memory: true` on the agent enables automatic context summarisation at window boundaries
- **OpenAI dynamic instructions**: context injected programmatically by the callback function

**Pattern:** Keep *stable* context (role, tools, output format) in the system prompt. Keep *dynamic* context (current state, retrieved memory, session data) in injected user-turn messages or system-turn continuations.

### 4.4 Tool Catalogue Framing

How tools are described in the system prompt significantly affects call accuracy. Best practices synthesised:

```
## Tools

### tool_name
Purpose: [one sentence — what it does, not how it works]
When to call: [trigger condition]
When NOT to call: [anti-trigger — prevents common misuse]
Parameters:
  - param_name (type, required/optional): [description + format hint]
  - ...
Returns: [output shape description]
```

For large libraries: enumerate only the always-available meta-tools (e.g., `tool_search`) and tell the model to discover the rest.

### 4.5 Decision Format / Output Contract

For agents that must emit structured decisions (like `agent_framework`'s JSON decision loop), the output contract must be explicit in the system prompt:

```
## Output Format

Respond with a single JSON object. Do not wrap in markdown. Valid `kind` values:

{"kind": "final_message", "message": "..."}
{"kind": "call_tool", "tool_name": "...", "arguments": {...}}
{"kind": "call_subagent", "subagent_id": "...", "parameters": {...}}
{"kind": "callback", "intent": "information_request", "message": "..."}

Never combine `subagent_id` and `tool_name` in the same object.
If the model output is not valid JSON, the runtime will raise an error.
```

Including a negative example ("never combine X and Y") is effective at preventing common contract violations.

### 4.6 Workflow Rules (Conversation Flow, State Transitions)

For agents with a structured workflow (multi-phase tasks, stateful processes):

```
## Workflow

Phase 1 — Discovery: Gather all required information using tools.
  - Do not proceed to Phase 2 until you have confirmed X and Y.
Phase 2 — Analysis: Synthesise findings.
Phase 3 — Output: Call `submit_report` with the structured result.
  - This is a terminal tool — calling it ends the session.
```

**OpenAI's framing:** "Conversation Flow — states, goals, and transitions." CrewAI enforces flow through role sequencing (Planner → Researcher → Critic). `agent_framework` uses `terminal_tools` to force phase termination.

### 4.7 Guardrails & Escalation

**Positive framing works better than deny-lists:**
- Good: *"Only answer questions about our product catalog. For other topics, escalate with intent `policy_or_approval`."*
- Less effective: *"Never answer questions about X, Y, Z, A, B..."* (models drift past exhaustive lists)

**Escalation triggers:**
- Confidence threshold: *"If you are uncertain about a critical assumption, use a callback to ask before proceeding."*
- Policy boundary: *"If the user requests an action that modifies live data, request confirmation before calling the tool."*
- Cost/step cap: `max_iter` (CrewAI), `max_turns` (AutoGen), or explicit in prompt: *"If you have not completed the task in 10 tool calls, summarise progress and escalate."*

**Structured-output guardrail** (`agent_framework` CLAUDE.md rule): The prompt must instruct the model to produce valid JSON matching the contract. The runtime raises `ValueError` for violations. **There is no repair logic.** This is a hard guardrail enforced by the runtime, but the prompt must set up the model to succeed — don't rely on the runtime catching every mistake.

### 4.8 Examples (Multishot)

Few-shot examples in the system prompt are effective for:
- Teaching the exact decision format (show a complete Thought→Action→Observation loop)
- Demonstrating edge-case handling (show how to escalate when data is missing)
- Establishing output style (consistent structure for complex tool arguments)

**Guidance from Anthropic multishot docs:**
- Use diverse examples that cover different input types and edge cases
- Include at least one negative example (what the model should NOT do)
- Examples can be in `<examples>` XML tags, keeping them visually distinct from instructions

---

## 5. Sub-Agent and Delegation Design

### 5.1 When to Split: Single-Agent vs Router vs Crew

| Situation | Architecture |
|-----------|-------------|
| Single domain, bounded task | Single agent with all tools |
| Multiple domains, one at a time | Router/supervisor + single worker per domain |
| Multiple domains, concurrent tasks | Crew / `call_subagents` parallel batch |
| Iterative refinement needed | Reflexion loop (critic subagent) |
| Human approval required mid-task | Callback + resume pattern |

**Single-agent failure modes:** Too many tools in context, conflicting role instructions, no clear terminal condition. If an agent's system prompt tries to do three different jobs, split it.

### 5.2 Parameter Passing and Output Contracts

When delegating to sub-agents:
- Pass only what the sub-agent needs — not the entire parent context
- Define the expected output schema in the parent agent's instructions: *"The sub-agent returns a JSON object with fields `status` and `findings`."*
- Use `output_key` (in `call_subagents`) to bind results into named slots for downstream use

**Claude Code pattern:** Sub-agent prompts are self-contained — no references to parent context. The parent constructs the sub-agent's full brief including relevant snippets.

### 5.3 Callback Intents in `agent_framework`

When an agent needs human input or parent escalation, it emits a `callback` decision with an intent:

| Intent | Use case |
|--------|----------|
| `information_request` | Missing input needed to proceed |
| `proposal_review` | Presenting a plan before execution |
| `execution_recovery` | Unexpected error, needs guidance |
| `delegation_return` | Returning result to parent agent |
| `policy_or_approval` | Requires explicit permission |
| `guardrail_trip` | Policy violation detected |

The system prompt should tell the agent *which intents are available* and *when each applies*. Do not leave intent selection implicit.

### 5.4 Parallel vs Sequential Fan-Out

- **`call_subagents`** (parallel): N sub-agents run concurrently. Use when tasks are independent and results will be aggregated.
- **Sequential chaining**: Parent calls one sub-agent, uses result, then calls another. Use when each step depends on the prior step's output.
- **AutoGen sequential chat + carryover**: Built-in sequential chaining with summary injection.

**Timeout and cost:** `SUBAGENT_BATCH_TIMEOUT_SECONDS` (default 300s) limits parallel batch wall-clock time. For expensive sub-agents, design them to fail fast with partial results rather than hanging.

---

## 6. Failure Modes and Guardrails

### 6.1 Common Agentic Failure Modes

**Loop / stuck behaviour:** Agent calls the same tool repeatedly. Caused by ambiguous terminal conditions or tool results that don't update state.
*Fix:* Add an explicit loop counter in instructions, or a `max_iter`-style cap. Include in system prompt: *"If you have called the same tool twice with the same arguments, stop and escalate."*

**Over-calling tools:** Agent calls tools for information already in context. Caused by poor memory design or unclear context injection.
*Fix:* Instruct the agent to check existing context before calling tools. Include: *"Before calling a tool, check whether the answer is already in the conversation history."*

**Mode collapse:** Agent always produces the same decision type regardless of task.
*Fix:* Ensure the system prompt includes examples of each valid decision kind. Imbalanced examples bias the model toward the most represented kind.

**Heuristic repair / non-contract output:** Model emits a partially-valid JSON object with unknown fields or wrong structure. Downstream code silently repairs it, masking the root cause.
*Fix (from `CLAUDE.md`):** Never implement repair logic. The runtime raises `ValueError`. The fix is in the prompt — clarify the contract with examples and negative examples.

**Context window overflow:** Long tool results or verbose reasoning push the system prompt out of the attention window.
*Fix:* Use programmatic tool orchestration (Claude pattern) to control what enters context. Enable `respect_context_window` (CrewAI). Design tools to return concise outputs with optional detailed mode.

### 6.2 Structured-Output Enforcement

`agent_framework`'s `CLAUDE.md` rule — no heuristic repair — is consistent with the industry trend away from fuzzy parsing. Frameworks are converging on:
1. Enforce output structure via `response_format` / JSON mode at the API level
2. Reject non-contract output at the parse layer (`AgentDecision.from_model_response` raises `ValueError`)
3. Fix the root cause in the prompt, not with a Python patch

Anthropic's guidance: use `response_format: {"type": "json_object"}` to force JSON output, then validate against the expected schema. The validation raises clearly, pointing to the prompt or tool result that caused the malformed decision.

---

## 7. Mapping to `agent_framework`

### 7.1 Which Patterns Match `AgentDecision` Kinds

| Industry pattern | `AgentDecision` kind | Notes |
|-----------------|---------------------|-------|
| ReAct loop | Default loop (call_tool → repeat) | One iteration = one decision |
| Plan-and-Execute | `call_subagent` to a planner, then `call_subagents` to executors | Planner agent returns a plan; executors run in parallel |
| Reflexion / self-critique | `call_subagent` to a critic agent + `callback` if human review | Critic result fed back to original agent |
| Handoff / delegation | `call_subagent` or `call_subagents` | Single vs parallel depending on task |
| Escalation / human-in-loop | `callback` with appropriate intent | Parent agent or human receives the callback |
| Router / supervisor | Root agent uses `call_subagent` based on routing logic | Root prompt has routing rules |
| Skills / dynamic instructions | `invoke_skill` | Loads skill content inline |
| Terminal action | `final_message` or configured `terminal_tools` | Both stop the loop |

### 7.2 How Skills/Commands/Terminal-Tools Slot In

- **Skills** (`invoke_skill`): Use for domain-specific knowledge that not every run needs. The skill description in the catalog is the "when to load" signal.
- **Commands** (`COMMANDS_DIRECTORY`): Parametric prompt templates. Use for repeatable user-facing workflows (e.g., `/summarise`, `/diagnose`).
- **Terminal tools** (`terminal_tools` frontmatter list): Tools whose invocation ends the loop immediately without execution. Use for clean handoff patterns — the tool arguments become the `AgentResult.message`.

### 7.3 Where `system.decision.md` / `system.text.md` Align With Industry

| Template | Industry equivalent | Typical use |
|----------|-------------------|-------------|
| `system.decision.md` | OpenAI Agents SDK `instructions` + structured output | Internal agents, tool-use agents, sub-agents |
| `system.text.md` | Plain conversational instructions | User-facing agents with simple response format |
| `system.json_object.md` | OpenAI `response_format: json_object` with callback patterns | Agents that return structured domain data |

---

## 8. References

### Anthropic
- [Claude Prompting Best Practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)
- [Multishot Prompting](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-engineering/multishot-prompting)
- [Writing Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use)
- [Equipping Agents for the Real World with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Claude Code Best Practices](https://code.claude.com/docs/en/best-practices)
- [Claude Code System Prompts (Piebald-AI)](https://github.com/Piebald-AI/claude-code-system-prompts)

### OpenAI
- [OpenAI Agents SDK — Agents](https://openai.github.io/openai-agents-python/agents/)
- [A Practical Guide to Building Agents (PDF)](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- [Prompt Engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)
- [Prompt Guidance](https://developers.openai.com/api/docs/guides/prompt-guidance)

### Google
- [Agentic AI Overview](https://docs.cloud.google.com/architecture/agentic-ai-overview)
- [Vertex AI Agent Builder Overview](https://docs.cloud.google.com/agent-builder/overview)
- [ADK Agent Development](https://docs.cloud.google.com/agent-builder/agent-engine/develop/adk)
- [LangGraph + Gemini ReAct Example](https://ai.google.dev/gemini-api/docs/langgraph-example)

### LangChain / LangGraph
- [Planning Agents (Plan-and-Execute)](https://www.langchain.com/blog/planning-agents)
- [ReAct Agent Template](https://github.com/langchain-ai/react-agent)
- [LangChain Agents Docs](https://docs.langchain.com/oss/python/langchain/agents)
- [ReAct vs Plan-and-Execute Comparison](https://dev.to/jamesli/react-vs-plan-and-execute-a-practical-comparison-of-llm-agent-patterns-4gh9)

### CrewAI
- [CrewAI Agents Docs](https://docs.crewai.com/en/concepts/agents)
- [CrewAI Crash Course — DigitalOcean](https://www.digitalocean.com/community/tutorials/crewai-crash-course-role-based-agent-orchestration)

### AutoGen
- [AutoGen Use Cases](https://microsoft.github.io/autogen/0.2/docs/Use-Cases/agent_chat/)
- [AutoGen Conversation Patterns](https://microsoft.github.io/autogen/0.2/docs/tutorial/conversation-patterns/)
- [AutoGen Architecture Review](https://mgx.dev/insights/autogen-a-comprehensive-review-of-microsofts-multi-agent-conversational-framework-for-llms/8a620b4813ac4155a9f3868e954ebb11)

### LlamaIndex
- [ReActAgent Example](https://developers.llamaindex.ai/python/examples/agent/react_agent/)
- [LlamaIndex Agents Docs](https://docs.llamaindex.ai/en/stable/module_guides/deploying/agents/)

### Patterns / Research
- [ReAct Paper (arxiv 2210.03629)](https://arxiv.org/abs/2210.03629)
- [Reflexion Paper (arxiv 2303.11366)](https://arxiv.org/abs/2303.11366)
- [Reflexion — Prompting Guide](https://www.promptingguide.ai/techniques/reflexion)
- [Agentic Design Patterns: Reflection — DeepLearning.AI](https://www.deeplearning.ai/the-batch/agentic-design-patterns-part-2-reflection/)
- [Role-Specific Prompt Design](https://medium.com/@jeevitha.m/role-specific-prompt-design-tailoring-instructions-for-agent-personalities-a8298a7ed253)
- [ReWOO Paper (arxiv 2305.18323)](https://arxiv.org/abs/2305.18323)
- [LLMCompiler Paper (arxiv 2312.04511)](https://arxiv.org/abs/2312.04511)
