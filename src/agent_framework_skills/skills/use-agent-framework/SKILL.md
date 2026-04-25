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
| `references/agent-usage.md` | Before creating or editing an agent `.md`, adjacent agent `.json`, or Python `AgentBehavior` |
| `references/workflow-agents.md` | Before building deterministic controller agents that orchestrate subagents from `before_run(...)` |
| `references/tool-authoring.md` | Before creating or changing a custom tool `.md` / `.py` pair |
| `references/evaluator-usage.md` | Before writing evaluations, case files, initializers, or running the evaluator |
| `references/memory-usage.md` | When the agent handles large/shared payloads, `mem://...` refs, or memory tools |
| `references/callback-handling.md` | When the agent or host must handle clarification, approval, escalation, passthrough, or user-input routing |
| `references/env-reference.md` | When editing `.env`, debugging configuration resolution, or checking supported keys |
| `assets/agent-prompt-design-research.md` | When you want a broad survey of how other frameworks design agentic prompts, and the rationale behind patterns |
| `references/agent-prompt-patterns.md` | Before writing a new agent's system prompt — quick checklist + pattern selector |
| `assets/agent-prompt-organization.md` | When writing or reviewing a structured agent system prompt — recommended section order (Responsibilities → Boundaries → Workflow → Output Shape → Specific Rules) with rationale, examples, and copy-ready template |

## Quick orientation

- **Agents** are `.md` files with YAML frontmatter + system prompt + user prompt template
- **Decisions** are JSON objects emitted by the model each loop iteration. For interaction routing, distinguish caller escalation, direct user input, and agent-only resolution instead of treating every question as a generic callback.
- **Tools** are `.md` files with a Python sibling that exports `build_tool(definition)`
- **The evaluator** runs agents against test cases and scores the output with a second LLM
- **Case files** are three-section `.md` files: frontmatter / prompt / criteria

## Base directory

The `references/` folder is in the same directory as this file. Load any reference with:

```
references/framework-usage.md
references/agent-usage.md
references/workflow-agents.md
references/tool-authoring.md
references/evaluator-usage.md
references/memory-usage.md
references/callback-handling.md
references/env-reference.md
assets/agent-prompt-design-research.md
assets/agent-prompt-organization.md
references/agent-prompt-patterns.md
```

## VS Code workspace: patch launch.json

If a `.vscode/` directory exists in the project root, offer to add the evaluator debug configurations to `.vscode/launch.json`. Create the file if it does not exist.

The configurations to add (merge into `configurations` array; add `inputs` array if absent):

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Evaluator: web UI",
      "type": "debugpy",
      "request": "launch",
      "module": "agent_framework_evaluator",
      "args": [
        "web",
        "--env", "${workspaceFolder}/.env"
      ],
      "cwd": "${workspaceFolder}",
      "env": { "PYTHONASYNCIODEBUG": "1" },
      "justMyCode": false
    },
    {
      "name": "Evaluator: .py — run all cases",
      "type": "debugpy",
      "request": "launch",
      "module": "agent_framework_evaluator",
      "args": [
        "evaluate",
        "--env", "${workspaceFolder}/.env",
        "--initializer", "${file}"
      ],
      "cwd": "${workspaceFolder}",
      "justMyCode": false
    },
    {
      "name": "Evaluator: .md — run single case",
      "type": "debugpy",
      "request": "launch",
      "module": "agent_framework_evaluator",
      "args": [
        "evaluate",
        "--env", "${workspaceFolder}/.env",
        "--case-file", "${file}"
      ],
      "cwd": "${workspaceFolder}",
      "justMyCode": false
    }
  ]
}
```

**Merge rules:**
- If `launch.json` already exists: add only the configurations whose `name` is not already present.
- If `launch.json` does not exist: write the full block above.
- Always keep `"version": "0.2.0"` and any existing configurations untouched.

**Note on agent/initializer for `.md` configs:** the `.md` launch config passes no `--agent` flag. The agent id and setup module are read automatically from the case file's frontmatter (`agent:` and `initializer:` fields). Add those fields to any `.md` case file you want to debug with F5 — no prompt required.
