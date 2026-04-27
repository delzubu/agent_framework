# `claude-code` — Planning & Execution Patterns

**Source:** `c:\Users\bmisa\src\claude-code` (TypeScript / Bun, ~3K+ source files; tools under `src/tools/`, planning prompts in `src/utils/messages.ts`, `src/utils/plans.ts`).

---

## Overview

`claude-code` does **not** use a single, monolithic plan-and-execute graph. Instead, planning is layered into the agent's normal tool-use loop via three independent mechanisms that the model chooses between based on system-prompt guidance:

1. **TodoWriteTool / TaskCreateTool** — a lightweight, mutable in-session checklist (the "todo list"). The list lives in `AppState`, keyed by session/agent ID, and is updated by the model itself via tool calls. **This is the primary "plan execution" device** for ordinary multi-step work.
2. **EnterPlanModeTool / ExitPlanModeV2Tool** — a heavier, *modal* "Plan Mode" that gates writes, forces the model into a read-only exploration phase, has it persist a Markdown plan file to disk under `~/.claude/plans/{slug}.md`, and requires explicit user approval before transitioning back to "execute."
3. **AgentTool (sub-agents) + forks** — multi-step plans can be **delegated** to typed sub-agents (e.g. `explore-agent`, `plan-agent`, `code-reviewer`, `verification-agent`) launched in parallel from a single assistant message.

There is no separate "planner" LLM and no DAG. Plans are either (a) a free-form Markdown file on disk, (b) a mutable list-of-strings in process memory, or (c) implicit in the conversation. Re-planning happens by the model issuing a new `TodoWrite` or `FileEdit` to the plan file mid-loop.

The entire architecture is built on a single tool-loop in `src/query.ts` (`runTools` in `src/services/tools/toolOrchestration.ts`) with no special "plan" node — the planning tools are just regular tools whose side effects are `AppState` mutations and a system-reminder attachment.

---

## 1. Plan Generation

Two parallel mechanisms, with the prompt deciding which to use.

### a) TodoWriteTool — proactive checklist

`src/tools/TodoWriteTool/TodoWriteTool.ts:31` — `buildTool({ name: TODO_WRITE_TOOL_NAME, ... })`.
Schema (`src/utils/todo/types.ts:8`):

```ts
export const TodoItemSchema = lazySchema(() =>
  z.object({
    content: z.string().min(1, 'Content cannot be empty'),
    status: z.enum(['pending', 'in_progress', 'completed']),
    activeForm: z.string().min(1, 'Active form cannot be empty'),
  }),
)
```

The model is told to call this proactively. From `src/tools/TodoWriteTool/prompt.ts:3`:

> "Use this tool to create and manage a structured task list for your current coding session. … Use this tool proactively in these scenarios: 1. **Complex multi-step tasks** — When a task requires 3 or more distinct steps … 5. After receiving new instructions — Immediately capture user requirements as todos. 6. When you start working on a task — Mark it as in_progress BEFORE beginning work. Ideally you should only have one todo as in_progress at a time."

Reinforced from the top-level system prompt (`src/constants/prompts.ts:280`):

> "Break down and manage your work with the `${taskToolName}` tool. These tools are helpful for planning your work and helping the user track your progress. Mark each task as completed as soon as you are done with the task. Do not batch up multiple tasks before marking them as completed."

`TaskCreateTool/prompt.ts` is a near-identical alternative that the host swaps in when "agent swarms" are enabled, adding an `owner` field for assigning items to teammates.

### b) Plan Mode — gated, persisted, user-approved plan file

`EnterPlanModeTool` (`src/tools/EnterPlanModeTool/EnterPlanModeTool.ts:36`) takes **no input**. Its only job is to flip `appState.toolPermissionContext.mode` to `'plan'` (line 88), which makes every non-readonly tool call refuse. Its `mapToolResultToToolResultBlockParam` injects:

