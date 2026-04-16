# Using the agent evaluator

This guide is for **interactive debugging and headless runs** using the **`agent_framework_evaluator`** package shipped alongside `agent_framework`. For authoring agents, tools, and `.env` setup, start with **[Using the agent framework](using-agent-framework.md)**. Architecture reference: [Agent Evaluator & Web Runtime](../architecture/agent-evaluator-web-runtime.md).

---

## 1. What it does

- **Web UI** — local FastAPI app with a three-pane layout: agent/setup/prompt, final JSON result, and a **hierarchical trace** fed by unified **`TraceEvent`** streaming over WebSocket.
- **Headless CLI** — same execution path as the UI (`SessionRunner` + `AgentHost`) without a browser; optional **JSONL** and **LLM trace directory** output.
- **Setup modules** — optional Python files that can register tools, expose prompt templates, and run **`suite_setup` / `test_setup` / `test_teardown` / `suite_teardown`** hooks (see [Setup module contract](../architecture/agent-evaluator-web-runtime.md#9-setup-module-contract)).

Regression **XML/JSON evaluation** (`python -m agent_framework --evaluate …`) is a **different** subsystem; this guide does not cover it.

---

## 2. Installation

Install the framework with **web** dependencies (FastAPI, Uvicorn):

```bash
pip install "agent_framework[web]"
```

For development, `pip install "agent_framework[dev]"` already includes the same web stack.

The console script **`agent-eval`** is equivalent to **`python -m agent_framework_evaluator`**:

```bash
agent-eval web --env .env
```

---

## 3. Configuration (`.env`)

The evaluator uses the **same** `HostConfig` / `.env` as the core runtime ([Host & Orchestration](../architecture/host-orchestration.md#3-configuration-hostconfig)). Minimum expectations:

| Variable | Purpose |
|----------|---------|
| `AGENT_DIRECTORY` | Directory of agent `.md` files |
| `TOOLS_DIRECTORY` | Tool definitions |
| `WORLD_DIRECTORY` | Sandboxed file root for tools |
| `ROOT_AGENT` | Default root agent id |
| `OPENAI_API_KEY` / `DEFAULT_PROVIDER` / `DEFAULT_MODEL` | Or DIAL variables if using DIAL |
| `MISSING_TOOL_POLICY` | `graceful` (default) or `strict` — see [Host & Orchestration](../architecture/host-orchestration.md#3-configuration-hostconfig) |

Paths in `.env` are resolved **relative to the directory containing the env file**.

**Tip:** Start the web server from the directory where your `.env` lives, or pass an absolute `--env` path on the CLI.

---

## 4. Starting the web UI

```bash
python -m agent_framework_evaluator web --env .env --host 127.0.0.1 --port 8123
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--env` | `.env` | Path to environment file |
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `8123` | Port |
| `--open-browser` | off | Open the default browser after start |

Open **`http://127.0.0.1:8123/`** (or your chosen host/port).

On load (and before **Run** if the socket dropped or the server restarted), the page:

1. Calls **`GET /api/agents`** as a lightweight health check and to fill the agent **datalist**.
2. Calls **`POST /api/sessions`** with body **`{}`** — this uses **`env_path: ".env"`** relative to the server process unless you override it in the JSON body. For a different file, use the HTTP API manually or start the server from a cwd where `.env` is correct.
3. Opens a **WebSocket** to **`/ws/{session_id}`** for **live traces** and **outbox** push. **User replies are not tied to the socket:** they are submitted with **`POST /api/sessions/{id}/user-input`** (see §5.3).

---

## 5. Using the UI

### 5.1 Left rail

- **Agent** — type an agent id (autocomplete from catalog). This is the id passed to **`run_agent`**.
- **Setup File** — optional filesystem path to a **`setup.py`** (see §7). When the field **changes**, the UI fetches **`GET /api/setup-template?path=...`** and, if the prompt area is empty, fills it from **`PROMPT_TEMPLATE`** or **`get_prompt_template()`**.
- **Mode** — “Test Set” is reserved; **Single Run** is what the Run button uses.

### 5.2 Prompt and Run

Enter the user instruction in **Prompt**, then **Run**. If the WebSocket is closed (e.g. after a backend restart), the UI **re-establishes** a session: health check → optional close of the old session id → **`POST /api/sessions`** → new **`/ws/{session_id}`**, then the run proceeds.

The client sends a WebSocket message:

```json
{ "type": "run", "agent_id": "<id>", "prompt": "<text>", "setup_path": "<optional path>" }
```

**Response** and **trace** stream back on the same socket. The **Spans** pane groups events by **agent call**: each `runtime.audit.agent_call_started` opens a collapsible frame (collapsed by default) with a **spinner** while the run is in progress; when `runtime.agent_finished` arrives, the spinner is replaced by the **status** (e.g. completed / failed) and a color cue. Child events for that run (LLM, decisions, tools, etc.) are nested inside the frame. **Sub-agents** nest under the parent because the audit payload includes **`parent_run_id`** (the caller’s `run_id`), which keeps the tree correct even if multiple sub-agents are **forked in parallel** and finish out of order. Session-level rows (`runtime.session_started` / `finished`) stay at the root. Use the **channel** checkboxes to show or hide **runtime**, **llm**, **log**, and **user** events in both panes. The **log level** dropdown filters `channel=log` rows with the framework logging levels (`error`, `warning`, `info`, `debug`) and defaults to `warning`. Set it to `debug` before evaluation to show evaluator input, full evaluator LLM prompt, and evaluator result diagnostics in the Trace pane.

### 5.3 Clarifications

When the runtime needs input, **`WebUserCommunication`** enqueues an outbox item (e.g. **`prompt`**, **`question`**, **`confirmation`**, **`permission`**) that includes a **`prompt_id`** (UUID). The **Conversation** pane shows the request; you type in the **Reply** box at the bottom.

**Submit answers over HTTP** (works even if the WebSocket disconnected after the prompt appeared):

```http
POST /api/sessions/{session_id}/user-input
Content-Type: application/json

{ "prompt_id": "<uuid>", "text": "<answer>" }
```

Use **`"text": null`** to cancel that wait if the server still expects the same **`prompt_id`**.

Optionally, the UI can also send a legacy WebSocket message (omit **`prompt_id`** to match the current wait):

```json
{ "type": "user_input", "text": "<answer>", "prompt_id": "<optional uuid>" }
```

### 5.4 Leaving the page

**`beforeunload`** sends **`POST /api/sessions/{id}/close`** with **`keepalive`** so **suite teardown** runs (see architecture doc). The WebSocket **`finally`** path also finalizes the session when the socket disconnects; teardown is **idempotent** per runner.

---

## 6. Headless CLI

```bash
python -m agent_framework_evaluator run \
  --env .env \
  --agent root \
  --prompt "Your instruction"
```

| Flag | Required | Meaning |
|------|----------|---------|
| `--prompt` or `--prompt-file` | one of them | Instruction text |
| `--setup` | no | Path to setup `.py` |
| `--output` | no | Write JSON result to file instead of only stdout |
| `--trace-jsonl` | no | Append all unified trace events to a JSONL file |
| `--trace-llm-dir` | no | Write **`llm`** channel events to per-agent logs under this directory |

Stdout (or **`--output`**) is JSON like:

```json
{
  "status": "completed",
  "message": "..."
}
```

---

## 7. Setup module (optional)

A setup file is a normal Python module loaded from disk. Supported **optional** callables:

| Hook | When |
|------|------|
| `register(host, session_context)` | After host creation; use to register tools or configure host |
| `suite_setup(session_context)` | Once per session/suite scope |
| `test_setup(case_dict, session_context)` | Before each run |
| `test_teardown(case_dict, session_context)` | After each run |
| `suite_teardown(session_context)` | When the session is closed / finalized |

Expose a prompt default via **`PROMPT_TEMPLATE`** (string) and/or **`get_prompt_template()`**.

Full contract: [§9 Setup Module Contract](../architecture/agent-evaluator-web-runtime.md#9-setup-module-contract).

---

## 8. Tracing and logs

- **In the UI** — events are whatever the runner publishes to **`CompositeRuntimeTracer`** (runtime, user, LLM if enabled, **`log`** channel for Python logging when wired). The evaluator attaches the framework log bridge to `agent_framework` and `agent_framework_evaluator` loggers, so regular Python logging records are normalized into structured trace events. Plain `log.record` rows render as compact log lines; structured records with `trace_kind` / `trace_payload` render as expandable trace rows. The span view is **hierarchy-first** (agent-call frames + `parent_run_id`), not a flat `span_id` parent chain.
- **Headless** — use **`--trace-jsonl`** / **`--trace-llm-dir`** on the evaluator CLI.
- **Main framework CLI** — unified JSONL and optional Python logging mirror:

  ```bash
  python -m agent_framework --runtime-trace-jsonl ./logs/run.jsonl --instruction "..."
  python -m agent_framework --runtime-trace-jsonl ./logs/run.jsonl --runtime-trace-python-logs --instruction "..."
  ```

- **Audit JSONL** (`logs/trace-*.jsonl`) and **unified** traces are **separate** pipelines; see [Tracing & Evaluation](../architecture/tracing-evaluation.md).

---

## 9. HTTP API (reference)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Static UI |
| `POST` | `/api/sessions` | Body: `{ "env_path": ".env" }` (optional). Returns `{ "session_id" }`. |
| `POST` | `/api/evaluate-result` | Score the latest/manual result. Body includes `session_id`, `evaluator_prompt`, `agent_message`, and optional `log_level` (`warning` default). |
| `POST` | `/api/evaluate-case` | Score one initializer case. Body includes `session_id`, `initializer`, `case_index`, `agent_message` or `agent_result`, and optional `log_level` (`warning` default). Case markdown may set `result_field` such as `message` or `parameters`. |
| `POST` | `/api/sessions/{id}/close` | Finalize session; **`suite_teardown`** if defined; cancels a pending input wait. |
| `POST` | `/api/sessions/{id}/user-input` | Body: `{ "prompt_id": "<uuid>", "text": "<string or null>" }`. Delivers input for the active wait; **`409`** if nothing is waiting or **`prompt_id`** does not match. |
| `GET` | `/api/agents?env_path=.env` | List agent ids (probe host uses catalog discovery). |
| `GET` | `/api/setup-template?path=` | Safe load of setup module; returns `{ "template": "..." }`. |
| WebSocket | `/ws/{session_id}` | Traces, outbox push, **`run`**; optional **`user_input`** (HTTP preferred for replies). |

---

## 10. Troubleshooting

| Issue | What to check |
|-------|----------------|
| `ModuleNotFoundError: fastapi` / `uvicorn` | Install **`agent_framework[web]`** or **`[dev]`**. |
| Empty agent list | `.env` path, **`AGENT_DIRECTORY`**, run **`GET /api/agents`** with correct **`env_path`**. |
| Run fails immediately | API keys, model id, MCP optional; see server stderr. |
| No trace nodes | Ensure agents use behaviors / hooks that emit activity; **`NullRuntimeTracer`** is not used in evaluator sessions (a composite tracer is always attached). |

---

## 11. Further reading

- [Agent Evaluator & Web Runtime (architecture)](../architecture/agent-evaluator-web-runtime.md)
- [Tracing & Evaluation](../architecture/tracing-evaluation.md)
- [Host & Orchestration](../architecture/host-orchestration.md)
