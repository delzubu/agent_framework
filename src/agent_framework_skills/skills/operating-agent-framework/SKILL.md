---
name: operating-agent-framework
description: |
  Guide for configuring and operating an agent_framework deployment.
  Use when setting up .env, understanding the default agent/tool/skill/command
  folder layout, or invoking the framework, evaluator, or compiler CLIs.
---

# operating-agent-framework skill

This skill covers the operational side of `agent_framework` — configuration, project layout, and CLI invocation.

## How to use this skill

| Reference | When to load |
|-----------|-------------|
| `references/env-reference.md` | When setting up or debugging .env — all supported keys with defaults and examples |
| `references/project-layout.md` | When structuring the project's agent, tool, skill, and command directories |
| `references/cli-usage.md` | When invoking the framework, evaluator, or workflow compiler from the terminal |

## Quick orientation

- Configuration is driven by `.env` — provider keys, directory paths, model overrides
- Agents live in `AGENT_DIRECTORY`, tools in `TOOLS_DIRECTORY`, skills in `SKILLS_DIRECTORIES`
- The main CLI is `python -m agent_framework`; the evaluator is `python -m agent_framework_evaluator`
- The workflow compiler CLI is `compile-workflow`

## Base directory

The `references/` folder is in the same directory as this file.
