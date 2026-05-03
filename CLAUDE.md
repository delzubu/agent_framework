# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A flexible, standalone agent runtime for Python developers who want to embed LLM agents in their software. Agents and tools are defined in Markdown files with YAML frontmatter; their behavior is controlled by the prompts in those files, not just Python code.

## Commands

```bash
# Install (development mode)
pip install -e ".[dev]"
pip install -e ".[dev,dial]"     # include DIAL driver

# Run tests
pytest
pytest tests/test_framework_runtime.py::TestName   # single test

# CLI
python -m agent_framework --console
python -m agent_framework --instruction "..." --agent <id> --llm-trace console|file|both

# Evaluator
python -m agent_framework_evaluator web
python -m agent_framework_evaluator run --env .env --agent <id> --prompt "..."
python -m agent_framework_evaluator evaluate --initializer <path> --case-file <path> --verbose

# Workflow compiler
compile-workflow compile --log <audit.jsonl> --agent-id <new_id> \
    --output-dir <dir> --source-agent-path <agent.md>
```

## Source tree

```
src/
├── agent_framework/                  Core runtime package
│   ├── host.py                       AgentHost — central orchestrator; owns all registries,
│   │                                 model driver, conversation store, tracer, MCP manager.
│   │                                 Entry points: AgentHost.create(), .from_env_console()
│   ├── agent.py                      Re-exports Agent and friends for the public API
│   ├── agent_registry.py             Discovers and lazily loads agent .md files
│   ├── tool.py                       Tool base class and ToolDefinition loader
│   ├── tool_registry.py              Discovers tool .md/.py pairs; accepts programmatic register()
│   ├── command.py                    CommandRegistry; renders $ARGUMENTS/$1-$9 in .md prompts
│   ├── model.py                      ModelDriver/AsyncModelDriver protocols, ModelContext,
│   │                                 ModelResponse, CapabilityDefinition, DriverCapabilities,
│   │                                 assemble_system_prompt, parse_json_object_model_output
│   ├── model_overrides.py            Per-agent model/temperature overrides from AGENT_MODELS env
│   ├── model_validation.py           JSON-schema validation for structured model output
│   ├── config.py                     HostConfig + load_host_config (reads .env)
│   ├── conversation.py               ConversationStore / AsyncConversationStore protocols;
│   │                                 InMemoryConversationStore reference implementation
│   ├── messages.py                   Typed multimodal message model (ChatMessage, ContentPart, …)
│   ├── errors.py                     ModelDriverError, ConversationNotFoundError
│   ├── validation.py                 _normalize_json_text — strips markdown fences from model output
│   ├── memory.py                     Memory tool support (auto-store threshold, normalization)
│   ├── memory_tools.py               Built-in memory read/write tools
│   ├── skill.py                      SkillRegistry; injects skills catalog into conversation
│   ├── file_reference.py             @filename token expansion in prompts
│   ├── audit_trace.py                InMemoryAuditTracer, AuditTraceSubscriber, JSONL serialization
│   ├── agent_event_publisher.py      Singleton agent_events; publishes lifecycle trace events
│   ├── runtime_trace_behavior.py     AgentBehavior that fires runtime.agent_started / _finished /
│   │                                 runtime.parameters_bound audit events
│   ├── tracing.py                    TraceEvent, TraceContext, make_trace_event, NullRuntimeTracer
│   ├── tracing_bridge.py             get_active_tracer() context-var accessor
│   ├── tracing_consumers/            Log handler that bridges Python logging into trace events
│   ├── tracing_subscribers/          JSONL file subscriber; LLM trace file subscriber
│   ├── llm_trace_logging.py          LLM request/response trace wiring
│   ├── trace_logging.py              Structured trace logging utilities
│   ├── usage_tracking.py             Per-run LLM token usage accumulation
│   ├── user_communication.py         UserCommunication async protocol (send, read, permissions)
│   ├── console_communication.py      ConsoleUserCommunication — asyncio.to_thread console I/O
│   ├── web_communication.py          WebSocket-backed UserCommunication for the evaluator UI
│   ├── web_host.py                   AgentHost subclass wired for the evaluator web server
│   ├── interaction.py                Interaction helpers for multi-turn conversations
│   ├── evaluator.py                  Legacy evaluator entry point (XML cases)
│   ├── __main__.py                   CLI entry point (--console, --instruction, --evaluate, …)
│   │
│   ├── agents/                       Agent class and all per-run data structures
│   │   ├── agent.py                  Agent — loaded from .md; owns run(), execute_programmatic_workflow()
│   │   ├── agent_run.py              AgentRun — mutable per-invocation state (prompt, parameters,
│   │   │                             history, fragments, missing_parameters, …)
│   │   ├── agent_decision.py         AgentDecision — parsed from model JSON; SubagentCallSpec
│   │   ├── agent_parameter.py        AgentParameter spec (name, type, required, default, pattern)
│   │   ├── agent_behavior.py         AgentBehavior base class (before_run, after_run, respond_to_callback)
│   │   ├── agent_hook_decision.py    AgentHookDecision — returned by before_run to short-circuit
│   │   ├── agent_invocation.py       AgentInvocation — snapshot passed to on_pre_agent hooks
│   │   ├── agent_result.py           AgentResult(status, message, response, prompt)
│   │   ├── agent_host_protocol.py    AgentHostProtocol — structural Protocol used inside agents
│   │   │                             to avoid circular imports with host.py
│   │   ├── turn_driver.py            TurnDriver protocol + StandardTurnDriver (single model call
│   │   │                             → dispatch loop per outer iteration)
│   │   ├── workflow.py               ProgrammaticWorkflow + all step types + WorkflowMutation types
│   │   ├── helpers.py                load_runtime_metadata (reads .json sidecar), split_markdown_sections,
│   │   │                             extract_prompt_value, apply_runtime_placeholders, coerce_parameter_value
│   │   ├── call_context.py           CallContext stack (run_id chain for nested agents)
│   │   ├── sequential_hook.py        SequentialHook — typed event bus for lifecycle callbacks
│   │   ├── result_envelope.py        ResultEnvelope for subagent batch results
│   │   ├── subagent_hook_decision.py Decision returned by respond_to_callback
│   │   └── *_event.py / *_hook_decision.py   Typed event and hook-decision dataclasses for each
│   │                                          lifecycle point (model, tool, subagent, skill, end)
│   │
│   ├── agents/system*.md             System prompt templates injected by model.py:
│   │   ├── system.md                 Base — tools + subagent catalog
│   │   ├── system.decision.md        Structured JSON decision format (default)
│   │   ├── system.text.md            Plain text response mode
│   │   ├── system.json_object.md     Arbitrary JSON output mode
│   │   └── system.plan_execute.md    Extended instructions for PlanningTurnDriver agents
│   │
│   ├── builtin_tools/                Seven Tool subclasses registered programmatically by
│   │   │                             register_builtin_tools(registry). No .md files needed.
│   │   └── read/write/edit/bash/glob/grep/web_fetch tools
│   │
│   ├── drivers/
│   │   ├── openai.py                 OpenAiModelDriver (sync, default)
│   │   └── dial.py                   DialChatCompletionsDriver (async, requires [dial] extra)
│   │
│   ├── mcp/                          Client-only MCP integration
│   │   ├── manager.py                McpManager — connects stdio/HTTP servers, bridges tools
│   │   ├── client.py                 Low-level MCP client
│   │   ├── config.py                 .mcp.json discovery and parsing
│   │   ├── tools.py                  McpBridgeTool — wraps an MCP tool as a framework Tool
│   │   └── types.py                  MCP type definitions
│   │
│   └── planning/                     Planning agent support
│       ├── turn_driver.py            PlanningTurnDriver — PLAN/EXECUTE/REFLECT state machine
│       ├── plan_state.py             PlanState — tracks step status, results, token refs
│       ├── step_reference.py         StepReferenceResolver — resolves {{step_id.path}} tokens
│       └── config.py                 PlanningConfig dataclass (max_steps, parallel_execution, …)
│
├── agent_framework_evaluator/        Web UI + CLI for running and evaluating agents
│   ├── app.py                        FastAPI app — REST endpoints + WebSocket run handler
│   ├── cli.py                        Entry point: web / run / evaluate subcommands
│   ├── session_manager.py            SessionRecord — stores run state, last_run_result
│   ├── evaluation.py                 run_evaluation, select_agent_result_field,
│   │                                 CASE_NO_CALLBACKS_POSTFIX
│   ├── case_markdown.py              MarkdownCaseLoader — parses .md case files, resolves @refs
│   ├── initializer_catalog.py        Discovers initializer .py files by convention
│   ├── auto_user_reply.py            Automated callback responses for headless evaluation
│   ├── runtime/
│   │   ├── session_runner.py         run_once — core single-run executor
│   │   ├── runner_host.py            AgentHost subclass for evaluator runs
│   │   ├── setup_loader.py           Loads initializer register()/setup() hooks
│   │   └── debug_subscriber.py       Trace subscriber for evaluator debug output
│   └── web/                          Static frontend (JS/HTML) — thin WebSocket observer only
│
├── agent_framework_skills/           Pre-built skill pack installer
│   ├── cli.py                        agent-framework-skills CLI
│   └── installer.py                  Copies skill files into configured SKILLS_DIRECTORY
│
└── agent_workflow_compiler/          Compiles planning-agent audit logs into deterministic agents
    ├── cli.py                        compile-workflow CLI entry point
    ├── log_reader.py                 JSONL → ordered list[AuditEvent]
    ├── plan_extractor.py             extract_plan() → PlanCompilation from AuditEvents
    ├── models.py                     PlanCompilation, CompiledStep, ReplanCheckpoint, AuditEvent
    └── emitter/
        ├── markdown.py               Emits <id>.md (agent definition)
        ├── json_def.py               Emits <id>.json (sidecar) and <id>.workflow.json
        ├── behavior.py               Emits <id>.py (AgentBehavior + ProgrammaticWorkflow)
        └── _tokens.py                Token detection, _value_to_python_expr,
                                      find_invocation_param, infer_param_ref
```

