# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (development mode)
pip install -e ".[dev]"

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

### Core Concepts

**Agents** (`src/agent_framework/agents/`): Each agent is a `.md` file with:
- YAML frontmatter: `id`, `role`, parameters, model config
- System prompt instructions
- User prompt template (rendered with invocation parameters)

**Tools** (`tool.py`): Also `.md` files with a Python sibling module for implementation.

**AgentHost** (`host.py`): Central orchestration runtime. Owns the agent registry, tool registry, model driver, call context stack, audit tracer, and I/O. All agent invocations flow through the host.

### Decision Loop

Each agent runs a loop: call model → parse `AgentDecision` → act → repeat.

Decision kinds:
- `final_message` — agent is done, returns `AgentResult`
- `call_tool` — invoke a registered tool, add result to context
- `call_subagent` — delegate to a child agent via `host.call_subagent`
- `callback` — escalate to caller (human or parent agent)

Callback intents: `information_request`, `proposal_review`, `execution_recovery`, `delegation_return`, `policy_or_approval`, `guardrail_trip`

### Response Modes

System prompt templates in `agents/` control output format:
- `system.md` — base template (tools, subagents, workflow)
- `system.decision.md` — structured action JSON (default)
- `system.text.md` — plain text responses
- `system.json_object.md` — arbitrary JSON with callback patterns

### Configuration (`.env`)

```
OPENAI_API_KEY=...
DEFAULT_PROVIDER=openai
DEFAULT_MODEL=gpt-4o-mini
AGENT_DIRECTORY=path/to/agents
TOOLS_DIRECTORY=path/to/tools
WORLD_DIRECTORY=path/to/sandbox
ROOT_AGENT=<agent_id>
AGENT_MODELS=agent_id:model,...   # per-agent overrides
```

### Extensibility

**`AgentBehavior`** (`agent_behavior.py`): Subclass to customize agent execution:
- `before_run()` — after parameter binding, before main loop
- `respond_to_callback()` — handle callbacks from subagents
- `after_run()` — post-execution, can override result

**Event/hook system**: `SequentialHook` fires typed lifecycle events (`AgentStartEvent`, `ModelStartEvent`, `ToolStartEvent`, etc.) that behaviors can observe.

### Tracing & Audit

- `InMemoryAuditTracer` captures immutable `AgentCallAuditRecord` per run
- JSONL dumps go to `logs/`
- `trace_viewer.html` — standalone HTML viewer for trace files
- LLM-level request/response logging via `--llm-trace`
