---
name: embedding-agent-framework
description: |
  Guide for hosting the agent_framework inside a Python program.
  Use when running the AgentHost, wiring memory or context, routing
  messages and callbacks, or invoking sub-agents from host code.
---

# embedding-agent-framework skill

This skill covers embedding the `agent_framework` runtime in a Python application — the host layer, memory wiring, sub-agent dispatch from Python, and callback routing.

## How to use this skill

| Reference | When to load |
|-----------|-------------|
| `references/framework-embedding.md` | Before writing host code — AgentHost setup, running agents, sub-agent dispatch |
| `references/memory-usage.md` | When the host must wire memory, pass `mem://` refs, or use memory tools |
| `references/callback-routing.md` | When routing callback decisions in host code — what each kind means to the host |

## Quick orientation

- **AgentHost** runs agents; configure it with provider, agent directory, tool directory
- **Memory** uses a URI model (`mem://scope/key`); the host exposes memory tools to agents
- **Callbacks** surface as `AgentResult` with `status="waiting"` — inspect `callback_intent`
- **Sub-agents** can be invoked from Python via `AgentHost.run()` with a parent run context

## Base directory

The `references/` folder is in the same directory as this file.
