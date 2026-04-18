# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Non-negotiable: structured model output

Do **not** add heuristics that reinterpret invalid or non-contract model JSON into valid `AgentDecision` values (for example mapping unknown `kind` strings such as `gather_context` to `call_tool` or `callback`). **Validate strictly** and **fail with a clear error** (`AgentDecision.from_model_response` raises `ValueError` for unsupported `kind`). Fix the problem at the source: agent prompts, `response_format` / JSON mode, or provider configuration — not silent repair in Python.

### Breaking changes (observability)

- All drivers (OpenAI, DIAL, …): non-**text** **`response_mode`** → assistant output must be valid JSON object text → else **`ModelDriverError`** (shared **`parse_json_object_model_output`** in **`model.py`**).
- Decision JSON must include **`kind`**; both **`subagent_id`** and **`tool_name`** set → **`ValueError`**.
- **`run_tool_loop`**: bad tool **`arguments`** JSON → **`ValueError`** after error log.

## Structured model responses — no guessing

When working on this repository:

- **Do not** implement repair logic, fuzzy mapping, or heuristics that turn **invalid** or **non-contract** model JSON into valid `AgentDecision` objects (e.g. unknown `kind` values like `gather_context` must **not** be coerced into `call_tool` / `callback`).
- **Do** enforce the contract: unsupported `kind` → **raise** (`ValueError` from `AgentDecision.from_model_response`) so failures are explicit.
- **Do** fix invalid output upstream: prompts, `response_format` / JSON mode, provider settings — not silent recovery in Python.

This policy is mirrored in `CLAUDE.md` under **Non-negotiable: structured model output**.


## Commands

```bash
# Install (development mode)
pip install -e ".[dev]"

# Install with DIAL driver support
pip install -e ".[dev,dial]"

# Run tests
pytest
pytest tests/test_framework_runtime.py::TestName  # single test

# Run CLI
python -m agent_framework --console                            # interactive
python -m agent_framework --instruction "..."                  # one-shot
python -m agent_framework --agent <id> --instruction "..."    # specific agent
python -m agent_framework --evaluate path/to/evaluation.xml   # regression eval
python -m agent_framework --llm-trace console|file|both       # with LLM tracing
```

## Architecture

This is a **markdown-defined agent runtime**. Agents and tools are defined in Markdown files with YAML frontmatter — their behavior is controlled by the prompts in those files, not just Python code.

Model/LLM layering (merged `ModelContext`, `ModelDriverBase`, ADR): see [`docs/architecture/adr-model-context-and-drivers.md`](docs/architecture/adr-model-context-and-drivers.md).

### Core Concepts

**Agents** (`src/agent_framework/agents/`): Each agent is a `.md` file with:
- YAML frontmatter: `id`, `role`, parameters, model config, `terminal_tools` list
- System prompt instructions
- User prompt template (rendered with invocation parameters)

**Tools** (`tool.py`): Also `.md` files with a Python sibling module. The module must export `build_tool(definition: ToolDefinition) -> Tool`.

**AgentHost** (`host.py`): Central orchestration runtime. Owns the agent registry (`AgentRegistry`), tool registry (`ToolRegistry`), command registry (`CommandRegistry`), model driver, conversation store, call context stack, audit tracer, user communication (`UserCommunication`), and optional MCP manager. All agent invocations flow through the host. Also provides `complete()`, `complete_async()` for headless model calls. Lifecycle: `await host.start()` (discovers registries, starts MCP), `await host.aclose()` (shuts down MCP and async driver). Factory: `AgentHost.create(model_driver=..., builtin_tools=True)` — no `.env` needed. CLI path: `AgentHost.from_env_console(env_path)` — wires `ConsoleUserCommunication` and calls `start()` synchronously.

**File reference injection** (`file_reference.py`): `@filename` or `@"path with spaces.ext"` tokens in any prompt string passed to `AgentHost.run_agent` (and in evaluator case markdown prompt blocks) are automatically expanded to their file contents before the agent sees them. Text files are wrapped in `<file name="...">` tags; binary files are base64-encoded in `<file name="..." encoding="base64">` tags. Unresolvable references (file not found) are left unchanged. Resolver is pluggable via `host.file_ref_resolver` (`FileReferenceResolver` protocol) — override in an initializer's `register(host, ctx)` hook for custom handling (e.g. extracting pptx text). Evaluator `MarkdownCaseLoader` accepts the same `resolver=` keyword; references in case prompts resolve relative to the case file's directory.

**Registries** (`tool_registry.py`, `agent_registry.py`, `command.py`): Formal dataclass registries following the `SkillRegistry` pattern. Each supports `discover()` (eager catalog scan), `get()` (lazy load), `reload()`, and `list_names()`. `ToolRegistry` also accepts `register(tool)` for programmatic registration (builtins, MCP bridges).

