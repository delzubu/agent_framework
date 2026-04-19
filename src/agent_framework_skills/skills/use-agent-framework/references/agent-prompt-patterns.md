# Agent Prompt Patterns — Quick Reference

Machine-readable distillation of the research in `agent-prompt-design-research.md`. No prose — tables, checklists, and copy-ready templates only.

**Recommended prompt structure:** `assets/agent-prompt-organization.md` — full section-by-section guide with examples and a copy-ready template. Load it before writing or reviewing any agent system prompt.

---

## 1. System-Prompt Skeleton

The recommended section order (see `assets/agent-prompt-organization.md` for rationale and full examples):

```markdown
## Responsibilities
[Agent role in 1–2 sentences. Primary output it owns. Main inputs. What belongs to deterministic code, not this agent.]

## Boundaries
- [Hard limit: what the agent must NOT infer, reveal, mutate, or decide.]
- [Scope limit: what belongs to other agents or deterministic code.]
- [Framework limit: when to use callback / tool / subagent / final_message.]
- [Deprecated fields or forbidden behaviors.]

## Workflow
1. [First routing or safety check — callback/block conditions BEFORE normal output schema.]
2. [Resolution or retrieval step.]
3. [Decision / decomposition step.]
4. [Final emission step.]

## Output Shape
[Allowed framework decision envelopes — exact JSON, no prose.]
[Agent-specific payload schema with field names that exactly match all examples.]

## Specific Rules And Examples
### [Topic 1]
[Rule + correct/wrong examples, grouped by topic.]
### [Topic 2]
...
```

**Why this order matters:** Workflow routes callback/block decisions *before* the normal output schema is shown. If examples of the normal result appear first, the model may pick that shape when a callback is actually required.

---

## 2. Pattern Selector

| When you need… | Use pattern | Token cost | Accuracy | Source |
|---|---|---|---|---|
| Single task, ≤3 tools, fast response | **ReAct** | Low (2–3K) | 85% | LangChain, LlamaIndex |
| Multi-step task, known structure, quality matters | **Plan-and-Execute** | Higher (3–5K) | 92% | LangChain |
| Agent must self-improve across retries | **Reflexion** | Highest | High on code/reasoning | arxiv 2303.11366 |
| Multiple independent subtasks | **Parallel fan-out** (`call_subagents`) | Scales with N | — | `agent_framework` |
| Multiple specialised domains | **Router + workers** | Low router, per-worker | — | CrewAI, AutoGen |
| Large tool library (>10K tokens) | **Tool search / progressive disclosure** | ~85% less vs. full load | — | Anthropic 2026 |
| Domain knowledge, not always needed | **Skills / invoke_skill** | Near-zero until triggered | — | Anthropic Skills |
| Iterative refinement (Planner→Researcher→Critic) | **Crew** | 3× single agent | — | CrewAI |
| Human approval mid-task | **Callback** (`information_request`, `proposal_review`) | — | — | `agent_framework` |

---

## 3. Role / Persona Checklist

- [ ] Role stated in one sentence (domain + title)
- [ ] Single, unambiguous goal (avoid "help users with anything")
- [ ] Explicit scope limit ("You do NOT handle X — escalate instead")
- [ ] Backstory / domain grounding present if persona matters to user-facing interactions
- [ ] Tone / verbosity stated if the model needs non-default style
- [ ] For internal agents (called by other agents): omit persona, keep only role and goal

**Anti-patterns:**
| Mistake | Problem |
|---------|---------|
| Same role across all agents in a crew | Specialisation value disappears |
| Multiple conflicting goals | Accuracy drops; agent picks one arbitrarily |
| No scope limit | Agent drifts into tasks meant for other agents |
| Backstory without domain grounding | Unpredictable behaviour |

---

## 4. Tool Framing Checklist

Per tool in the system prompt:

- [ ] Name uses namespace prefix (e.g., `jira_issues_search`, not `search`)
- [ ] One-sentence purpose (what it does, not how it works)
- [ ] Trigger condition stated ("Call when you need to look up X")
- [ ] Anti-trigger stated ("Do NOT call if X is already in context")
- [ ] Parameter names are unambiguous (`user_id` not `user`)
- [ ] Parameter descriptions include format hint where non-obvious (date format, currency code)
- [ ] Return value shape described (field names, not just "a result")
- [ ] Error handling instruction: what to do if the tool fails or returns empty