## Coding and architectural standards

**Agent definition files are the source of truth for behavior.** Python code implements mechanics; `.md` files define what an agent does, what tools it can use, and how it interprets its inputs. When changing agent behavior, change the prompt first.

**Runtime metadata belongs in the `.json` sidecar, not `.md` YAML.** The `behavior`, `model`, `temperature`, `planning`, and provider fields are read from `<agent>.json` by `helpers.load_runtime_metadata`. The `.md` frontmatter holds only the agent definition fields (`id`, `role`, `parameters`, `tools`, `subagents`, `terminal_tools`).

**Three `---` section structure for agent files.** `helpers.split_markdown_sections` expects exactly three `---` delimiters: frontmatter / system prompt / user prompt template. The user template is rendered with `{{param_name}}` substitution before the LLM sees it.

**Strict model output — no silent repair.** Do not coerce unknown `kind` values or malformed JSON into valid decisions. `AgentDecision.from_model_response` raises `ValueError` for unsupported `kind`; let it. Fix the problem at the source: prompts, `response_format`, or provider config.

**`AgentHostProtocol` breaks the circular import.** `agents/` code refers to the host through `AgentHostProtocol` (a structural Protocol in `agent_host_protocol.py`), not through `host.py` directly. Maintain this separation — do not import `host.py` from inside `agents/`.

