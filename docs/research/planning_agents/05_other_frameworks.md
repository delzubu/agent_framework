# Other Agentic Frameworks & Foundational Techniques

This report complements the focused reports on `claude-code` (01), `nano-claude-code` (02), LangGraph (03), and Microsoft Agent Framework (04). It covers the rest of the landscape — OpenAI Agents SDK, CrewAI, AutoGPT/BabyAGI, the foundational techniques (ReAct, Plan-and-Solve, Reflexion, Tree-of-Thoughts), Anthropic's "Building effective agents" guide, DSPy, and Devin/OpenHands — and ends with cross-cutting patterns.

---

## 1. OpenAI Agents SDK (`openai-agents-python`)

The Swarm successor that became OpenAI's official agents SDK. Three primitives:

- **Agent**: an LLM with `instructions` and a `tools` list.
- **Handoff**: an agent listed as a tool of another agent — invoking the handoff transfers the conversation to that agent.
- **Guardrail**: input/output validation that runs *in parallel* with agent execution.

**Coordination model — "Python-first orchestration":**

> "Use built-in language features to orchestrate and chain agents, rather than needing to learn new abstractions."

There is **no planner pattern** — no Plan schema, no replanner, no DAG. Multi-step coordination is one of:

- A single agent ReAct-loops to completion via `Runner.run()`.
- Agents-as-tools: a primary agent calls another agent like a function, parent retains ownership.
- Handoffs: control fully transfers, the called agent returns when done; the conversation context follows.
- Manager-style orchestration: the developer writes Python that coordinates agents directly.

The SDK explicitly defers planning to "the model + the developer's Python," not to the framework. This is the polar opposite of LangGraph's "graph-as-data" position.

**Parallelism:** primarily for guardrails (run safety checks alongside agent execution). Parallel agent dispatch is left to user code (`asyncio.gather` over `Runner.run`).

---

## 2. CrewAI

CrewAI structures multi-agent work as **Crews** of **Agents** executing **Tasks** under a **Process**.

### Process modes

- **Sequential**: tasks execute in order. Output of task N is automatically supplied as `context` to task N+1 (overridable per-task by listing specific predecessor tasks).
- **Hierarchical**: a **manager agent** (or `manager_llm`) sees all tasks and "handles planning, delegation, and validation. Tasks are not pre-assigned; the manager allocates tasks to agents based on their capabilities, reviews outputs, and assesses task completion." This is supervisor-pattern planning, where the LLM-based manager decides task→agent routing each step.

### Planning feature

Optional `planning=True` on Crew construction activates an **AgentPlanner**:

> "Before each Crew iteration, all Crew information is sent to an AgentPlanner that will plan the tasks step by step."

The output of AgentPlanner is appended to each task's description before agents begin work. So the plan is *not* a separate executable structure — it's *plan-as-prompt-context-injection*. This is similar to claude-code's plan-mode ("plan goes into a Markdown file the executor reads") rather than LangGraph's `Plan` schema.

The planner LLM defaults to `gpt-4o-mini`, configurable via `planning_llm`.

### Plan format

Numbered steps with sub-steps for each task — "research scope definition, source identification, data collection, analysis, organization, and finalization phases" in the docs example. No fixed schema; freeform prose.

### Replanning

Replanning happens "before each Crew iteration" — i.e. every full pass through the Crew's task list re-runs the planner. There's no per-task replan or stall detection.

---

## 3. AutoGPT / BabyAGI / SmolAgents — the foundational autonomous loop

### AutoGPT (the original)

Architecture: a single LLM in an infinite loop with a structured prompt that lists `CONSTRAINTS`, `COMMANDS` (tools), `RESOURCES`, and `PERFORMANCE EVALUATION`. The model emits a single JSON action per turn:

```json
{
    "thoughts": {
        "text": "...",
        "reasoning": "...",
        "plan": "- short bulleted\n- list that conveys\n- long-term plan",
        "criticism": "constructive self-criticism",
        "speak": "thoughts summary to say to user"
    },
    "command": { "name": "command name", "args": { "arg name": "value" } }
}
```