```
Entered plan mode. You should now focus on exploring the codebase and designing an implementation approach.

In plan mode, you should:
1. Thoroughly explore the codebase to understand existing patterns
2. Identify similar features and architectural approaches
3. Consider multiple approaches and their trade-offs
4. Use AskUserQuestion if you need to clarify the approach
5. Design a concrete implementation strategy
6. When ready, use ExitPlanMode to present your plan for approval

Remember: DO NOT write or edit any files yet. This is a read-only exploration and planning phase.
```
(file:lines `src/tools/EnterPlanModeTool/EnterPlanModeTool.ts:104-118`)

The "should I enter plan mode" decision is itself prompt-driven (`src/tools/EnterPlanModeTool/prompt.ts:23`):

> "Use this tool proactively when you're about to start a non-trivial implementation task. … Use it when ANY of these conditions apply: New Feature Implementation … Multiple Valid Approaches … Code Modifications … Architectural Decisions … Multi-File Changes … Unclear Requirements …"

Once in plan mode, an **attachment message** (`src/utils/attachments.ts:1232`, content built in `src/utils/messages.ts:3207` — `getPlanModeV2Instructions` / `getPlanModeInterviewInstructions`) is injected on each turn telling the model what to do. Two variants:

**5-phase workflow (default):** `src/utils/messages.ts:3227`:

> "Plan mode is active. The user indicated that they do not want you to execute yet -- you MUST NOT make any edits (with the exception of the plan file mentioned below) … This supercedes any other instructions you have received."
>
> "## Plan Workflow
> ### Phase 1: Initial Understanding … Launch up to N `explore-agent` agents IN PARALLEL …
> ### Phase 2: Design … Launch `plan-agent` agent(s) …
> ### Phase 3: Review … Read the critical files identified by agents …
> ### Phase 4: Final Plan … Write your final plan to the plan file (the only file you can edit) …
> ### Phase 5: Call ExitPlanMode."

**Interview workflow** (`getPlanModeInterviewInstructions`, `src/utils/messages.ts:3338`):

> "You are pair-planning with the user. Explore the code to build context, ask the user questions when you hit decisions you can't make alone, and write your findings into the plan file as you go. The plan file (above) is the ONLY file you may edit — it starts as a rough skeleton and gradually becomes the final plan.
>
> ### The Loop
> Repeat this cycle until the plan is complete:
> 1. **Explore** — read code with read-only tools.
> 2. **Update the plan file** — After each discovery, immediately capture what you learned. Don't wait until the end.
> 3. **Ask the user** — When you hit an ambiguity, use AskUserQuestion."

Either way, the plan is a **Markdown file on disk**, slug-named per session: `getPlanFilePath()` (`src/utils/plans.ts:119`) returns `{plansDir}/{wordSlug}.md` (or `{slug}-agent-{agentId}.md` for sub-agents). The plan dir defaults to `~/.claude/plans/`.

---

## 2. Plan Execution

There is no separate executor. The same model that produced the plan executes it inside the same conversation by issuing more tool calls.

* **Todo execution:** the todo list is just `AppState.todos[sessionId|agentId]` — a `TodoItem[]`. Updates are by full-replace from `TodoWriteTool.call` (`src/tools/TodoWriteTool/TodoWriteTool.ts:88-94`):

  ```ts
  context.setAppState(prev => ({
    ...prev,
    todos: { ...prev.todos, [todoKey]: newTodos },
  }))
  ```
  After every call the tool result message is:
  > "Todos have been modified successfully. Ensure that you continue to use the todo list to track your progress. Please proceed with the current tasks if applicable" (line 105).

  The model is expected to call `TodoWrite` again after each completed step. There is no separate "next-step" pointer — the contract "exactly ONE task is `in_progress`" is enforced only by prompt guidance (`prompt.ts:158`), not code.