**Parameter values flow through `run.parameter_values`, not ad-hoc.** `refresh_parameter_state` is the single place that extracts and validates parameter values from the rendered prompt + seed parameters. Call it; do not read `run.rendered_prompt` directly to parse values.

**Tracing is additive and optional.** Publish new lifecycle events via `agent_events` (`agent_event_publisher.py`) using `make_trace_event`. Never make correctness depend on a trace event being received. Subscribers are fire-and-forget.

**`runtime.parameters_bound` is the authoritative parameter snapshot for log consumers** (including the workflow compiler). `runtime.agent_started.parameters` only captures seed params — values injected by `on_pre_agent` hooks are absent. `runtime.parameters_bound` fires after all pre-run hooks.

**Workflow step parameter values are lambdas, not string tokens.** `ProgrammaticWorkflow` steps accept `dict[str, Any] | WorkflowValueResolver`. Use `lambda s: _ref(s, ...)` to defer resolution to runtime. `resolve_workflow_value` recurses into dicts so per-key lambdas work. Do not introduce `{{token}}` strings at the workflow runtime layer — that is a compiler concern only.

**Evaluator orchestration is server-side.** The JS frontend is a thin WebSocket observer. Do not re-introduce client-side result forwarding, batch loops, or `no_callbacks` postfix injection into `web/app.js`.

