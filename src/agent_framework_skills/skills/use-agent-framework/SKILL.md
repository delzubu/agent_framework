---
name: use-agent-framework
description: Complete guide for writing agents, tools, evaluations, and tests with the agent_framework package. Read references on demand for detailed documentation.
---

# agent_framework skill

This skill gives you everything needed to work with the `agent_framework` package — writing agents, tools, sub-agents, callbacks, evaluations, and test cases — without reading any source files.

## How to use this skill

Load reference files on demand as your work requires:

| Reference | When to load |
|-----------|-------------|
| `references/framework-usage.md` | Before writing or modifying any agent, tool, behavior, or host code |
| `references/evaluator-usage.md` | Before writing evaluations, case files, initializers, or running the evaluator |
| `references/agent-design-guide.md` | When deciding how to structure a multi-agent system (placeholder — TODOs inside) |

## Quick orientation

- **Agents** are `.md` files with YAML frontmatter + system prompt + user prompt template
- **Decisions** are JSON objects emitted by the model each loop iteration (`final_message`, `call_tool`, `call_subagent`, `call_subagents`, `callback`, `invoke_skill`)
- **Tools** are `.md` files with a Python sibling that exports `build_tool(definition)`
- **The evaluator** runs agents against test cases and scores the output with a second LLM
- **Case files** are three-section `.md` files: frontmatter / prompt / criteria

## Base directory

The `references/` folder is in the same directory as this file. Load any reference with:

```
references/framework-usage.md
references/evaluator-usage.md
references/agent-design-guide.md
```
