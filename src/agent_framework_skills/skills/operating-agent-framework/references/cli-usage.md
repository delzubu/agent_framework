# CLI reference — agent_framework commands

This reference covers the command-line interfaces for running the framework, evaluator, and workflow compiler.

---

## `python -m agent_framework`

The main runtime CLI. Runs an agent against a single instruction or opens an interactive console session.

```bash
# Interactive console (read-eval-print loop)
python -m agent_framework --console --env .env

# One-shot instruction
python -m agent_framework --env .env --instruction "What is X?"

# Target a specific agent (instead of ROOT_AGENT)
python -m agent_framework --env .env --agent summariser --instruction "..."

# Enable LLM request/response tracing
python -m agent_framework --env .env --instruction "..." --llm-trace console
python -m agent_framework --env .env --instruction "..." --llm-trace file
python -m agent_framework --env .env --instruction "..." --llm-trace both

# Write unified TraceEvent log to a JSONL file
python -m agent_framework --env .env --instruction "..." --runtime-trace-jsonl trace.jsonl

# Run XML regression evaluation harness
python -m agent_framework --env .env --evaluate path/to/evaluation.xml
```

Key flags:

| Flag | Meaning |
|------|---------|
| `--env` | Path to `.env` file (defaults to `.env` in cwd) |
| `--console` | Launch interactive console instead of one-shot |
| `--agent` | Agent id to invoke (overrides `ROOT_AGENT`) |
| `--instruction` | Instruction string passed to the agent |
| `--llm-trace` | Log LLM requests/responses: `console`, `file`, or `both` |
| `--runtime-trace-jsonl` | Write structured runtime trace events to a JSONL file |
| `--evaluate` | Run XML evaluation harness against specified file |

---

## `python -m agent_framework_evaluator`

Web UI and CLI for running and evaluating agents interactively or in batch.

```bash
# Start the web UI
python -m agent_framework_evaluator web --env .env

# Run a single agent call from the CLI
python -m agent_framework_evaluator run --env .env --agent <id> --prompt "..."

# Run a batch evaluation
python -m agent_framework_evaluator evaluate \
    --initializer <path> \
    --case-file <path> \
    --verbose
```

Key flags:

| Flag | Meaning |
|------|---------|
| `web` | Launch the evaluator web UI |
| `run` | Execute a single agent run from the terminal |
| `evaluate` | Run a batch evaluation against a case file |
| `--env` | Path to `.env` file |
| `--agent` | Agent id for `run` subcommand |
| `--prompt` | Prompt string for `run` subcommand |
| `--initializer` | Path to Python initializer for `evaluate` |
| `--case-file` | Path to evaluation case file |
| `--verbose` | Show detailed per-case output |

The web UI is available at `http://localhost:8000` by default after `web` starts.

---

## `compile-workflow`

Compiles a planning-agent audit log into a deterministic workflow agent.

```bash
compile-workflow compile \
    --log <audit.jsonl> \
    --agent-id <new_id> \
    --output-dir <dir> \
    --source-agent-path <agent.md>
```

Key flags:

| Flag | Meaning |
|------|---------|
| `--log` | Path to the JSONL audit log produced by the planning agent |
| `--agent-id` | Id to assign to the compiled workflow agent |
| `--output-dir` | Directory where the compiled agent files are written |
| `--source-agent-path` | Path to the original planning agent `.md` file |

The output is a new agent `.md` file (and optional sidecars) in `--output-dir` that replays the captured plan deterministically.

---

## `agent-framework-skills`

Installs pre-built skill packs into the project's skills directory.

```bash
# Install skills into the default location
agent-framework-skills install

# Install to a custom target directory
agent-framework-skills install --target ./.claude/skills --force

# Preview what would be installed without writing files
agent-framework-skills install --dry-run

# List available skills without installing
agent-framework-skills install --list
```

Key flags:

| Flag | Meaning |
|------|---------|
| `--target` | Destination directory for skill folders (default: `skills/`) |
| `--force` | Overwrite existing skill directories |
| `--dry-run` | Show what would be installed without writing |
| `--list` | List available skills and exit |