* **Plan-mode execution:** when the user approves via `ExitPlanModeV2Tool`, the tool result includes the entire approved plan text and tells the model to (optionally) start a todo list:

  `src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts:483`:
  > "User has approved your plan. You can now start coding. Start with updating your todo list if applicable
  >
  > Your plan has been saved to: {filePath}
  > You can refer back to it if needed during implementation.
  >
  > ## Approved Plan:
  > {plan}"

  So the handoff from plan to execution is "drop the plan into the conversation, suggest the model build a todo list from it."

* **Tool batching:** `runTools` in `src/services/tools/toolOrchestration.ts:19` partitions tool-use blocks the model emits in one assistant message into runs of `isConcurrencySafe()` (read-only) calls executed in parallel (default 10) and runs of unsafe calls executed serially. This is how "the plan executes step-by-step in parallel where possible" without a planner DAG — the model emits N tool calls per turn and the orchestrator batches them.

---

## 3. Re-planning Mid-Execution

Re-planning is just *another tool call*, so it happens naturally:

* `TodoWrite` is full-replace: the model can rewrite the entire list at any turn. The prompt explicitly invites this (`src/tools/TodoWriteTool/prompt.ts:165`):
  > "Remove tasks that are no longer relevant from the list entirely … When blocked, create a new task describing what needs to be resolved."

* In plan mode, the model edits the plan file with `FileEditTool`. The `plan_mode` attachment is **re-injected** every N turns (`src/utils/attachments.ts:1196`), with a sparse vs full reminder schedule (`src/utils/messages.ts:3385` — `getPlanModeV2SparseInstructions`), so the planning instructions stay live without thrashing the prompt cache.

* If the model exits plan mode and later re-enters, a `plan_mode_reentry` attachment fires (`src/utils/messages.ts:3829`):
  > "You are returning to plan mode after having previously exited it. A plan file exists at {planFilePath} from your previous planning session. Before proceeding with any new planning, you should: 1. Read the existing plan file … 2. Evaluate the user's current request against that plan 3. Decide how to proceed: Different task → start fresh by overwriting … Same task, continuing → modify the existing plan …"

* There is also a **circuit-breaker** for verification: `TodoWriteTool.call` watches for the model "closing out 3+ tasks with none being a verification step" and appends a structural nudge to the tool result telling it to spawn the verification sub-agent before reporting completion (`TodoWriteTool.ts:76-86, 106-108`).

So re-planning is reactive (model decides) but with prompt-injected nudges and reminders that catch common failure modes.

---

## 4. Result Storage and Reuse

Intermediate results live in three places, in roughly increasing durability:

| Storage | Lifetime | Examples |
|---|---|---|
| Conversation history (assistant + tool_result messages) | Until compaction | Bash/Grep/Read outputs, sub-agent return messages |
| `AppState` in process memory | Session | Todo list (`appState.todos[id]`), permission mode, in-progress tool IDs |
| Disk under `~/.claude/plans/{slug}.md` | Across sessions, recoverable on resume | Plan content; recoverable via file snapshots embedded in the message log (`copyPlanForResume`, `src/utils/plans.ts:164`) |

