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
        "--case-file", "${file}",
        "--agent", "${input:agentId}"
      ],
      "cwd": "${workspaceFolder}",
      "justMyCode": false
    }
  ],
  "inputs": [
    {
      "id": "agentId",
      "type": "promptString",
      "description": "Agent id to run this case against",
      "default": "root"
    }
  ]
}
```

**Merge rules:**
- If `launch.json` already exists: add only the configurations whose `name` is not already present; merge the `agentId` input only if no input with `id: "agentId"` exists.
- If `launch.json` does not exist: write the full block above.
- Always keep `"version": "0.2.0"` and any existing configurations untouched.
