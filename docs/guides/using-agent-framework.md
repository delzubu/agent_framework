# Using the agent framework

**Who this is for:** You want to run LLM “agents” with tools and sub-agents, but you would rather **edit Markdown and small Python modules** than wire everything in code. This guide walks you from a first agent file to a host you can embed in your own app.

**What this framework is:** A **runtime** that loads agents from **Markdown contracts** (prompts + allow-lists), talks to **OpenAI- or DIAL-style** model APIs, runs **tools** and **child agents**, optional **skills** and **MCP** bridges, and records what happened through **tracing** and optional **audit** logs. You stay in control of prompts and boundaries; the framework handles the decision loop, registries, and I/O.

**What it is not:** A hosted product, a training stack, or a replacement for reading your provider’s API docs. Deep protocol detail lives in [`docs/architecture/`](../architecture/overview.md); here we focus on *how to work with the framework day to day*.

**Prerequisites:** Python 3.11+, a provider key (e.g. OpenAI), and patience to try one small example before scaling up.

---

## How this guide is organized

Each chapter answers a different question you will ask at a different moment. Read in order the first time; later, jump to the chapter that matches where you are stuck.

**§1 — Create and run agents.**  
*Purpose:* Get from zero to a running agent without understanding the whole stack. *You will learn* what a single `.md` file must contain (the three `---` sections), how parameters and templates fit together, and the shortest CLI path to talk to your agent. *After it,* you should be able to create `agents/root.md`, point `.env` at it, and run `python -m agent_framework --console` with confidence.

**§2 — Extend agents.**  
*Purpose:* Separate “what the model sees” from “how we run it.” *You will learn* why **sidecar JSON** sits beside the Markdown (models, temperature, behaviors) and how a small **Python behavior** file can hook tool calls or startup without forking the framework. *After it,* you will know when to edit `.md` vs `.json` vs `behaviors/*.py`.

**§3 — Host options.**  
*Purpose:* Clarify where “the program” lives. *You will learn* that **`AgentHost`** is the engine (registries, driver, user I/O), while **console** and **browser** are just different ways to feed it input. *After it,* you will not expect an HTTP server inside the core package—and you will know when to use `from_env_console`, `create_web_host`, or the separate **evaluator** app.

**§4 — Skills and MCP.**  
*Purpose:* Add knowledge and external systems without stuffing everything into one prompt. *You will learn* skills as **folder-based** instructions the model can invoke by name, and MCP as **plug-in tools** from configured servers. *After it,* you will know what to put in `skills/` and `.mcp.json`, and how allow-lists on the agent keep the model safe.

**§5 — Adding tools.**  
*Purpose:* Give the model real actions (files, commands, APIs) in a repeatable way. *You will learn* the **`.md` + `.py`** tool pair, built-ins you get for free, and what happens when a tool is missing (`MISSING_TOOL_POLICY`). *After it,* you can add a custom tool and list it in the agent frontmatter.

**§6 — Configuration.**  
*Purpose:* One place to describe your world (paths, models, keys). *You will learn* the **`.env`** layout the host expects and how to override models per agent. *After it,* you can move a project between machines by editing env, not code.

**§7 — Tracing and debugging.**  
*Purpose:* See failures and slow paths without guessing. *You will learn* the difference between **unified trace events** (good for the evaluator and JSONL), **LLM request logs**, and **audit JSONL**—and which flag or subscriber turns each on. *After it,* you can reproduce a bug with a trace file or the local evaluator UI.

**§8 — Building your own projects.**  
*Purpose:* Go from “demo folder” to “library inside my service.” *You will learn* a sane package layout, calling **`run_agent`** vs **`complete_async`** for pipelines, and where to look for test patterns. *After it,* you can embed the host in FastAPI, scripts, or batch jobs without fighting the lifecycle.

---

## 1. Create and run agents

Think of one **`.md` file per agent**. The file is cut into **three parts** by lines that contain only `---`:

1. **YAML frontmatter** — identity (`id`, `role`, `description`), **parameters** the user must supply, and **allow-lists**: `tools`, `subagents`, `skills`, optional `terminal_tools`.
2. **System prompt** — stable instructions (persona, safety, how to use tools).
3. **User prompt template** — what you send each run; use `{{parameter_name}}` only for names declared under `parameters:`.

If you omit a section, loading fails with a clear error—this is intentional so broken agents never silently run.

**Minimal layout:**

```text
project/.env
project/agents/root.md    # id should match ROOT_AGENT in .env
project/tools/            # optional
project/world/            # optional sandbox for file tools
```

**Run interactively:**

```bash
pip install agent_framework   # add [dial] or [web] as needed
python -m agent_framework --console --env .env
```

**Run once:**

```bash
python -m agent_framework --env .env --instruction "Your task here."
python -m agent_framework --env .env --agent other_id --instruction "…"
```

The model normally returns **JSON decisions** (`final_message`, `call_tool`, `call_subagent`, `invoke_skill`, `callback`, …). You do not hand-author that JSON—the bundled system templates teach the model the shape; your job is the Markdown contract and allow-lists.

---

## 2. Extend agents (JSON + Python)