## Workflows

### Standard agent decision loop

```
AgentHost.run_agent(agent_id, prompt, parameters)
  └─ Agent.run(host, run, caller_id)
       ├─ refresh_parameter_state(run)          # extract params from rendered prompt
       ├─ build_context(host, run)              # assemble system prompt + tool/subagent catalog
       ├─ audit_agent_call_started(...)         # emit full rendered prompts
       ├─ _run_pre_agent_hooks(...)
       │    ├─ AgentBehavior.before_run(...)    # behaviors run first (can short-circuit)
       │    └─ on_pre_agent callbacks           # old-style hooks (can inject system_message fragments)
       ├─ refresh_parameter_state(run)          # second pass — picks up hook-injected fragments
       ├─ audit_parameters_bound(...)           # emit complete parameter snapshot
       └─ while should_continue(run):
            TurnDriver.run_turn(agent, host, run, caller_id)
              └─ StandardTurnDriver:
                   ├─ refresh_parameter_state(run)
                   ├─ build_incremental_context(...)
                   ├─ model_driver.decide(context)     # LLM call → raw JSON
                   └─ AgentDecision dispatch:
                        call_tool     → execute tool, inject result, continue
                        call_subagent → host.call_subagent, inject result, continue
                        call_subagents→ host.call_subagent_batch (parallel/sequential)
                        invoke_skill  → inject skill content, continue
                        callback      → AgentBehavior.respond_to_callback or bubble to caller
                        final_message → return AgentResult, exit loop
```

### Planning agent loop (PlanningTurnDriver)

Activated when an agent's `.json` sidecar has `"planning": {"enabled": true}`. The outer `Agent.run` loop is unchanged; `PlanningTurnDriver` replaces `StandardTurnDriver` for each iteration.

```
run_turn phases (one phase per outer loop iteration):

  PLAN phase     — no plan in PlanState yet
    model call → expect submit_plan decision
    validate step graph (no cycles, token refs resolvable)
    emit runtime.audit.named_event {type: plan_updated, is_initial: true}
    transition to EXECUTE

  EXECUTE phase  — ready batch (steps with satisfied deps) available
    _select_ready_batch(plan_state) → batch of parallel-ready steps
    _dispatch_parallel_batch(batch) → ThreadPoolExecutor for parallel steps
      each step: call_tool or call_subagent, store result in plan_state
    _resolve_step_parameters with {{step_id.path}} token substitution
    inject reminder (completed steps + next ready batch) into run context
    if all done → transition to REFLECT with end_of_plan=True

  REFLECT phase  — no ready steps (waiting on model)
    model call → expect continue_plan or final_message
    continue_plan: update plan, emit plan_updated (is_initial: false), back to EXECUTE
    final_message: return AgentResult
```

Key files: `planning/turn_driver.py`, `planning/plan_state.py`, `planning/step_reference.py`, `planning/config.py`.

### Programmatic workflow (compiled agents)

Used by agents compiled from planning logs. `AgentBehavior.before_run` short-circuits the model loop entirely.

```
AgentBehavior.before_run(agent, host, run, caller_id)
  └─ agent.execute_programmatic_workflow(host, run, caller_id, workflow, initial_parameters)
       ├─ refresh_parameter_state(run)   # validate required params; raise ValueError if missing
       ├─ ProgrammaticWorkflowState(initial_parameters=run.parameter_values)
       └─ while step_id != None:
            step = workflow.steps[step_id]
            dispatch by type:
              WorkflowCallToolStep    → host.execute_tool(tool_name, resolve(arguments, state))
              WorkflowCallSubagentStep→ host.call_subagent(subagent_id, resolve(parameters, state))
              WorkflowCallSubagentsStep→ host.call_subagent_batch(calls, mode, timeout)
              WorkflowInvokeSkillStep → host.invoke_skill(skill_name, resolve(parameters, state))
              WorkflowBranchStep      → evaluate condition(state) → then_step or else_step
              WorkflowReturnStep      → return coerce_workflow_result(resolve(value, state))
              WorkflowRaiseStep       → raise WorkflowAbortedError
            state.step_results[step.step_id] = result
            if workflow.on_step_end:
              mutation = on_step_end(step_id, result, state, workflow)
              WorkflowContinue  → advance to step.next_step
              WorkflowGoto      → jump to mutation.step_id
              WorkflowReplace   → swap workflow, restart at new entry_step
              WorkflowAbort     → raise WorkflowAbortedError(reason)
```