Notable design elements (from `autogpt/prompts/prompt.py`):
- **`plan` is just a bullet list inside the JSON.** No formal data structure — every turn the model rewrites it.
- **`criticism`** is mandatory self-evaluation before each action — the original Reflexion-style baked-in.
- **Memory** (vector store): tool results are written to a vector DB and retrieved by similarity in subsequent prompts. This is the original "results live in retrieval, not the context window" pattern.
- **Task list** (in BabyAGI variants): a plain list of strings that the model prioritizes, executes the top one, then re-prioritizes based on results.

### BabyAGI

Three loops over a flat task list:
1. **Execution agent** — picks the top task, runs it.
2. **Task creation agent** — given the result, generates *new* tasks.
3. **Task prioritization agent** — re-sorts the task list.

The "plan" is the task list; replanning is implicit in the prioritization step. Vector memory stores results.

### SmolAgents (Hugging Face)

The current minimalist heir. The agent emits **executable Python code** (not JSON tool calls) every turn, and the runtime executes that code in a sandbox with a curated function library. Planning is implicit — the model writes Python that calls planning helpers if it wants. The contribution is "the action space *is* code," sidestepping JSON-tool-call brittleness.

---

## 4. Foundational planning techniques (the papers)

### ReAct (Yao et al., 2022)

Interleaves **Thought / Action / Observation** trace:

```
Thought 1: I need to find ...
Action 1: Search[...]
Observation 1: ...
Thought 2: ...
Action 2: ...
```

This is the prompt scaffold every modern function-calling agent inherits. The Thought step is the model's *reasoning out loud*; tool calls go in Action; tool results come back as Observation. No separate plan — planning is per-step thinking.

### Plan-and-Solve (Wang et al., 2023)

Two-stage prompting variant of CoT. The exact prompt that beats vanilla CoT:

> "Let's first understand the problem and devise a plan to solve the problem. Then, let's carry out the plan and solve the problem step by step."

Variant ("PS+"):

> "Let's first understand the problem, extract relevant variables and their corresponding numerals, and devise a complete plan. Then, let's carry out the plan, calculate intermediate variables (pay attention to correct numerical calculation and commonsense), solve the problem step by step, and show the answer."

This single prompt seeded the entire "plan first, then execute" agent literature — both LangGraph's Plan-and-Execute pattern and CrewAI's `planning=True` are direct descendants.

### Reflexion (Shinn et al., 2023)

After each trajectory, the agent generates a textual **self-reflection** stored in a memory buffer. On the next attempt, the buffer is included in the prompt. The reflection prompt scaffold:

> "You were unsuccessful in completing the task. Diagnose a possible reason for failure and devise a new, concise, high level plan that aims to mitigate the same failure. Use complete sentences."

This is the conceptual root of Magentic's "stall → revise plan" loop and LangGraph's Plan-and-Execute replanner.

### Tree of Thoughts (Yao et al., 2023)

Replaces linear chain-of-thought with **search over a tree** of partial reasoning steps. At each node the model proposes K candidate next steps; a value function (LLM-as-judge or task-specific) scores them; the runtime expands the best (or runs BFS/DFS). Real implementations are rare in production agents because of the cost (K× per step), but the idea — *generate multiple candidate plan steps and pick the best* — appears in the parallelization / voting pattern Anthropic recommends.

---

## 5. Anthropic's "Building effective agents"

The most influential design guide of 2024–25. Six patterns + three principles.

### The six patterns

1. **Prompt chaining** — fixed sequential decomposition. *"Use when task can be easily and cleanly decomposed into fixed subtasks."*
2. **Routing** — classify input, dispatch to specialist. *"Use when distinct categories that are better handled separately."*
3. **Parallelization** — run independent subtasks concurrently (sectioning) or run the same task K times and aggregate (voting). *"Use when subtasks can be parallelized for speed, or when multiple perspectives are needed."*
4. **Orchestrator-Workers** — central LLM dynamically decomposes and delegates. *"Use for tasks where you can't predict the subtasks needed (e.g., multi-file coding). Subtasks aren't pre-defined, but determined by the orchestrator."*
5. **Evaluator-Optimizer** — generator + critic in a loop. *"Use when clear evaluation criteria, and when iterative refinement provides measurable value."*
6. **Autonomous Agents** — LLM in a tool-use loop with environmental feedback. *"Use for open-ended problems where it's difficult or impossible to predict the required number of steps. You must have some level of trust in its decision-making."*

### Three principles

