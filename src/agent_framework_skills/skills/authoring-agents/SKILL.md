---
name: authoring-agents
description: |
  Complete guide for writing, iterating, and testing agent_framework agents.
  Use when writing or editing an agent definition — .md file, sidecar .json, prompts,
  decision contracts, behavior hooks, tools, planning/workflow agent variants,
  or running the evaluator to check the agent.
---

# authoring-agents skill

This skill gives you everything needed to define agents, tools, prompts, and evaluations
with the `agent_framework` package — without reading any source files.

## How to use this skill

Load reference files on demand as your work requires:

| Reference | When to load |
|-----------|-------------|
| `references/agent-file-layout.md` | First — before touching any .md or .json agent file |
| `references/agent-usage.md` | When creating or editing a reactive agent .md/.json or Python AgentBehavior |
| `references/tool-authoring.md` | When creating or changing a custom tool .md / .py pair |
| `references/callback-handling.md` | When the agent must handle clarification, approval, escalation, or passthrough |
| `references/planning-agents.md` | When building planning agents that emit submit_plan and execute steps |
| `references/workflow-agents.md` | When building deterministic workflow-controller agents |
| `references/evaluator-usage.md` | When writing evaluations, case files, initializers, or running the evaluator |
| `references/decision-envelope.md` | When checking the exact JSON shape of any decision kind |
| `references/agent-prompt-patterns.md` | Before writing a new agent's system prompt — quick checklist + pattern selector |
| `assets/agent-prompt-organization.md` | When writing or reviewing a structured agent system prompt |
| `assets/agent-prompt-design-research.md` | When you want a broad survey of agentic prompt patterns and rationale |

## Quick orientation

- **Agents** are `.md` files with YAML frontmatter + system prompt + user prompt template
- **Decisions** are JSON objects emitted by the model each loop iteration — shapes vary by kind
- **Tools** are `.md` + `.py` sibling pairs; the `.py` exports `build_tool(definition)`
- **The evaluator** runs agents against test cases and scores output with a second LLM
- **Case files** are three-section `.md` files: frontmatter / prompt / criteria

## Base directory

The `references/` and `assets/` folders are in the same directory as this file.

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