`_ref(state, step_id, *path)` — runtime helper inlined in generated behavior files. Resolves `state.step_results[step_id]` first, then falls back to `state.initial_parameters[step_id]`. Path segments traverse dicts, lists (by numeric index), and object attributes.

### Workflow compilation pipeline

```
compile-workflow compile --log audit.jsonl --agent-id <id> ...
  └─ log_reader.read_events(log)          → list[AuditEvent]
  └─ plan_extractor.extract_plan(events)  → PlanCompilation
       ├─ filter to planner run_id
       ├─ invocation_parameters from runtime.parameters_bound (fallback: runtime.agent_started)
       ├─ final plan = last plan_updated event's plan array
       ├─ topological sort → CompiledStep list with next_step pointers
       ├─ replan checkpoints from non-initial plan_updated events
       └─ step_results from runtime.agent_finished events (subagents only)
  └─ emitters (all three run for every compile):
       markdown.py  → <id>.md
       json_def.py  → <id>.json (behavior pointer) + <id>.workflow.json (human-readable)
       behavior.py  → <id>.py  (AgentBehavior + _build_workflow + _on_step_end stubs)

Parameter resolution in emitted code (priority order per literal value):
  1. {{token}} string     → _value_to_python_expr → lambda s: _ref(s, step_id, *path)
  2. matches invocation param by value → lambda s: _ref(s, 'param_name')  [# Bound from agent parameter]
  3. replan step + found in step_results → lambda s: _ref(s, step_id, *path)  [# Parameter inferred: ...]
  4. plain literal
```

## Configuration (`.env`)

```
DEFAULT_PROVIDER=openai        # or: dial
OPENAI_API_KEY=...
DEFAULT_MODEL=gpt-4o-mini      # comma-separated for fallback chain

DIAL_BASE_URL=...
DIAL_API_VERSION=2024-10-21
DIAL_API_KEY=...

AGENT_DIRECTORY=path/to/agents
TOOLS_DIRECTORY=path/to/tools
ROOT_AGENT=<agent_id>
AGENT_MODELS=agent1=m1,m2|agent2=m3   # per-agent model overrides

SKILLS_DIRECTORY=path/to/skills        # or SKILLS_DIRECTORIES=path/a,path/b
SKILLS_CATALOG_MAX_TOKENS=2000
COMMANDS_DIRECTORY=path/to/commands
MCP_CONFIG_PATH=path/to/.mcp.json     # default: auto-discover upward
MCP_ENABLED=true
MISSING_TOOL_POLICY=graceful          # graceful = skip + trace; strict = fail run

SUBAGENT_BATCH_TIMEOUT_SECONDS=300
SUBAGENT_MAX_PARALLELISM=8
SUBAGENT_BATCH_MAX_CALLBACK_ROUNDS=5
```

## Documentation

Public docs live in `docs/pages/` (deployed via GitHub Pages). Architecture docs in `docs/`. Information architecture: `docs/pages-information-architecture.md`. Do **not** push to the GitHub wiki.

## Reference frameworks

Consult when asked to "research similar frameworks" or "design a framework-agnostic solution":

Agentic: LangGraph, LangChain, Microsoft Agent Framework, Google ADK, CrewAI, AutoGen, DSPy, LlamaIndex, Haystack, SuperAGI, OpenDevin

SDKs: OpenAI SDK, Anthropic SDK, Google GenAI SDK, Azure AI SDK, EPAM DIAL, LiteLLM, Vercel AI SDK, HuggingFace Transformers, MCP