**Built-in tools** (`builtin_tools/`): Seven `Tool` subclasses registered programmatically (no `.md` files): `Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`, `WebFetch`. Permission-gated tools (`Write`, `Edit`, `Bash`, `WebFetch`) call `host.user_comm.request_permission()` before acting. Registered by `register_builtin_tools(registry)`. Enabled by default in `AgentHost.create()`.

**Commands** (`command.py`): Parametrized markdown prompts in a configured directory. Claude Code frontmatter format (`description`, `argument-hint`, `allowed-tools`, `model`). `render(cmd, raw_args)` substitutes `$ARGUMENTS` and `$1`–`$9`. Unknown commands dispatch to a `_command_fallback` callable on the host. Execute via `await host.execute_command(name, raw_args) -> str | None`.

**UserCommunication** (`user_communication.py`, `console_communication.py`): Async Protocol replacing the old `input_reader`/`output_writer` callables. Key methods: `send_message`, `read_user_input`, `request_permission`, `ask_confirmation`. `NullUserCommunication` — no-op (default for `AgentHost.create()`). `ConsoleUserCommunication` — `asyncio.to_thread`-based console I/O with `[y/n/a/d]` permission prompts and session-level allow/deny caching.

**MCP** (`mcp/`): Client-only MCP integration. `McpManager` connects to stdio/HTTP MCP servers (`start_all()`, `stop_all()`), then `bridge_mcp_tools()` registers each MCP tool as a `McpBridgeTool` in `ToolRegistry`. Config via `.mcp.json` (auto-discovered from cwd upward) or `MCP_CONFIG_PATH`. Qualified tool names: `mcp__<server>__<tool>`.

**Skills** (`skills_directories`): Markdown-defined instruction sets discovered from one or more configured directories. Each skill is a `.md` file with YAML frontmatter (`id`, `description`, `priority`). The skills catalog (names + descriptions) is injected as a first-turn conversation message (`{"role": "user"}` at index 2) with priority-based truncation to stay within the `SKILLS_CATALOG_MAX_TOKENS` budget — lower-priority skills are dropped first. When a model emits an `invoke_skill` decision, `handle_skill_invocation()` injects `Base directory: <path>` and a `<skill_files>` file list directly into the conversation (no resource tool required). Configure with `SKILLS_DIRECTORY`/`SKILLS_DIRECTORIES` and `SKILLS_CATALOG_MAX_TOKENS`.

**Errors** (`errors.py`): `ModelDriverError` (structured HTTP error with `status_code` and `upstream_body`) and `ConversationNotFoundError` (raised when a conversation_id is not found in the store).

**Messages** (`messages.py`): Typed multimodal chat message model — `ChatMessage`, `ContentPart`, `ImageUrl`, `FunctionCall`, `ToolCallMessage`. All frozen dataclasses with `to_dict()` / `from_dict()` round-trip. Does NOT change `ModelContext.messages` type (stays `tuple[dict, ...]`).

**Conversation store** (`conversation.py`): `ConversationStore` and `AsyncConversationStore` are `typing.Protocol` classes for storage-agnostic multi-turn conversation history. `InMemoryConversationStore` is the reference implementation with optional TTL and thread-safety.

**Validation** (`validation.py`): `_normalize_json_text(raw)` strips markdown fences — private primitive used by `model.py` drivers and `parse_json_object_model_output`. No public parsing API; structured output correctness is enforced upstream via `response_format` / JSON mode, not Python-side retries.

**Drivers** (`drivers/`): Optional provider drivers. `DialChatCompletionsDriver` (async, DIAL/OpenAI-compatible chat completions) requires `[dial]` extra. Uses `aidial-sdk` for typed request construction.

### Decision Loop

Each agent runs a loop: call model → parse `AgentDecision` → act → repeat.

Decision kinds (closed set; any other top-level `kind` after alias normalization is invalid):
- `final_message` — agent is done, returns `AgentResult`
- `call_tool` — invoke a registered tool, add result to context
- `call_subagent` — delegate to a single child agent via `host.call_subagent`
- `call_subagents` — dispatch a batch of child agents (parallel or sequential) via `host.call_subagent_batch`; each entry specifies `subagent_id`, `parameters`, `output_key`
- `callback` — escalate to caller (human or parent agent)
- `invoke_skill` — invoke a named skill from the catalog

Top-level callback **intent** names (e.g. `information_request`) are normalized to `kind: callback` per `agents/agent_decision.py`.

**Terminal tools** (`terminal_tools` in frontmatter): Tool names that exit the loop immediately when called, without executing the tool. The result is `AgentResult(status="completed", message=json.dumps(tool_args))`. Useful for clarification/escalation exit points.

Callback intents: `information_request`, `proposal_review`, `execution_recovery`, `delegation_return`, `policy_or_approval`, `guardrail_trip`

### Response Modes

System prompt templates in `agents/` control output format:
- `system.md` — base template (tools, subagents, workflow)
- `system.decision.md` — structured action JSON (default)
- `system.text.md` — plain text responses
- `system.json_object.md` — arbitrary JSON with callback patterns

### Configuration (`.env`)

