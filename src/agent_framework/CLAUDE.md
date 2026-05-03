# agent_framework — core runtime

## Source tree

```
agent_framework/
├── host.py                       AgentHost — central orchestrator; owns all registries,
│                                 model driver, conversation store, tracer, MCP manager.
│                                 Entry points: AgentHost.create(), .from_env_console()
├── agent.py                      Re-exports Agent and friends for the public API
├── agent_registry.py             Discovers and lazily loads agent .md files
├── tool.py                       Tool base class and ToolDefinition loader
├── tool_registry.py              Discovers tool .md/.py pairs; accepts programmatic register()
├── command.py                    CommandRegistry; renders $ARGUMENTS/$1-$9 in .md prompts
├── model.py                      ModelDriver/AsyncModelDriver protocols, ModelContext,
│                                 ModelResponse, CapabilityDefinition, DriverCapabilities,
│                                 assemble_system_prompt, parse_json_object_model_output
├── model_overrides.py            Per-agent model/temperature overrides from AGENT_MODELS env
├── model_validation.py           JSON-schema validation for structured model output
├── config.py                     HostConfig + load_host_config (reads .env)
├── conversation.py               ConversationStore / AsyncConversationStore protocols;
│                                 InMemoryConversationStore reference implementation
├── messages.py                   Typed multimodal message model (ChatMessage, ContentPart, …)
├── errors.py                     ModelDriverError, ConversationNotFoundError
├── validation.py                 _normalize_json_text — strips markdown fences from model output
├── memory.py                     Memory tool support (auto-store threshold, normalization)
├── memory_tools.py               Built-in memory read/write tools
├── skill.py                      SkillRegistry; injects skills catalog into conversation
├── file_reference.py             @filename token expansion in prompts
├── audit_trace.py                InMemoryAuditTracer, AuditTraceSubscriber, JSONL serialization
├── agent_event_publisher.py      Singleton agent_events; publishes lifecycle trace events
├── runtime_trace_behavior.py     AgentBehavior that fires runtime.agent_started / _finished /
│                                 runtime.parameters_bound audit events
├── tracing.py                    TraceEvent, TraceContext, make_trace_event, NullRuntimeTracer
├── tracing_bridge.py             get_active_tracer() context-var accessor
├── tracing_consumers/            Log handler that bridges Python logging into trace events
├── tracing_subscribers/          JSONL file subscriber; LLM trace file subscriber
├── llm_trace_logging.py          LLM request/response trace wiring
├── trace_logging.py              Structured trace logging utilities
├── usage_tracking.py             Per-run LLM token usage accumulation
├── user_communication.py         UserCommunication async protocol (send, read, permissions)
├── console_communication.py      ConsoleUserCommunication — asyncio.to_thread console I/O
├── web_communication.py          WebSocket-backed UserCommunication for the evaluator UI
├── web_host.py                   AgentHost subclass wired for the evaluator web server
├── interaction.py                Interaction helpers for multi-turn conversations
├── evaluator.py                  Legacy evaluator entry point (XML cases)
├── __main__.py                   CLI entry point (--console, --instruction, --evaluate, …)
│
├── agents/                       Agent class and per-run state — see agents/CLAUDE.md
├── builtin_tools/                Seven Tool subclasses registered by register_builtin_tools()
│                                 read/write/edit/bash/glob/grep/web_fetch — no .md files needed
├── drivers/
│   ├── openai.py                 OpenAiModelDriver (sync, default)
│   └── dial.py                   DialChatCompletionsDriver (async, requires [dial] extra)
├── mcp/                          Client-only MCP integration
│   ├── manager.py                McpManager — connects stdio/HTTP servers, bridges tools
│   ├── client.py                 Low-level MCP client
│   ├── config.py                 .mcp.json discovery and parsing
│   ├── tools.py                  McpBridgeTool — wraps an MCP tool as a framework Tool
│   └── types.py                  MCP type definitions
└── planning/                     Planning agent support — see planning/CLAUDE.md
```

## Standard agent decision loop

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

## Rules

**`AgentHostProtocol` breaks the circular import.** `agents/` code refers to the host through `AgentHostProtocol` (a structural Protocol in `agent_host_protocol.py`), not through `host.py` directly. Maintain this separation — do not import `host.py` from inside `agents/`.

**Parameter values flow through `run.parameter_values`, not ad-hoc.** `refresh_parameter_state` is the single place that extracts and validates parameter values from the rendered prompt + seed parameters. Call it; do not read `run.rendered_prompt` directly to parse values.

**`runtime.parameters_bound` is the authoritative parameter snapshot for log consumers** (including the workflow compiler). `runtime.agent_started.parameters` only captures seed params — values injected by `on_pre_agent` hooks are absent. `runtime.parameters_bound` fires after all pre-run hooks.
