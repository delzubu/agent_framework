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

## Packages

Each package has its own `CLAUDE.md` with the source tree, workflow pseudocode, and rules relevant to that area.

| Package | Path | Purpose |
|---|---|---|
| `agent_framework` | `src/agent_framework/` | Core runtime — host, agents, model drivers, tracing |
| `agent_framework_evaluator` | `src/agent_framework_evaluator/` | Web UI + CLI for running and evaluating agents |
| `agent_framework_skills` | `src/agent_framework_skills/` | Pre-built skill pack installer (`agent-framework-skills` CLI) |
| `agent_workflow_compiler` | `src/agent_workflow_compiler/` | Compiles planning-agent audit logs into deterministic agents |

## Development workflow

**Branching.** Major new features are implemented on a dedicated feature branch. Bug fixes are committed directly to the active branch (normally `master` once a prior PR is merged, unless stated otherwise).

**Committing.** Commit after each self-contained piece of work is done — not at the end of everything.

**Pushing.** Push only when a full feature is complete. A feature is complete when all three of the following are true:
1. All coding is done.
2. All tests pass (`pytest`).
3. All documentation is updated — this means: architecture notes (in `docs/` and the relevant `CLAUDE.md` files), user-facing documentation (in `docs/pages/`), and any skill knowledge files that describe new or changed behavior.

## Architectural standards

**Agent definition files are the source of truth for behavior.** Python code implements mechanics; `.md` files define what an agent does, what tools it can use, and how it interprets its inputs. When changing agent behavior, change the prompt first.

**Strict model output — no silent repair.** Do not coerce unknown `kind` values or malformed JSON into valid decisions. `AgentDecision.from_model_response` raises `ValueError` for unsupported `kind`; let it. Fix the problem at the source: prompts, `response_format`, or provider config.

**Tracing is additive and optional.** Publish new lifecycle events via `agent_events` (`agent_event_publisher.py`) using `make_trace_event`. Never make correctness depend on a trace event being received. Subscribers are fire-and-forget.

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

## Reference frameworks

Consult when asked to "research similar frameworks" or "design a framework-agnostic solution":

Agentic: LangGraph, LangChain, Microsoft Agent Framework, Google ADK, CrewAI, AutoGen, DSPy, LlamaIndex, Haystack, SuperAGI, OpenDevin

SDKs: OpenAI SDK, Anthropic SDK, Google GenAI SDK, Azure AI SDK, EPAM DIAL, LiteLLM, Vercel AI SDK, HuggingFace Transformers, MCP