1. **Simplicity** — start simple, add complexity only when needed.
2. **Transparency** — explicitly show planning steps.
3. **Tool documentation** — treat tool engineering with the same care as prompt engineering: *"Keep the format close to what the model has seen naturally occurring in text on the internet… Make sure there's no formatting 'overhead' such as having to keep an accurate count of thousands of lines of code."*

### What NOT to do

> "Start with simple prompts, optimize them with comprehensive evaluations, and add multi-step agentic systems only when simpler solutions fall short."

> "Frameworks… often create extra layers of abstraction that can obscure the underlying prompts and responses, making them harder to debug. They can also make it tempting to add complexity when a simpler setup would suffice."

This guide is the explicit philosophical underpinning of `claude-code`'s design — todo-list + plan mode + sub-agents are the **orchestrator-workers** pattern, the verification gate is **evaluator-optimizer**, parallel tool calls are **parallelization**.

---

## 6. DSPy (briefly)

DSPy treats agents as **programs that compose typed Modules** (`Predict`, `ChainOfThought`, `ReAct`, `ProgramOfThought`). The interesting contribution for planning is that **prompts are not authored — they are compiled** by an optimizer (`BootstrapFewShot`, `MIPROv2`, etc.) from a training set of (input, expected-output) pairs.

For planning agents this means: instead of hand-tuning a planner system prompt, you write a `Plan` signature (`Plan(task: str) -> steps: list[str]`) and let DSPy generate few-shot demonstrations and optimize the prompt phrasing automatically.

This is mostly relevant to us as a *future* layer — we'd run DSPy over our planner agent's markdown file, treating it as a starting prompt and letting an optimizer rewrite the body.

**TextGrad** (Yuksekgonul et al., 2024) does something similar but using LLM-generated *textual* gradients on the prompt itself. Same spirit: planner prompts as optimizable programs.

---

## 7. Devin / SWE-agent / OpenHands

### Devin (Cognition AI, closed)

Public details limited. Known to use an internal **planner + scratchpad + browser/editor/shell** loop. The planner writes a numbered plan, executes it, and updates the plan as it goes. It's effectively the orchestrator-workers pattern with a single very capable executor.

### SWE-agent (Yang et al., 2024)

Introduced the **Agent-Computer Interface (ACI)** concept — that the *tool design* matters as much as the agent's reasoning. Their tool surface is a small, hand-tuned set of file-navigation commands. No formal planner; the model uses a ReAct loop with these tools.

### OpenHands (formerly OpenDevin)

`CodeActAgent` is the default agent. Action space:
- **Conversation** (natural language to user/agent),
- **CodeAct** — `bash` or interactive `python` execution.

By unifying the action space around code execution, OpenHands sidesteps the JSON-tool-call brittleness — every action is a code block. Planning is implicit (the model writes a multi-step bash script if it wants), and replanning happens by the model issuing new code based on previous outputs in context.

OpenHands does also support multi-agent setups (delegator → coder, etc.) but the documented default is the single CodeActAgent.

---

## Cross-cutting patterns and key takeaways

1. **Plans are almost always either (a) a list of strings or (b) a free-form Markdown file.** No mainstream framework asks the model to emit a typed DAG with edges. LLMCompiler and ReWOO are the exceptions, and both struggle with parser fragility. **Lesson:** start with a list-of-steps schema; add structure only if you have a concrete reason.

2. **Replanning is either model-driven, structural, or both.** Model-driven (Plan-and-Execute, claude-code TodoWrite-rewrite) is reactive — the model decides. Structural (Magentic stall counter, BabyAGI prioritization pass) is forced — the runtime triggers it on counters or schedules. The strongest designs combine both: model-driven by default, with structural fallback when no progress is detected.

3. **Result reuse comes in three flavors:**
    - **In-context** (tool results stay in the message log) — claude-code, nano-claude, OpenAI Agents SDK.
    - **Symbolic substitution** (`#E1`, `${1}`, `{{step_1}}`) — ReWOO, LLMCompiler, our `player_controller` spec.
    - **Vector retrieval** (results dumped to a vector DB, retrieved by similarity) — AutoGPT, BabyAGI.
   The first scales to short tasks; the second scales to one big planner call; the third scales to long-horizon autonomous agents but is fragile.

