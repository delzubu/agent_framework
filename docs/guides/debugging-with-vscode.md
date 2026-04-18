# Debugging with VS Code

Set breakpoints anywhere in `agent_framework`, `agent_framework_evaluator`, your initialiser, custom tools, and skills — all with a single keypress.

## Prerequisites

- VS Code with the [Python (ms-python.python)](https://marketplace.visualstudio.com/items?itemName=ms-python.python) or [Python Debugger (ms-python.debugpy)](https://marketplace.visualstudio.com/items?itemName=ms-python.debugpy) extension installed.
- Project installed in editable mode: `pip install -e ".[dev]"` (editable install makes all `src/` sources live — breakpoints work in package code without any path mapping).

## launch.json

Create `.vscode/launch.json` in the repository root with the following content.

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

## How to use

| Scenario | Steps |
|----------|-------|
| Debug the web UI | Open any file → **Run → Start Debugging** → pick **Evaluator: web UI** |
| Debug all cases from an initialiser | Open the `.py` initialiser → **F5** → pick **Evaluator: .py — run all cases** |
| Debug a single case `.md` file | Open the `.md` case file → **F5** → pick **Evaluator: .md — run single case** → type the agent id when prompted |

The `${file}` variable is resolved to whichever file is currently active in the editor, so there is no need to hard-code paths in the launch config.

## Why these settings matter

### `justMyCode: false`

Without this, VS Code silently skips breakpoints inside any installed package.
Set to `false` to step freely into `agent_framework`, `agent_framework_evaluator`, and any other dependency.

### No `--reload`

Uvicorn's `--reload` flag forks a worker subprocess. The debugger attaches to the launcher process and never reaches your code. The web UI config above omits `--reload` deliberately — the single process is fully debuggable.

### `PYTHONASYNCIODEBUG=1`

Set on the web UI config only. Enables asyncio's debug mode, which surfaces unawaited coroutines and slow callbacks as warnings in the Debug Console.

## Breakpoints in your own code

Because the project uses an editable install (`pip install -e .`), all source files under `src/` are referenced directly — not copied. Breakpoints in your initialiser `.py`, custom tool `.py` files, agent `.md` files (not executable, but useful for reference while stepping), and any local module work out of the box.

If you add a new package outside `src/` and breakpoints are not hitting, check that the package is also installed in editable mode or that its source directory is on `PYTHONPATH`.

## Tip: per-case debugging

To stop at a breakpoint for a specific case only, open the initialiser, use **Evaluator: .py — run all cases**, and add a conditional breakpoint:

```python
# In your code_evaluator or setup() function:
# Right-click the red dot → Edit Breakpoint → Expression:
case_index == 2
```

Or add a temporary `breakpoint()` call in your initialiser's `setup()` or a code evaluator function — it works the same way.
