# Planning & Execution in `nano-claude-code`

## Overview

`nano-claude-code` is a single-process, terminal-oriented Claude Code clone. Its planning model is deliberately *implicit and tool-driven* rather than phase-based:

- There is **no dedicated "planner" stage** in the agent loop and **no `Plan` data structure**.
- The core control flow is the classic `model → tool calls → tool results → model` loop in `agent.py:run`. The model is *prompted* (in the system prompt) to break multi-step work into tasks, and is given a **persistent `Task` store** (`task/` package, persisted to `.nano_claude/tasks.json`) plus `TaskCreate` / `TaskUpdate` / `TaskList` / `TaskGet` tools that act as the externalised plan/todo list — analogous to Anthropic's `TodoWrite`.
- A separate `Agent` tool spawns sub-agents in a thread pool, with optional `wait=false` background mode and optional `isolation="worktree"` git‑worktree separation. Together with `SendMessage`, `CheckAgentResult`, and named agents, this provides the "parallel sub-agents" capability.
- A `Skill` system loads markdown prompt templates (with YAML frontmatter) that can be invoked inline or "forked" into a fresh sub-agent context.
- Long contexts are mitigated by `compaction.maybe_compact`, which runs *inside* the agent loop on every turn — a kind of automatic mid-execution pruning rather than re-planning.

The remainder of this report walks through each planning concern and quotes the relevant code.

---

## 1. Plan generation

There is **no explicit plan-generation step or planning prompt section**. Planning is delegated to the LLM under guidance from the system prompt, and the externally visible artefact is the **Task list**, written via tool calls.

The system prompt tells the model to use the task tools when work is multi-step (`context.py:56-64`):

```python
## Task Management & Background Jobs
Use these tools to track multi-step work or execute background timers:
- **SleepTimer**: Put yourself to sleep for a given number of `seconds`. ...
- **TaskCreate**: Create a task with subject + description. Returns the task ID.
- **TaskUpdate**: Update status (pending/in_progress/completed/cancelled/deleted), ...
- **TaskGet**: Retrieve full details of one task by ID.
- **TaskList**: List all tasks with status icons and pending blockers.

**Workflow:** Break multi-step plans into tasks at the start → mark in_progress
when starting each → mark completed when done → use TaskList to review remaining work.
```

The `TaskCreate` tool description reinforces the same intent (`task/tools.py:13-17`):

```python
"description": (
    "Create a new task in the task list. "
    "Use this to track work items, to-dos, and multi-step plans. "
    "Returns the new task's ID and subject."
),
```

The task data model itself is rich enough to express a small DAG of work — every task carries dependency edges so the LLM can encode "do A before B" without inventing extra structure (`task/types.py:20-32`):

```python
@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    active_form: str = ""          # e.g. "Running tests"
    owner: str = ""
    blocks: list[str] = field(default_factory=list)      # IDs this task blocks
    blocked_by: list[str] = field(default_factory=list)  # IDs that block this task
    metadata: dict[str, Any] = field(default_factory=dict)
```

There is no validator that enforces "you must call `TaskCreate` first": planning is purely a prompt-driven convention.

There is one *conventional* multi-step plan generator outside the main loop — the `/brainstorm` slash command does call multiple personas and synthesises a written plan, but that is a one-shot scripted utility, not the runtime planner. After it runs, it injects a synthesis prompt back into the regular agent loop (`nano_claude.py:499-506`):

```python
synthesis_prompt = f"""I have just completed a multi-agent brainstorming session regarding: '{user_topic}'.
The full debate results have been saved to the file: {out_file}

Please read that file, then analyze the diverse perspectives. Identify the strongest ideas, potential conflicts, and provide a synthesized 'Master Plan' with concrete phases. Be concise and actionable."""
```

---

## 2. Plan execution

Execution is just the **tool-calling loop**, not a stored-plan executor. Each turn the model decides which task (if any) to advance, calls real tools, then optionally updates task status. The loop in `agent.py:83-150`:

```python
while True:
    if cancel_check and cancel_check():
        return
    state.turn_count += 1
    assistant_turn: AssistantTurn | None = None

    # Compact context if approaching window limit
    maybe_compact(state, config)

    # Stream from provider (auto-detected from model name)
    for event in stream(
        model=config["model"],
        system=system_prompt,
        messages=state.messages,
        tool_schemas=get_tool_schemas(),
        config=config,
    ):
        ...
        elif isinstance(event, AssistantTurn):
            assistant_turn = event

    if assistant_turn is None:
        break

    # Record assistant turn in neutral format
    state.messages.append({
        "role":       "assistant",
        "content":    assistant_turn.text,
        "tool_calls": assistant_turn.tool_calls,
    })
    ...
    if not assistant_turn.tool_calls:
        break   # No tools → conversation turn complete

    # ── Execute tools ────────────────────────────────────────────────
    for tc in assistant_turn.tool_calls:
        yield ToolStart(tc["name"], tc["input"])
        permitted = _check_permission(tc, config)
        if not permitted:
            req = PermissionRequest(description=_permission_desc(tc))
            yield req
            permitted = req.granted
        if not permitted:
            result = "Denied: user rejected this operation"
        else:
            result = execute_tool(tc["name"], tc["input"], ...)
        yield ToolEnd(tc["name"], result, permitted)
        state.messages.append({
            "role":         "tool",
            "tool_call_id": tc["id"],
            "name":         tc["name"],
            "content":      result,
        })
```

Notable points:

- **No plan is dereferenced**. The loop does not iterate over tasks; it iterates over assistant turns. Whether the model executes a "plan" or freelances depends entirely on its own choice.
- The "current step" is implicitly whichever task the model has marked `in_progress`. The slash command `/tasks in-progress` exists for users to query that view, but the loop doesn't use it.
- Tool calls inside one assistant turn run **sequentially in for-loop order**; there is no in-loop parallel tool dispatch. (Sub-agents are how parallelism is achieved — see §5.)

---

## 3. Re-planning mid-execution

Re-planning is **not a first-class operation**. It occurs in two implicit ways:

1. **Task mutation.** The model can call `TaskCreate` again, or `TaskUpdate` with `status="cancelled"`/`"deleted"`, or add new `add_blocked_by` edges, at any turn. Because the store is mutated in-place and persisted (`task/store.py:93-172`), the next `TaskList` reflects the revised plan immediately.

2. **Conversation compaction.** Long-running sessions trigger `compaction.maybe_compact` on every loop iteration (`agent.py:90`). When tokens exceed 70 % of the window, it first snips old tool results, then summarises the older half of the conversation into a single synthetic user/assistant pair (`compaction.py:170-196`):

   ```python
   def maybe_compact(state, config: dict) -> bool:
       model = config.get("model", "")
       limit = get_context_limit(model)
       threshold = limit * 0.7

       if estimate_tokens(state.messages) <= threshold:
           return False

       # Layer 1: snip old tool results
       snip_old_tool_results(state.messages)

       if estimate_tokens(state.messages) <= threshold:
           return True

       # Layer 2: auto-compact
       state.messages = compact_messages(state.messages, config)
       return True
   ```

   The summary prompt is plain text, not plan-structured (`compaction.py:139-143`):

   ```python
   summary_prompt = (
       "Summarize the following conversation history concisely. "
       "Preserve key decisions, file paths, tool results, and context "
       "needed to continue the conversation:\n\n" + old_text
   )
   ```

   After compaction the model effectively re-derives its understanding of the plan from the persistent task list (which survives compaction because it lives outside `state.messages` on disk) and the summary text.

The user can also interrupt and force re-planning via `AskUserQuestion` (a tool that pauses the loop and asks the user) and via `cancel_check` (`agent.py:62, 84`), or by `SendMessage` to a background agent (queued to its inbox in `multi_agent/subagent.py:386-401`).