**For large tool libraries:**
- [ ] Expose only a `tool_search(query)` meta-tool in the system prompt
- [ ] Instruct model to search before every tool call ("Do not assume tool names")
- [ ] OR use skills/catalog pattern — load tool subsets on demand

---

## 5. Decision-Format Contracts

### `agent_framework` JSON decision kinds

```json
// Terminal — agent is done
{"kind": "final_message", "message": "string"}

// Tool call
{"kind": "call_tool", "tool_name": "string", "arguments": {}}

// Single sub-agent delegation
{"kind": "call_subagent", "subagent_id": "string", "parameters": {}}

// Parallel batch delegation
{
  "kind": "call_subagents",
  "subagents": [
    {"subagent_id": "string", "parameters": {}, "output_key": "string"},
    ...
  ]
}

// Human/parent escalation
{
  "kind": "callback",
  "intent": "information_request | proposal_review | execution_recovery | delegation_return | policy_or_approval | guardrail_trip",
  "message": "string"
}

// Load a skill
{"kind": "invoke_skill", "skill_id": "string"}
```

**Output contract rules (enforce in system prompt):**
- Do NOT wrap JSON in markdown code fences
- Do NOT include both `subagent_id` and `tool_name` in the same object
- Do NOT invent `kind` values — only the six above are valid
- Output MUST be a single JSON object, nothing else

**Add to system prompt (negative example):**
```
WRONG: ```json\n{"kind": "call_tool", ...}```
WRONG: {"kind": "gather_context", ...}
WRONG: {"kind": "call_tool", "tool_name": "x", "subagent_id": "y"}
CORRECT: {"kind": "call_tool", "tool_name": "x", "arguments": {...}}
```

---

## 6. Callback Intent Selector

| Situation | Intent |
|-----------|--------|
| Missing information needed to proceed | `information_request` |
| Presenting a plan before executing | `proposal_review` |
| Unexpected error, needs guidance | `execution_recovery` |
| Returning a result to the parent agent | `delegation_return` |
| Requires explicit permission or approval | `policy_or_approval` |
| Policy violation detected | `guardrail_trip` |

**Rule:** State in the system prompt which intents the agent is permitted to use. Do not leave intent selection implicit.

---

## 7. Sub-Agent Delegation Rubric

| Question | If yes → | Notes |
|----------|----------|-------|
| Is the subtask fully independent? | `call_subagent` (single) | One sub-agent, sequential |
| Are N subtasks independent of each other? | `call_subagents` (parallel batch) | Max parallelism: `SUBAGENT_MAX_PARALLELISM` (default 8) |
| Does the subtask need human input? | `callback` before delegating | Get input, then delegate |
| Does execution need human review mid-way? | `callback` with `proposal_review` | Show plan, await approval |
| Does the subtask need a clean exit signal? | Configure `terminal_tools` on the sub-agent | Tool call ends loop without execution |
| Are subtasks chained (B depends on A)? | Sequential `call_subagent` calls | Parent waits for A, passes result to B |

---

## 8. Guardrail Patterns

**Positive framing (works better than deny-lists):**
```
Only handle questions about [domain]. For anything outside this scope,
use callback with intent "policy_or_approval" and explain why you cannot proceed.
```

**Escalation triggers to include in system prompt:**

| Trigger | Instruction |
|---------|-------------|
| Ambiguous critical assumption | "If uncertain about X, use information_request before proceeding" |
| Destructive action | "If the action modifies live data, use proposal_review first" |
| Step cap reached | "If you have made 10 tool calls without completing the task, use execution_recovery" |
| Same tool called twice with same args | "Stop and escalate with execution_recovery" |
| Unexpected tool result | "Do not retry blindly — escalate with execution_recovery and describe the unexpected result" |

**Output format guardrail:**
```
Your response MUST be a single valid JSON object matching the schema above.
The runtime raises an error for any other format — there is no repair.
```

---

## 9. Multishot Example Structure

Include 1–3 examples in the system prompt using this template:

