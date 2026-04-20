# Purpose

`agent_framework.host` is the main runtime boundary for applications that embed or run markdown-defined agents.

The module brings together the pieces that are intentionally kept separate elsewhere in the package: agents, tools, skills, model drivers, conversation storage, user communication, callbacks, tracing, and optional MCP integration.

Use this module when you need to run an agent, call a model through the framework, or host a tool-calling loop without building the orchestration plumbing yourself.

## Usage Pattern

Most applications start with one of two host construction paths:

- `AgentHost.from_env(...)` or `AgentHost.from_env_console(...)` when the runtime should be configured from environment variables and project directories.
- `AgentHost.create(...)` when an application wants to supply dependencies directly, usually for tests, services, or embedded use.

Once created, a host is responsible for discovering registries, managing lifecycle, and running agent or model calls.