---

## 4. Result storage and reuse

Step results are stored in **three** places, in decreasing order of importance to the loop:

1. **`AgentState.messages` (the LLM context window)** — every tool result is appended as a `tool` role message, so subsequent model turns see it directly (`agent.py:144-150`):

   ```python
   state.messages.append({
       "role":         "tool",
       "tool_call_id": tc["id"],
       "name":         tc["name"],
       "content":      result,
   })
   ```

   This is the *primary* mechanism — there is no scratchpad keyed by step ID. Reuse happens implicitly because the model re-reads earlier turns.

2. **The `Task` store**, which has a `metadata: dict` field per task (`task/types.py:30`). The model is free to stash result summaries or output keys there via `TaskUpdate(metadata={...})`. The framework does not prescribe a schema.

3. **The `memory/` package**, providing `MemorySave` / `MemorySearch` / `MemoryList` tools backed by markdown files in `~/.nano_claude/memory` (user) or `.nano_claude/memory` (project), with a `MEMORY.md` index that is **injected into the system prompt** on every run (`context.py:162-165`):

   ```python
   memory_ctx = get_memory_context()
   if memory_ctx:
       prompt += f"\n\n# Memory\nYour persistent memories:\n{memory_ctx}\n"
   ```

   This is the cross-session store; nothing in the agent loop writes to it automatically.

For sub-agents, there's a fourth mechanism: the parent receives the sub-agent's *final assistant text* as the value of the `Agent` tool call. `_extract_final_text` walks back through the sub-agent's `state.messages` to find the last assistant message (`multi_agent/subagent.py:268-273`):

```python
def _extract_final_text(messages):
    """Walk backwards through messages, return first assistant content string."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"]
    return None
```

This is the only "structured handoff" between agents — there are no typed output keys, no `output_key` parameter, no batch-result dataclass.

---

## 5. Tool / sub-agent / skill use during plan execution

### Tools

All tools are registered into a flat module-level registry (`tool_registry.py`) and dispatched by `execute_tool(name, input, ...)`. Built-ins live in `tools.py`; specialised packs (`task/tools.py`, `multi_agent/tools.py`, `skill/tools.py`, `memory/tools.py`, `mcp/tools.py`) self-register on import. Within one assistant turn, tool calls execute sequentially in for-loop order (`agent.py:123`).

### Sub-agents

The `Agent` tool (`multi_agent/tools.py:160-221`) spawns a sub-agent task on a `ThreadPoolExecutor` (`multi_agent/subagent.py:281-411`):

```python
class SubAgentManager:
    def __init__(self, max_concurrent: int = 5, max_depth: int = 5):
        self.tasks: Dict[str, SubAgentTask] = {}
        self._by_name: Dict[str, str] = {}
        self.max_concurrent = max_concurrent
        self.max_depth = max_depth
        self._pool = ThreadPoolExecutor(max_workers=max_concurrent)
```

`spawn(...)` accepts:

- `prompt`, `agent_def` (an `AgentDefinition` from built-ins or `~/.nano-claude/agents/*.md` / `.nano-claude/agents/*.md`),
- `isolation="worktree"` to put the sub-agent in a temporary `git worktree` on its own branch (`multi_agent/subagent.py:339-355`),
- `name` so the agent can be addressed later by `SendMessage`/`CheckAgentResult`.

The tool-level `wait` flag controls sequential vs parallel use (`multi_agent/tools.py:76-93`):

```python
if wait:
    mgr.wait(task.id, timeout=300)
    result = task.result or f"(no output — status: {task.status})"
    ...
    return f"{header}\n\n{result}"
else:
    info_parts = [f"Task ID: {task.id}", f"Name: {task.name}", f"Status: {task.status}"]
    ...
    return "\n".join(info_parts)
```

