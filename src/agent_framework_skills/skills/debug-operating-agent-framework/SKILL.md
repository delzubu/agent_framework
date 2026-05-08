---
name: debug-operating-agent-framework
description: |
  Debugging guide for agent_framework operational issues.
  Use when the model provider, .env configuration, MCP server wiring,
  or CLI invocation is misbehaving.
version: "1.0"
priority: 0
---

# debug-operating-agent-framework skill

Use this skill when debugging configuration, model/provider errors, MCP tool registration, or CLI problems.

## How to use this skill

| Reference | When to load |
|-----------|-------------|
| `references/known-issues-operating.md` | When you have a symptom — provider errors, model output format issues, .env misconfiguration, MCP tools not found, CLI flags |
| `references/trace-jsonl.md` | Before reading or querying a `.jsonl` audit log |
| `tools/parse_log.py` | Import or run to extract structured data from a `.jsonl` log |

## Related skills

- For the full configuration reference: load **operating-agent-framework** skill → `references/env-reference.md`
- For agent definition issues: load **debug-authoring-agents** skill
- For host integration issues: load **debug-embedding-agent-framework** skill

## Base directory

The `references/` and `tools/` folders are in the same directory as this file.

## Debugging workflow

### 1. Check the provider configuration

Look for `llm.error` events in the trace log. If no `llm.request` event exists at all, the model driver failed to initialize — check `DEFAULT_PROVIDER`, `OPENAI_API_KEY`, `DIAL_BASE_URL`, and `DIAL_API_KEY` in `.env`.

### 2. Check model output format

`llm.response.raw_text` in the log shows the exact model output before parsing. If the framework raises `ValueError: unsupported kind`, the model returned a JSON object with an unrecognised `kind` value — fix the system prompt or add `response_format` enforcement.

### 3. Check MCP tools

If a tool the agent tries to call is missing, check `MCP_CONFIG_PATH` and `MCP_ENABLED=true` in `.env`. The MCP manager logs connection errors to `llm.error` or to the Python logger.

### 4. Check CLI flags

For `python -m agent_framework`: `--agent` must match the `id:` field in the agent's `.md` frontmatter. `--env` must point to a readable `.env` file. Load `operating-agent-framework` → `references/cli-usage.md` for all flags.