4. **Sub-agents are scoped context windows, not specialized models.** Every framework reaches the same conclusion: a sub-agent's *purpose* is to keep its working context out of the parent's context window. The model behind it can be the same model. claude-code, MS Agent Framework, OpenAI Agents SDK, and CrewAI all converge on this.

5. **Parallelism falls out of two simple primitives.** Either *batch dispatch* (`call_subagents`, `Send`, `concurrent` workflow, `asyncio.gather`) or *concurrency-safe tool flag* (claude-code's `isConcurrencySafe()`). Both are simpler than a planning-time DAG. **Lesson:** make parallel/sequential a runtime concern, not a planner concern, except when the dependencies are genuinely declarative.

6. **HITL is becoming a typed first-class event.** Magentic's `MagenticPlanReviewRequest`, Microsoft's `request_info`, claude-code's `AskUserQuestion`, LangGraph's `interrupt()`, our `callback` decision — they all converge on "agent emits a typed pause-request event; the caller resumes with a typed response." **Lesson:** type the resume payload, don't make it a free-text "user message."

7. **The empty-system-prompt + per-turn-injected-template idiom is winning.** Magentic, claude-code's plan-mode reminders, LangGraph's per-node prompts. Why: prompt-cache friendliness and locality of intent ("the planning instructions live next to the planning data, not 5 messages ago"). **Lesson:** put role + contract in the system prompt; put workflow + step instructions in per-turn user-message attachments.

8. **Structured-output decisions (Pydantic / JSON schema) beat free-text.** Plan-and-Execute's `Act = Union[Plan, Response]`, LLMCompiler's `JoinOutputs`, Magentic's Progress Ledger schema — every well-engineered agent forces the "what next" decision through a schema. Aligns with our project's no-silent-repair rule.

9. **Plans on disk + slug paths gives free recovery and resumability.** claude-code's `~/.claude/plans/{slug}.md`, Magentic's checkpoint storage. Sessions resume; plans survive crashes. We persist conversations but not plans — a single file per planning session would close the gap cheaply.

10. **The "planner" is often just one prompt away from the executor.** Plan-and-Solve is *literally* one extra sentence in the prompt. Plan-and-Execute has separate planner/executor agents because LangGraph models it that way, but functionally it's "one model call decides the plan, the same model can execute steps." **Lesson:** for our framework, a planner doesn't need its own agent type — a markdown agent with a "first turn produces a plan, subsequent turns execute it" instruction can be the entire implementation.

11. **Verification / criticism / reflection are mandatory in long-horizon agents.** Reflexion's per-attempt self-reflection, claude-code's verification-agent gate, AutoGPT's mandatory `criticism` field, Magentic's stall-triggered fact updates. **Lesson:** bake "what could go wrong / has progress been made" into either the decision schema or a structural runtime check.

12. **Don't build a framework when a 50-line script will do.** Anthropic's headline. Every framework surveyed has a "we know we're more abstraction than you might want" disclaimer. **Lesson:** every new orchestration concept we add to `agent_framework` should pay for itself with a use case the existing primitives can't express cleanly.

---

## Sources

- [OpenAI Agents SDK docs](https://openai.github.io/openai-agents-python/)
- [CrewAI — Planning](https://docs.crewai.com/concepts/planning)
- [CrewAI — Processes](https://docs.crewai.com/concepts/processes)
- [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- AutoGPT prompt source: `Significant-Gravitas/AutoGPT` `classic/original_autogpt/autogpt/prompts/`
- ReAct: Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models*, 2022 (arxiv.org/abs/2210.03629)
- Plan-and-Solve: Wang et al., *Plan-and-Solve Prompting*, 2023 (arxiv.org/abs/2305.04091)
- Reflexion: Shinn et al., *Reflexion: Language Agents with Verbal Reinforcement Learning*, 2023 (arxiv.org/abs/2303.11366)
- Tree of Thoughts: Yao et al., *Tree of Thoughts: Deliberate Problem Solving with Large Language Models*, 2023 (arxiv.org/abs/2305.10601)
- DSPy: stanfordnlp/dspy
- TextGrad: Yuksekgonul et al., *TextGrad: Automatic "Differentiation" via Text*, 2024 (arxiv.org/abs/2406.07496)
- SWE-agent: Yang et al., *SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering*, 2024
- [OpenHands docs](https://docs.openhands.dev/modules/usage/agents)