```
<examples>
<example>
User input: [example input]
Correct decision: {"kind": "...", ...}
Reasoning: [optional — why this decision, not another]
</example>

<example>
User input: [edge case or escalation scenario]
Correct decision: {"kind": "callback", "intent": "...", "message": "..."}
</example>
</examples>
```

**Guidelines:**
- Include at least one normal case and one escalation/edge case
- Cover each valid `kind` that this agent can emit across examples
- Negative example: "Do NOT do this: `{\"kind\": \"unknown\"}`"
- Diverse inputs — don't use slight variations of the same scenario

---

## 10. Mapping to `agent_framework`

| Industry pattern | Our primitive | Configure via |
|-----------------|--------------|---------------|
| ReAct loop | Default decision loop | System prompt output format, tools list |
| Plan-and-Execute | Planner agent (`call_subagent`) + executor agents (`call_subagents`) | Separate `.md` files per agent |
| Reflexion / self-critique | Critic sub-agent + `callback` | Critic agent with evaluation role |
| Router / supervisor | Root agent with routing rules + `call_subagent` | Root agent system prompt |
| Parallel crew | `call_subagents` with `output_key` per agent | `call_subagents` decision kind |
| Tool search | MCP bridge tools + skills catalog | `.mcp.json`, `SKILLS_DIRECTORY` |
| Progressive disclosure | `invoke_skill` | Skill `.md` files, catalog injection |
| Terminal handoff | `terminal_tools` list in frontmatter | Agent `.md` frontmatter |
| Human-in-loop | `callback` with intent | System prompt guardrail rules |
| Sequential pipeline | Sequential `call_subagent` calls | Agent logic + parameter passing |

---

## 11. Red Flags in an Agent `.md`

If any of these are true, the agent will likely malfunction:

- [ ] **No explicit decision format** — model will invent its own output structure
- [ ] **Tools listed without when-to-call guidance** — agent over-calls or under-calls
- [ ] **Role defined but no goal** — agent has identity but no termination signal
- [ ] **Goal defined but no scope limit** — agent drifts across domains
- [ ] **No terminal/exit criterion** — loop may never end
- [ ] **Heuristic recovery hints** — phrases like "if unclear, make your best guess" violate the CLAUDE.md no-repair rule and mask errors
- [ ] **`kind` values invented in examples** — model will emit them during inference
- [ ] **All examples of the same decision kind** — model collapses to that kind
- [ ] **No negative example for the output contract** — common format violations not suppressed
- [ ] **Sub-agent prompt references parent context** — sub-agents must be self-contained
- [ ] **No callback/escalation path** — agent cannot ask for help when stuck

---

## 12. Agentic Prompt Checklist (Final Sign-Off)

Before committing a new agent `.md` (mirrors the review checklist in `assets/agent-prompt-organization.md`):

**Responsibilities**
- [ ] Agent role stated in 1–2 sentences — no schema details, no edge-case rules
- [ ] Primary output or decision the agent owns is explicit
- [ ] What NOT to push to this agent (belongs to deterministic code or other agents) is stated

**Boundaries**
- [ ] Hard "do not" / "must not" statements for every scope limit
- [ ] Framework limit stated: when to use `callback` / `call_tool` / `call_subagent` / `final_message`
- [ ] Deprecated or forbidden fields listed

**Workflow**
- [ ] Callback / block routing appears *before* the normal output schema in the prompt
- [ ] Steps are ordered (numbered list), not a flat list of rules
- [ ] Each step has a clear exit condition

**Output Shape**
- [ ] All allowed `kind` values listed with exact JSON fragments
- [ ] Field names in the schema exactly match field names in all examples
- [ ] Negative output examples present (wrong `kind`, wrong structure, forbidden fields)
- [ ] "JSON only, no markdown fences" constraint stated

**Specific Rules & Examples**
- [ ] At least one example showing the normal decision path
- [ ] At least one example showing a callback / escalation path
- [ ] Negative examples target real failure modes, not hypothetical ones
- [ ] No example uses a field name that contradicts the Output Shape schema

**Runtime**
- [ ] Terminal condition clear (which tool / which `kind` ends the loop)
- [ ] Persona included only if user-facing
- [ ] Does the model emit valid JSON on the first call without repair?
