# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A flexible, standalone agent runtime for Python developers who want to embed LLM agents in their software. Agents and tools are defined in Markdown files; their behavior is controlled by the prompts in those files, not just Python code.

Four installable packages live under `src/`:
- `agent_framework` — core runtime (agent loop, host, tools, drivers)
- `agent_framework_evaluator` — web UI + CLI for running and evaluating agents
- `agent_framework_skills` — CLI installer for pre-built skill packs
- `agent_workflow_compiler` — compiles planning-agent audit logs into deterministic workflow agents

## Commands

```bash
# Install (development mode)
pip install -e ".[dev]"
pip install -e ".[dev,dial]"     # include DIAL driver

# Run tests
pytest
pytest tests/test_framework_runtime.py::TestName   # single test

# CLI
python -m agent_framework --console                            # interactive
python -m agent_framework --instruction "..."                  # one-shot
python -m agent_framework --agent <id> --instruction "..."    # specific agent
python -m agent_framework --llm-trace console|file|both       # with LLM tracing

# Evaluator
python -m agent_framework_evaluator web                        # local web UI
python -m agent_framework_evaluator run --env .env --agent <id> --prompt "..."
python -m agent_framework_evaluator evaluate --initializer <path> --case-file <path> --verbose

# Workflow compiler
compile-workflow compile --log <audit.jsonl> --agent-id <new_id> --output-dir <dir> \
    --source-agent-path <agent.md>
```

## Non-negotiable: structured model output

Do **not** add heuristics that reinterpret invalid or non-contract model JSON into valid `AgentDecision` values (e.g. mapping unknown `kind` strings like `gather_context` to `call_tool`). **Validate strictly and fail with a clear error** — `AgentDecision.from_model_response` raises `ValueError` for unsupported `kind`. Fix invalid output upstream: agent prompts, `response_format` / JSON mode, or provider configuration — not silent repair in Python.

- Non-text `response_mode` → output must be valid JSON object → else `ModelDriverError` (via `parse_json_object_model_output` in `model.py`).
- Decision JSON must include `kind`; both `subagent_id` and `tool_name` set → `ValueError`.
- Bad tool `arguments` JSON in `run_tool_loop` → `ValueError` after error log.

## Architecture

### Agent definition format

Each agent is a `.md` file with three `---`-delimited sections:
1. YAML frontmatter: `id`, `role`, parameters, model config, `terminal_tools`, `tools`, `subagents`
2. System prompt
3. User prompt template (rendered with invocation parameters via `{{param_name}}`)

Runtime metadata (`behavior`, `model`, `temperature`, etc.) lives in a sibling `.json` sidecar file — NOT in the `.md` frontmatter. The framework reads the `.json` first, falling back to `.md` YAML for legacy fields. The `planning:` config block belongs in the `.json` sidecar.

### Core execution flow

`AgentHost.run_agent` → `Agent.run()` → parameter binding (`refresh_parameter_state`) → pre-run hooks (`_run_pre_agent_hooks`, including `AgentBehavior.before_run` then `on_pre_agent` callbacks) → post-hook parameter refresh → model decision loop (`TurnDriver.run_turn`) → `AgentDecision` dispatch → repeat.

Key files: `host.py`, `agents/agent.py`, `agents/agent_decision.py`, `agents/turn_driver.py`.

### Decision loop

Decision kinds (closed set):
- `final_message` — done, returns `AgentResult`
- `call_tool` — invoke registered tool
- `call_subagent` — delegate to child agent
- `call_subagents` — parallel/sequential batch of child agents
- `callback` — escalate to caller with an intent (`information_request`, `proposal_review`, `execution_recovery`, `delegation_return`, `policy_or_approval`, `guardrail_trip`)
- `invoke_skill` — inject skill content into conversation

**Terminal tools** (`terminal_tools` in frontmatter): exit the loop immediately when called, returning `AgentResult(status="completed", message=json.dumps(tool_args))`.

### Programmatic workflows

`Agent.execute_programmatic_workflow` runs a `ProgrammaticWorkflow` — a dict of typed step objects — without calling the model. Used by compiled agents to run deterministic plans.

Step types: `WorkflowCallToolStep`, `WorkflowCallSubagentStep`, `WorkflowCallSubagentsStep`, `WorkflowInvokeSkillStep`, `WorkflowReturnStep`, `WorkflowBranchStep`, `WorkflowRaiseStep`.

Parameter values can be lambdas `(state: ProgrammaticWorkflowState) -> Any`; `resolve_workflow_value` recursively resolves these (including per-key lambdas inside dicts). `state.initial_parameters` holds the agent's invocation parameters; `state.step_results` holds per-step outputs.

`ProgrammaticWorkflow.on_step_end` callback fires after each step and returns a `WorkflowMutation`: `WorkflowContinue` (default), `WorkflowGoto`, `WorkflowReplace`, or `WorkflowAbort`. This is the replan-checkpoint hook used by compiled agents.

### AgentBehavior