```
# OpenAI
OPENAI_API_KEY=...
DEFAULT_PROVIDER=openai
DEFAULT_MODEL=gpt-4o-mini          # comma-separated list for fallback: gpt-4o,gpt-4o-mini

# DIAL (alternative to OpenAI)
DEFAULT_PROVIDER=dial
DIAL_BASE_URL=https://your-dial.example.com
DIAL_API_VERSION=2024-10-21
DIAL_API_KEY=...

# Shared
AGENT_DIRECTORY=path/to/agents
TOOLS_DIRECTORY=path/to/tools
WORLD_DIRECTORY=path/to/sandbox
ROOT_AGENT=<agent_id>
AGENT_MODELS=agent1=m1,m2|agent2=m3   # | separates agents, , separates models per agent
SKILLS_DIRECTORY=path/to/skills        # single skills directory
SKILLS_DIRECTORIES=path/a,path/b       # multiple skills directories
SKILLS_CATALOG_MAX_TOKENS=2000         # max tokens for skills catalog injected into conversation
MISSING_TOOL_POLICY=graceful            # graceful = skip unloadable tools + trace; strict = fail run

# Commands
COMMANDS_DIRECTORY=path/to/commands    # single commands directory
COMMANDS_DIRECTORIES=path/a,path/b     # multiple commands directories (comma-separated)

# MCP
MCP_CONFIG_PATH=path/to/mcp.json       # explicit .mcp.json path (default: auto-discover)
MCP_ENABLED=true                        # set false to disable MCP entirely

# Parallel sub-agents
SUBAGENT_BATCH_TIMEOUT_SECONDS=300     # wall-clock deadline per call_subagents batch (default: 300)
SUBAGENT_MAX_PARALLELISM=8             # max entries per call_subagents decision (default: 8)
SUBAGENT_BATCH_MAX_CALLBACK_ROUNDS=5   # max callback-resolve-resume rounds per batch (default: 5)
```

### Extensibility

**`AgentBehavior`** (`agent_behavior.py`): Subclass to customize agent execution:
- `before_run()` — after parameter binding, before main loop
- `respond_to_callback()` — handle callbacks from subagents
- `after_run()` — post-execution, can override result

**Event/hook system**: `SequentialHook` fires typed lifecycle events (`AgentStartEvent`, `ModelStartEvent`, `ToolStartEvent`, etc.) that behaviors can observe.

**`DriverCapabilities`**: Drivers declare capabilities via `ClassVar[DriverCapabilities]` with flags `is_async`, `supports_multimodal`, `supports_response_format`, `supports_tools`, `supports_streaming`. Query with `get_driver_capabilities(driver)`.

**`AsyncModelDriver`**: Protocol for async drivers. `SyncToAsyncAdapter` and `AsyncToSyncAdapter` bridge sync/async worlds. `AgentHost.get_model_driver()` auto-wraps async drivers for the sync agent loop.

### Agent Evaluator (`agent_framework_evaluator`)

Three CLI subcommands (all use `python -m agent_framework_evaluator <cmd>`):
- `web` — starts the local FastAPI UI with WebSocket trace streaming
- `run` — headless single-agent invocation
- `evaluate` — runs and evaluates one or all cases from an initializer (or a standalone `.md` case file) entirely CLI-side; no web UI required. Key flags: `--initializer`, `--case N`, `--case-file`, `--output`, `--verbose`, `--agent`

All evaluation orchestration is **server-side**:
- `result_field` selection from `last_run_result` — both in `/api/evaluate-result` and `/api/evaluate-case`. Returns HTTP 400 (or CLI exit 1) if the field is missing.
- `case_run_mode: no_callbacks` postfix applied server-side in the WS `run` handler (constant `CASE_NO_CALLBACKS_POSTFIX` in `evaluation.py`).
- Batch iteration in `/api/evaluate-batch` (NDJSON streaming) and `evaluate` CLI — not client-side.
- `SessionRecord.last_run_result` stores the payload dict from `_agent_result_payload(result)` after each run; the evaluate endpoints read from it. **Do not re-introduce client-side agent result forwarding.**

Key evaluator files: `app.py` (FastAPI endpoints + WS handler), `session_manager.py` (`SessionRecord`), `runtime/session_runner.py` (`run_once`), `evaluation.py` (`run_evaluation`, `select_agent_result_field`, `CASE_NO_CALLBACKS_POSTFIX`), `cli.py` (subcommands including `evaluate`), `web/app.js` (thin observer — no payload extraction, no batch loop, no postfix).

### Tracing & Audit

- `InMemoryAuditTracer` captures immutable `AgentCallAuditRecord` per run
- JSONL dumps go to `logs/`
- `trace_viewer.html` — standalone HTML viewer for trace files
- LLM-level request/response logging via `--llm-trace`
- Both `OpenAiModelDriver` and `DialChatCompletionsDriver` use `ProviderRequestTrace` / `ProviderResponseTrace` callbacks — wire via `host.enable_audit_trace()` or `host.enable_llm_trace_logging()`
