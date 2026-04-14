# agent_framework

Generic markdown-defined agent runtime, orchestration host, tracing, and evaluator utilities.

---

## Features

- **Markdown-defined agents** — agent behavior, prompts, and parameter contracts live in `.md` files
- **Model-agnostic** — swap LLM providers without modifying agents; ships with `OpenAiModelDriver` and `DialChatCompletionsDriver`
- **Async-ready** — `AsyncModelDriver` protocol with sync/async adapters; async `complete_async()` and `run_tool_loop()` for service workloads
- **Conversation store** — opt-in multi-turn history with `ConversationStore` / `AsyncConversationStore` protocols; `InMemoryConversationStore` reference implementation with TTL
- **Headless invocation** — `AgentHost.complete()` / `complete_async()` for model calls without a markdown agent file
- **Tool-calling loop** — `run_tool_loop()` async helper with terminal tool support for clarification patterns
- **Terminal tools** — declare tool names in agent frontmatter that exit the decision loop immediately without executing
- **Skills** — directory-discovered markdown instruction sets, injected into the model conversation on demand
- **Tracing & audit** — unified **`TraceEvent`** pipeline (`tracing.py`, optional JSONL / debugger subscribers), `InMemoryAuditTracer` (JSONL), `LlmTraceLogger` (console/file)
- **Agent evaluator** — local web UI + WebSocket trace stream (`python -m agent_framework_evaluator`), headless `run` subcommand; see [Using the agent evaluator](docs/guides/using-agent-evaluator.md)
- **Evaluation** — XML-based and conversation-based regression evaluation harnesses (`python -m agent_framework --evaluate …`)
- **JSON validation retry** — `validate_and_retry()` utility for typed, validated model output with one automatic retry

---

## Installation

```bash
# Base (OpenAI driver)
pip install agent_framework

# With DIAL driver (async, OpenAI-compatible chat completions)
pip install "agent_framework[dial]"

# Web UI / evaluator (FastAPI + Uvicorn)
pip install "agent_framework[web]"

# Development
pip install "agent_framework[dev]"
```

---

## Documentation for users

- **[Using the agent framework](docs/guides/using-agent-framework.md)** — authoring agents (Markdown + JSON), behaviors, host modes, tools, skills, MCP, configuration, tracing, and embedding in your apps.

## Quick Start

### Markdown agent (existing workflow)

```python
from agent_framework.host import AgentHost

host = AgentHost.from_env_console(".env")
host.run_console()
```

### Headless invocation (no agent file)

```python
from agent_framework import AgentHost, HostConfig

host = AgentHost.create(model_driver=driver)
result = await host.complete_async(
    messages=[{"role": "user", "content": "Summarize this."}],
    response_mode="text",
)
print(result.raw_text)
```

### DIAL provider

```python
from agent_framework import AgentHost, HostConfig
from agent_framework.drivers.dial import DialChatCompletionsDriver

driver = DialChatCompletionsDriver(
    base_url="https://your-dial.example.com",
    deployment="gpt-4o",
    api_key="your-key",
)
host = AgentHost.create(model_driver=driver)
```

---

## Configuration

### `.env` (OpenAI)

```env
OPENAI_API_KEY=sk-...
DEFAULT_PROVIDER=openai
DEFAULT_MODEL=gpt-4o-mini
AGENT_DIRECTORY=agents
TOOLS_DIRECTORY=tools
WORLD_DIRECTORY=world
ROOT_AGENT=root
```

### `.env` (DIAL)

```env
DEFAULT_PROVIDER=dial
DIAL_BASE_URL=https://your-dial.example.com
DIAL_DEPLOYMENT=gpt-4o
DIAL_API_VERSION=2024-10-21
DIAL_API_KEY=your-api-key
DEFAULT_MODEL=gpt-4o
AGENT_DIRECTORY=agents
TOOLS_DIRECTORY=tools
WORLD_DIRECTORY=world
ROOT_AGENT=root
```

---

## CLI

**Runtime (`agent_framework`):**

```bash
python -m agent_framework --console                            # interactive
python -m agent_framework --instruction "..."                  # one-shot
python -m agent_framework --agent <id> --instruction "..."    # specific agent
python -m agent_framework --evaluate path/to/evaluation.xml   # regression eval
python -m agent_framework --llm-trace console|file|both       # LLM request/response trace
python -m agent_framework --runtime-trace-jsonl path.jsonl ... # unified TraceEvent JSONL
```

**Evaluator (`agent_framework_evaluator`):**

```bash
python -m agent_framework_evaluator web --env .env --port 8123   # local debugger UI
python -m agent_framework_evaluator run --env .env --agent root --prompt "..."  # headless
```

See [Using the agent evaluator](docs/guides/using-agent-evaluator.md) for setup files, trace export, and configuration.

---

## Architecture

See [`docs/architecture/`](docs/architecture/) for the full reference:

- [Overview](docs/architecture/overview.md)
- [Model Abstraction](docs/architecture/model-abstraction.md) — `ModelDriver`, `AsyncModelDriver`, `DriverCapabilities`
- [Host & Orchestration](docs/architecture/host-orchestration.md) — `AgentHost`, headless invocation, conversation store
- [Drivers](docs/architecture/drivers.md) — `OpenAiModelDriver`, `DialChatCompletionsDriver`, custom drivers
- [Conversation Model](docs/architecture/conversation-model.md) — `ConversationStore` protocols and `InMemoryConversationStore`
- [Agent Runtime](docs/architecture/agent-runtime.md) — decision loop, skills, terminal tools

Developer guides:

- [Using the agent framework](docs/guides/using-agent-framework.md) — end-to-end user guide (agents, tools, skills, MCP, config, tracing, projects)
- [Using DIAL](docs/guides/using-dial.md) — complete DIAL integration guide
- [Using the agent evaluator](docs/guides/using-agent-evaluator.md) — web debugger, headless runs, setup modules, trace files

Architecture (evaluator & tracing):

- [Agent Evaluator & Web Runtime](docs/architecture/agent-evaluator-web-runtime.md)
- [Tracing & Evaluation](docs/architecture/tracing-evaluation.md)