Subclass `AgentBehavior` (`agent_behavior.py`) to customize execution:
- `before_run()` — after pre-agent hooks, before the model loop. Return `AgentHookDecision(final_result=...)` to short-circuit (used by compiled workflow agents).
- `respond_to_callback()` — handle callbacks from subagents.
- `after_run()` — post-execution, can override result.

The `on_pre_agent` hook (old-style, fires AFTER `before_run` behaviors) can inject `system_message` fragments via `AgentHookDecision`. These fragments are picked up by the subsequent `refresh_parameter_state` call, so parameter values injected via hooks are available in `state.initial_parameters` at workflow start.

### Audit events and tracing

Events are published via `agent_events` (`agent_event_publisher.py`) and captured by `InMemoryAuditTracer` → JSONL in `logs/`. Key event kinds:

- `runtime.agent_started` — fires in `before_run`; `parameters` = seed params only (before hook injection)
- `runtime.parameters_bound` — fires after all pre-run hooks; `bound_parameters` = fully resolved param snapshot (including hook-injected values). **Compilers and log consumers should prefer this over `runtime.agent_started.parameters`.**
- `runtime.audit.agent_call_started` — full rendered system/user prompt
- `runtime.audit.named_event` with `type: plan_updated` — planning agent plan snapshots
- `runtime.agent_finished` — subagent result payload

### Workflow compiler (`agent_workflow_compiler`)

Compiles a planning-agent JSONL audit log into three files:
- `<id>.md` — agent definition (system prompt describes the workflow)
- `<id>.json` — sidecar wiring `behavior` to the generated Python module
- `<id>.py` — `AgentBehavior` subclass that builds and runs a `ProgrammaticWorkflow`

Compilation pipeline: `log_reader` → `plan_extractor.extract_plan` → `PlanCompilation` → `emitter/{markdown,json_def,behavior}`.

Parameter resolution in emitted code (priority order):
1. `{{token}}` strings in plan parameters → `lambda s: _ref(s, ...)` via `_value_to_python_expr`
2. Literal values matching a known invocation parameter → `lambda s: _ref(s, 'param_name')` via `find_invocation_param`
3. Replan-introduced literals found in step results → `lambda s: _ref(s, step_id, ...)` via `infer_param_ref` (with an explanatory comment listing all candidate paths)
4. Plain literal

The `_ref(state, step_id, *path)` helper (inlined in generated code) resolves `state.step_results[step_id]` first, then falls back to `state.initial_parameters[step_id]` — so parameter references and step-result references share the same lookup syntax.

Replan checkpoints appear as stubs in `_on_step_end`; edit them to activate `WorkflowGoto` / `WorkflowReplace` rerouting.

### Response modes

System prompt templates (`agents/` in the package): `system.md` (base), `system.decision.md` (structured JSON, default), `system.text.md` (plain text), `system.json_object.md` (arbitrary JSON).

### Configuration (`.env`)

```
DEFAULT_PROVIDER=openai            # or: dial
OPENAI_API_KEY=...
DEFAULT_MODEL=gpt-4o-mini          # comma-separated for fallback

DIAL_BASE_URL=...
DIAL_API_VERSION=2024-10-21
DIAL_API_KEY=...

AGENT_DIRECTORY=path/to/agents
TOOLS_DIRECTORY=path/to/tools
ROOT_AGENT=<agent_id>
AGENT_MODELS=agent1=m1,m2|agent2=m3

SKILLS_DIRECTORY=path/to/skills
SKILLS_DIRECTORIES=path/a,path/b
SKILLS_CATALOG_MAX_TOKENS=2000

COMMANDS_DIRECTORY=path/to/commands
MCP_CONFIG_PATH=path/to/.mcp.json  # default: auto-discover upward
MCP_ENABLED=true

SUBAGENT_BATCH_TIMEOUT_SECONDS=300
SUBAGENT_MAX_PARALLELISM=8
SUBAGENT_BATCH_MAX_CALLBACK_ROUNDS=5
MISSING_TOOL_POLICY=graceful        # graceful = skip + trace; strict = fail
```

### Evaluator (`agent_framework_evaluator`)

CLI: `python -m agent_framework_evaluator <web|run|evaluate>`. The `evaluate` subcommand runs cases headless. All evaluation orchestration is **server-side** — the JS frontend is a thin observer; do not re-introduce client-side result forwarding, batch loops, or postfix injection.

Key files: `app.py` (FastAPI + WS), `session_manager.py`, `runtime/session_runner.py`, `evaluation.py`, `cli.py`.

### Documentation

Public docs: `docs/pages/` (deployed via GitHub Pages). Developer/architecture docs: `docs/`. Information architecture: `docs/pages-information-architecture.md`. Do **not** push to the GitHub wiki for routine edits.

## Reference frameworks

Consult these when asked to "research similar frameworks" or "design a framework-agnostic solution":

- LangGraph, LangChain, Microsoft Agent Framework, Google ADK, CrewAI, AutoGen, DSPy, LlamaIndex, Haystack
- OpenAI SDK, Anthropic SDK, Google GenAI SDK, Azure AI SDK, EPAM DIAL, LiteLLM, HuggingFace Transformers, MCP