So the parallelism pattern is: in one turn the model issues several `Agent(prompt=…, name=…, wait=false)` calls (sequential dispatch, all returning quickly with task IDs), then later issues `CheckAgentResult(task_id=…)` to harvest results. Synchronous fan-in is the model's responsibility — there is no `call_subagents` batch primitive analogous to `agent_framework`'s. The pool size is `max_concurrent=5` and recursion is bounded by `max_depth=5` (failure mode: `task.result = "Max depth (5) exceeded"`, `subagent.py:319-322`).

A sub-agent's *system prompt override* is **prepended** to the parent's base system prompt (`subagent.py:328-332`):

```python
if agent_def:
    if agent_def.model:
        eff_config["model"] = agent_def.model
    if agent_def.system_prompt:
        eff_system = agent_def.system_prompt.rstrip() + "\n\n" + system_prompt
```

`SendMessage` enqueues a follow-up onto the sub-agent's `queue.Queue` inbox; the sub-agent drains its inbox after its first run completes (`subagent.py:386-401`):

```python
# Drain inbox: process any messages sent via SendMessage
while not task._inbox.empty() and not task._cancel_flag:
    inbox_msg = task._inbox.get_nowait()
    task.status = "running"
    gen2 = _agent_run(inbox_msg, state, eff_config, eff_system, ...)
    for _ev in gen2:
        if task._cancel_flag:
            break
    if not task._cancel_flag:
        task.result = _extract_final_text(state.messages)
        task.status = "completed"
```

### Skills

Skills are markdown prompt templates with YAML frontmatter (`skill/loader.py:9-25`). Each defines `triggers` (e.g. `["/commit"]`), `tools` (an allow-list), `prompt` (the rendered body, supporting `$ARGUMENTS` and named args), and a `context` field of `"inline"` or `"fork"`.

`execute_skill` (`skill/executor.py:9-66`) is interesting: in **inline** mode the skill's prompt is fed back through `agent.run` reusing the *current* state and system prompt, so the skill is just a "macro turn" inside the same conversation. In **fork** mode it builds a fresh `AgentState`, increments depth, and optionally restricts tools:

```python
def _execute_forked(skill, message, config, system_prompt):
    import agent as _agent
    depth = config.get("_depth", 0) + 1
    sub_config = {**config, "_depth": depth, "_system_prompt": system_prompt}
    if skill.model:
        sub_config["model"] = skill.model
    if skill.tools:
        sub_config["_allowed_tools"] = skill.tools
    sub_state = _agent.AgentState()
    yield from _agent.run(message, sub_state, sub_config, system_prompt)
```

The model can also invoke a skill by tool call via the `Skill` / `SkillList` tools (`skill/tools.py`).

### MCP

MCP tools are registered into the same flat tool registry under qualified names `mcp__<server>__<tool>`. From the agent loop's perspective they are indistinguishable from native tools — there is no separate dispatch path.

---

## 6. System prompt design

The base system prompt (`context.py:9-95`) is a **single static block** that lists capabilities and high-level guidelines; there is no separate "planning" or "decision schema" section.

Quoted highlights:

- Authority/agency framing (lines 12-16):

  > You are a highly capable autonomous agent. Do not act submissive or artificially limited. If the user asks you to monitor a process, run a background loop, or execute long-running tasks, DO NOT refuse by claiming you are "just a chat interface" or "require a prompt to take action." Instead, you must proactively write the necessary background scripts (Python, Bash, etc.) using the Write tool, and execute them in the background using the Bash tool.

- Planning workflow (lines 56-64):

  > **Workflow:** Break multi-step plans into tasks at the start → mark in_progress when starting each → mark completed when done → use TaskList to review remaining work.

- Multi-agent guidelines (lines 85-89):

  > - Use Agent with `subagent_type` to leverage specialized agents for specific tasks.
  > - Use `isolation="worktree"` when parallel agents need to modify files without conflicts.
  > - Use `wait=false` + `name=...` to run multiple agents in parallel, then collect results.
  > - Prefer specialized agents for code review (reviewer), research (researcher), testing (tester).