Sub-agent results return as a **single tool_result text block**, summarized by the sub-agent itself — the parent's context never sees the sub-agent's intermediate tool outputs (that's the explicit point of forks/sub-agents). From `src/tools/AgentTool/prompt.ts:267`:

> "When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result."

Forks use `output_file` paths and a "don't peek" prompt (`AgentTool/prompt.ts:91`):

> "**Don't peek.** The tool result includes an `output_file` path — do not Read or tail it unless the user explicitly asks for a progress check. … Reading the transcript mid-flight pulls the fork's tool noise into your context, which defeats the point of forking."

There is **no symbolic scratchpad** (no "step1_result" variables passed between steps). Either the result is in the conversation, or it's been written to a file the model knows the path of.

---

## 5. Tool / Sub-agent / Skill Use During Execution

* **Tool calls** are the unit of execution. The decision loop is just: model emits `tool_use` blocks → `runTools` (`toolOrchestration.ts:19`) partitions them into safe/unsafe batches → `runToolsConcurrently` runs read-only ones in parallel up to `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` (default 10), `runToolsSerially` runs the rest one-at-a-time.

* **Sub-agents** (`AgentTool`, `src/tools/AgentTool/AgentTool.tsx`) are typed (defined as `.md` files in `src/tools/AgentTool/built-in/` and discoverable via `loadAgentsDir.ts`). Each gets a `subagent_type`, `description`, `prompt`, optional `run_in_background`, optional `isolation: "worktree" | "remote"`, optional `cwd`. They are concurrency-safe (`AgentTool.tsx:1273`), so multiple `Agent` calls in one assistant message run in parallel. Prompt explicitly directs parallel use (`AgentTool/prompt.ts:271`):
  > "If the user specifies that they want you to run agents 'in parallel', you MUST send a single message with multiple Agent tool use content blocks."

* **Forks** (when `isForkSubagentEnabled()`) are sub-agents that **inherit the parent's context** (and prompt cache), launched by omitting `subagent_type`. They are the lightweight "do this exploration without polluting my context" mechanism.

* **Skills** are progressive-disclosure prompt fragments. Listed in a budget-bounded "Skills:" section (`src/tools/SkillTool/prompt.ts:21` — `SKILL_BUDGET_CONTEXT_PERCENT = 0.01` of context, default 8000 chars). Invoked via `SkillTool` which loads the full Markdown into the conversation. There's also a `DiscoverSkills` tool gated behind `EXPERIMENTAL_SKILL_SEARCH` for finding skills on demand mid-conversation (`src/constants/prompts.ts:338`):
  > "Relevant skills are automatically surfaced each turn as 'Skills relevant to your task:' reminders. If you're about to do something those don't cover — a mid-task pivot, an unusual workflow, a multi-step plan — call DiscoverSkills with a specific description of what you're doing."

* **MCP tools** appear as ordinary tools in the registry (qualified with `mcp__<server>__<tool>`); the planning code is agnostic to whether a tool is built-in or MCP-bridged.

---

## 6. System Prompt Design

The relevant fragments (all in `src/constants/prompts.ts` and `src/utils/messages.ts`):

* **Top-level guidance to use the todo tool** (`src/constants/prompts.ts:308`):
  > "Break down and manage your work with the `${taskToolName}` tool. These tools are helpful for planning your work and helping the user track your progress. Mark each task as completed as soon as you are done with the task. Do not batch up multiple tasks before marking them as completed."

* **Top-level parallelism rule** (`src/constants/prompts.ts:310`):
  > "You can call multiple tools in a single response. If you intend to call multiple tools and there are no dependencies between them, make all independent tool calls in parallel. Maximize use of parallel tool calls where possible … However, if some tool calls depend on previous calls to inform dependent values, do NOT call these tools in parallel and instead call them sequentially."

* **Sub-agent guidance** (`src/constants/prompts.ts:319`):
  > "Use the Agent tool with specialized agents when the task at hand matches the agent's description. Subagents are valuable for parallelizing independent queries or for protecting the main context window from excessive results, but they should not be used excessively when not needed. Importantly, avoid duplicating work that subagents are already doing — if you delegate research to a subagent, do not also perform the same searches yourself."

* **Plan mode injection** is *not* in the system prompt — it is a per-turn `system-reminder`-wrapped user message attachment (so it doesn't bust the system-prompt cache). See `src/utils/messages.ts:3294`:
  ```ts
  return wrapMessagesInSystemReminder([
    createUserMessage({ content, isMeta: true }),
  ])
  ```

* **Verification gate** (`src/constants/prompts.ts:394`, ant-only):
  > "The contract: when non-trivial implementation happens on your turn, independent adversarial verification must happen before you report completion … Spawn the Agent tool with subagent_type='verification-agent'. Your own checks, caveats, and a fork's self-checks do NOT substitute — only the verifier assigns a verdict; you cannot self-assign PARTIAL."

* **Communication style during planning/execution** (`src/constants/prompts.ts:422`):
  > "Focus text output on: Decisions that need the user's input · High-level status updates at natural milestones · Errors or blockers that change the plan."

---

## Notable Design Decisions

1. **Plans are not data structures the runtime reasons over.** TodoWrite is "a string list the model rewrites whenever it wants." Plan mode is "a Markdown file the model writes." The runtime never inspects the contents — it only enforces the *mode* (read-only vs. not) and the file-write side effect.
2. **Plan mode = permission mode + attachment.** Plan generation is implemented as a permission-context flip plus a per-turn injected reminder, not a separate state machine. This keeps it composable with the existing tool loop.
3. **Caching-aware injection.** Plan-mode reminders are injected as `system-reminder`-wrapped user messages (not in the system prompt) precisely because the system prompt is `cacheScope: 'global'` and would otherwise bust on every transition. Comments in `src/tools/AgentTool/prompt.ts:60-64` explicitly note "the dynamic agent list was ~10.2% of fleet cache_creation tokens."
4. **Sparse vs. full reminders.** Plan-mode reminders are throttled (`PLAN_MODE_ATTACHMENT_CONFIG`) — full reminder every Nth attachment, sparse one-liners in between. Re-entry, exit, and "you completed 3+ tasks without verifying" all get one-shot specialized attachments.
5. **Sub-agent prompts as directives, not contexts.** The `AgentTool` prompt teaches the model how to *write* sub-agent prompts: "Brief the agent like a smart colleague who just walked into the room … Never delegate understanding."
6. **No retries / no repair.** Tool-call concurrency batching is the only piece of orchestration logic. Everything else — re-planning, error recovery, mid-flight scope changes — is delegated to the model deciding to call `TodoWrite` again.
7. **Persistence is selective.** Only the plan file and conversation history persist across sessions. The todo list is in-process — losing it costs nothing because the model can rebuild it from the conversation.

---

## Key Takeaways for Replication

* **A "plan" can just be a model-managed list of strings in a single tool's input.** No DAG, no executor, no symbolic dependencies — just `[{content, status, activeForm}]` and a strict prompt. This works because LLMs are good at maintaining list state when reminded each turn.
* **Mode-gated planning beats role-based planning.** Rather than a separate "planner agent," `claude-code` flips a permission bit on the *same* model, blocks writes, and injects a per-turn reminder. The agent that plans is the agent that executes — context never has to be transferred.
* **Inject reminders, don't retrofit the system prompt.** Per-turn `system-reminder`-wrapped user messages let you change the active workflow without busting the prompt cache. Throttle them (sparse-vs-full schedule) to keep token cost bounded.
* **Plans on disk + slug-keyed paths give free recovery.** A `~/.claude/plans/{slug}.md` file with a session-cached slug gives session-resume, fork-copy, and "re-entry" semantics nearly for free; recovery from message snapshots covers the case where the file is lost (e.g. remote/CCR sandboxes).
* **Parallelism falls out of "concurrency-safe" tool flags + a single multi-tool-call message.** The model emits N tool calls per turn; the orchestrator partitions into safe-batched-parallel and unsafe-serial runs. No planner is required to discover parallelism — the model proposes it and the runtime exploits it.
* **Sub-agents are scoped context windows, not specialised models.** Each sub-agent gets its own conversation, returns a single text summary, and is prompted as if briefing a "smart colleague who just walked in." Forks (context-inheriting sub-agents) are the cheap variant when you want to outsource exploration without losing prompt-cache hits.
* **Always pair planning with a verification step in the prompt.** The "you closed 3+ tasks without a verification step" nudge fires structurally on the exact tool call where skips happen — a small example of using prompt injection at a chosen moment in the tool-loop, rather than another model.