**Sidecar `root.json` next to `root.md`** holds *runtime* tuning: `model`, `temperature`, `provider`, and `behaviors` (or a single `behavior`). Keep the **LLM-facing contract** in the `.md` frontmatter; keep **deployment choices** in JSON so you can swap models without rewriting prompts.

**Behaviors** are small Python modules that export `build_behavior() -> AgentBehavior`. In `attach()`, subscribe to hooks like `onPreTool`. Resolution: same folder as the agent (`agents/foo.py` for `agents/foo.md`), else `behaviors/name.py` **next to the parent of the agents folder** (e.g. project root if agents live under `agents/`). Use this when you need cross-cutting logic (logging, guardrails) instead of pasting it into every system prompt.

---

## 3. Host options

**`AgentHost`** is the center of gravity: tool and agent registries, model driver, optional MCP, tracing. It does **not** include an HTTP server.

- **Console:** `AgentHost.from_env_console(".env")` — familiar for local dev and the main CLI.
- **Headless:** `AgentHost.create(..., user_comm=NullUserCommunication())` — scripts and servers that supply input programmatically.
- **Browser / debugger:** use **`create_web_host`** plus your own transport, or install **`agent_framework[web]`** and run the **evaluator** ([separate guide](using-agent-evaluator.md)), which already wires WebSocket + trace streaming.

If you are unsure, start with console; move to null comm or the evaluator when you need automation or a UI.

---

## 4. Skills and MCP

**Skills** bundle reusable instructions in `skills/<name>/SKILL.md` (with frontmatter). The runtime injects a **catalog** so the model knows names and descriptions, then loads full content when the model chooses **`invoke_skill`**. List allowed names in the agent frontmatter under `skills:`—this is your safety boundary.

**MCP** connects external tool servers. Configuration is merged from `MCP_CONFIG_PATH`, a walking search for `.mcp.json`, then `~/.agent_framework/mcp.json`. After `await host.start()`, tools appear with qualified names like `mcp__server__tool`; add only the ones you trust to the agent’s `tools:` list. Set `MCP_ENABLED=false` to turn everything off.

---

## 5. Adding tools

**Built-ins** (registered by default): Read, Write, Edit, Bash, Glob, Grep, WebFetch—some ask the user before risky actions.

**Custom tools:** pair `tools/my_tool.md` (definition + docs) with `tools/my_tool.py` exporting `build_tool(definition) -> Tool` and an `invoke(self, arguments, host) -> str` implementation. The tool **id** in Markdown must match the filename and the string in the agent’s `tools:` list.

If an agent references a tool that cannot be loaded, **`MISSING_TOOL_POLICY`** in `.env` chooses **`graceful`** (skip, log, emit a trace event, continue) or **`strict`** (fail fast). Default is graceful so one missing file does not kill the whole session—especially helpful in the evaluator.

---

## 6. Configuration

**`.env`** next to your project (paths are resolved relative to the env file’s directory) typically sets: API keys, `DEFAULT_PROVIDER`, `DEFAULT_MODEL` (comma-separated fallbacks), `AGENT_DIRECTORY`, `TOOLS_DIRECTORY`, `WORLD_DIRECTORY`, `ROOT_AGENT`, optional `AGENT_MODELS`, skills directories, commands directories, MCP path/flags, and `MISSING_TOOL_POLICY`.

For DIAL, install the extra and set `DIAL_*` variables; see [Using DIAL](using-dial.md). In code, `HostConfig` + `AgentHost.create` is the same information without a file.

---

## 7. Tracing and debugging

You have **three complementary views**, not one switch:

- **Unified tracer** — structured `TraceEvent` stream (runtime, user, llm, system). Attach subscribers (e.g. JSONL) or use **`--runtime-trace-jsonl`** on the CLI. The **evaluator** subscribes for the live tree.
- **LLM trace** — raw request/response logging for prompt debugging: **`--llm-trace`**.
- **Audit JSONL** — richer per-run records under `logs/` when enabled from env-based hosts; great for the bundled trace viewer, separate from the unified event schema.

When something fails, turn on **one** channel first (usually unified JSONL or the evaluator), reproduce once, then read the error payload—tool load failures and execution errors both emit **error-level** runtime events when a tracer is attached.

---

## 8. Building your own projects

Declare **`agent_framework`** as a normal dependency. Keep `.env.example` in repo; load real `.env` locally. For **orchestration**, give a parent agent a `subagents:` list and child `.md` files—the host resolves and runs them with proper caller context.

For **pure API pipelines** (no Markdown agent), use **`complete_async`** or **`run_tool_loop`** on the same host so you share drivers and optional conversation stores. Test with **`builtin_tools=False`**, fake drivers, or `RecordingAgentHost` from `evaluator.py` as in the repository tests.

When you outgrow this guide, the architecture docs spell out protocols and hook order—but you should already know *which* document to open for your next question.

---

## Quick CLI reminder

```bash
python -m agent_framework --console --env .env
python -m agent_framework --env .env --instruction "…" [--agent ID]
python -m agent_framework --runtime-trace-jsonl ./run.jsonl --instruction "…"
python -m agent_framework --evaluate path/to/suite.xml
```

Evaluator (separate entry point): `python -m agent_framework_evaluator web --env .env`