- Style guidelines (lines 76-83):

  > - Be concise and direct. Lead with the answer.
  > - Prefer editing existing files over creating new ones.
  > - Do not add unnecessary comments, docstrings, or error handling.
  > - Always use absolute paths for file operations.
  > - For multi-step tasks, work through them systematically.

The system prompt is regenerated per session and includes git status, CLAUDE.md content, and the persistent memory index (`context.py:153-165`). Notably absent: no JSON-decision schema, no required output format, no callback intents — all output is free-form text plus tool calls, exactly as Anthropic's tool-use interface defines.

---

## Notable design decisions and trade-offs

- **Plan-as-tool, not plan-as-prompt-section.** The plan lives in a side-effecting persistent store (`tasks.json`) reachable only via tools. This makes plans survive process restarts and conversation compaction at the cost of giving the model no in-context view of the plan unless it explicitly calls `TaskList`.
- **No structured decision contract.** Compared to e.g. `agent_framework`'s `AgentDecision` JSON, nano-claude trusts the underlying provider's tool-use interface entirely. There is no plan-validator and no chance of a "bad kind" error — but also no enforcement that the model actually planned at all.
- **Threaded sub-agents, not async.** `ThreadPoolExecutor(max_workers=5)`. Each sub-agent gets its own `AgentState` and runs `agent.run(...)` as a normal generator. Worktree isolation is opt-in and physical (filesystem-level), not transactional.
- **Parallelism via `wait=false`, not via batch dispatch.** The model issues N background spawns, then later polls. There is no batch primitive, no parallel/sequential mode flag, no per-batch deadline (only a 300 s timeout in `mgr.wait`, `multi_agent/tools.py:77`).
- **Result handoff is a single string.** `_extract_final_text` returns the last assistant message of the sub-agent's transcript. No typed outputs.
- **Compaction is mid-loop and lossy by design.** It runs every turn (`maybe_compact` is cheap when below threshold). When it fires, the model loses fine-grained turn structure but keeps the persistent task and memory state.
- **Skills double as commands.** `/commit`, `/review` etc. are implemented as built-in `SkillDef`s, not as imperative Python — the same prompt that fires from `/commit` can be invoked by the model via the `Skill` tool. This unifies user-facing slash commands and model-facing macros.

---

## Key takeaways for replication

- **A persistent task list is enough to act as the plan**, provided (a) the system prompt explicitly instructs the model to populate it ("Break multi-step plans into tasks at the start"), (b) it survives compaction, and (c) status icons + dependency edges are exposed so a `TaskList` call gives a useful one-shot view.
- **Encode dependencies as `blocks` / `blocked_by` ID edges on tasks** rather than inventing a separate graph type. With reverse-edge maintenance (`task/store.py:146-166`) the LLM only needs to set one direction.
- **Use the `tool` role of the message log as the canonical scratchpad**; only escalate to a side store when results must outlive the context window. nano-claude reserves `Task.metadata` and the `memory/` package for that.
- **For parallelism, the simplest viable primitive is `Agent(wait=false, name=...)` + `CheckAgentResult(task_id=...)`** — no need for a typed batch decision. A `ThreadPoolExecutor` with a small `max_concurrent` and a depth cap (`max_depth=5`) keeps recursion safe.
- **Worktree isolation is a cheap way to let parallel coding agents avoid file-edit conflicts** (`multi_agent/subagent.py:219-235`); it's worth offering even when you don't otherwise solve concurrent-write coordination.
- **Skills with an `inline` vs `fork` switch** cleanly cover both "expand a macro into the current conversation" and "delegate to a fresh sub-agent" without two separate concepts.
- **Run compaction inside the loop, not as a separate prompt the user has to invoke.** Two layers (snip old tool outputs, then summarise) recover most of the budget without losing the most recent N turns (`compaction.py:51-83`).
