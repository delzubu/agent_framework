# agent_framework — `.env` Reference

Use this reference when creating, reviewing, or debugging `.env` configuration for the framework and evaluator.

---

## Scope

This file covers:

- host/framework `.env` keys
- evaluator `.env` keys
- aliases accepted by the config loader
- runtime/process env vars used by the system but not normally written to `.env`

Primary parser:

- `src/agent_framework/config.py`

Evaluator-specific readers:

- `src/agent_framework_evaluator/initializer_catalog.py`
- `src/agent_framework_evaluator/evaluation.py`

---

## Provider keys

| Key | Meaning | Default |
|---|---|---|
| `OPENAI_API_KEY` | API key for OpenAI-backed agents and LLM evaluation | `""` |
| `DEFAULT_PROVIDER` | Default model provider for agents without their own override | `openai` |
| `DEFAULT_MODEL` | Comma-separated fallback model list | `gpt-4o-mini` |
| `AGENT_MODELS` | Per-agent override map: `agent1=m1,m2|agent2=m3` | empty |

DIAL-specific:

| Key | Meaning | Default |
|---|---|---|
| `DIAL_BASE_URL` | Base URL for DIAL/OpenAI-compatible endpoint | `""` |
| `DIAL_API_VERSION` | API version | `2024-10-21` |
| `DIAL_API_KEY` | API key for DIAL | `""` |

Notes:

- `DEFAULT_MODEL` is split on commas
- `AGENT_MODELS` uses `|` between agents and `,` between models

---

## Core directory keys

| Key | Meaning | Default |
|---|---|---|
| `AGENT_DIRECTORY` | Agent Markdown directory | `agents` |
| `TOOLS_DIRECTORY` | Tool directory | `tools` |
| `WORLD_DIRECTORY` | Sandbox/root directory for file-oriented tools | `world` |
| `ROOT_AGENT` | Default root agent id | `root` |

Accepted aliases:

| Alias | Canonical key |
|---|---|
| `AGENTS_LOCAL_PATH` | `AGENT_DIRECTORY` |
| `TOOLS_LOCAL_PATH` | `TOOLS_DIRECTORY` |
| `WORLD_LOCAL_PATH` | `WORLD_DIRECTORY` |

Resolution rules:

- absolute paths stay absolute
- relative paths resolve relative to the `.env` file directory

---

## Skills keys

| Key | Meaning | Default |
|---|---|---|
| `SKILLS_DIRECTORY` | Single skills directory | auto-detect `skills/` if present |
| `SKILLS_DIRECTORIES` | Comma-separated list of skills directories | empty |
| `SKILLS_CATALOG_MAX_TOKENS` | Max token budget for injected skills catalog | `2000` |

Accepted aliases:

| Alias | Canonical key |
|---|---|
| `SKILLS_LOCAL_PATH` | `SKILLS_DIRECTORY` |
| `SKILLS_LOCAL_DIRECTORIES` | `SKILLS_DIRECTORIES` |

Behavior:

- if neither `SKILLS_DIRECTORY` nor `SKILLS_DIRECTORIES` is set, the loader uses a local `skills/` directory if it exists

---

## Commands keys

| Key | Meaning | Default |
|---|---|---|
| `COMMANDS_DIRECTORY` | Single slash-command directory | empty |
| `COMMANDS_DIRECTORIES` | Comma-separated list of command directories | empty |

Relative values resolve relative to the `.env` file directory.

---

## MCP keys

| Key | Meaning | Default |
|---|---|---|
| `MCP_CONFIG_PATH` | Explicit MCP config JSON path | auto-discover |
| `MCP_ENABLED` | Enable/disable MCP integration | `true` |

If `MCP_CONFIG_PATH` is not set:

- the host searches upward for `.mcp.json`
- then falls back to `~/.agent_framework/mcp.json`

---

## Tool/runtime safety keys

| Key | Meaning | Default |
|---|---|---|
| `MISSING_TOOL_POLICY` | How to handle declared tools that fail to load | `graceful` |

Accepted values:

- `graceful`
- `strict`
- `fail` -> treated as `strict`
- `error` -> treated as `strict`

---

## Memory keys

| Key | Meaning | Default |
|---|---|---|
| `MEMORY_ENABLED` | Enable scoped memory subsystem | `true` |
| `MEMORY_AUTO_STORE_THRESHOLD_BYTES` | Auto-store large parameters above this size | `32768` |
| `MEMORY_BUILTIN_TOOLS_ENABLED` | Expose read-side memory tools by default | `true` |
| `MEMORY_DEFAULT_PROJECTION_MODE` | Prompt projection mode | `catalog_and_selected_content` |
| `MEMORY_BACKEND` | Backend kind | `memory` |
| `MEMORY_QUERY_PROVIDER` | Query provider kind | `catalog` |
| `MEMORY_PROJECTOR` | Projector kind | `xml` |
| `MEMORY_GLOBAL_SCOPES` | Comma-separated additional global scope ids | empty |
| `MEMORY_GROUP_SCOPES` | Comma-separated group scope ids | empty |
| `MEMORY_USE_CASE_SCOPES` | Comma-separated use-case scope ids | empty |
| `MEMORY_ENABLE_AGENT_SCOPE` | Whether agent-specific scope should be visible | `false` |

Notes:

- auto-storage applies to parameters, not prompt text
- `MEMORY_*_SCOPES` values are comma-separated and deduplicated

---

## Evaluator keys

| Key | Meaning | Default |
|---|---|---|
| `AGENT_EVAL_INITIALIZER_DIR` | Directory scanned for evaluator initializer `.py` files | empty |
| `AGENT_EVAL_MODEL` | Model used for LLM-based evaluation/scoring | unset |

Behavior:

- `AGENT_EVAL_INITIALIZER_DIR` resolves relative to the `.env` file
- `AGENT_EVAL_MODEL` is optional; if unset, evaluator uses its normal default behavior
- LLM evaluation still requires `OPENAI_API_KEY`

---

## Runtime/process env vars

These are consumed from the environment but are not usually hand-authored in `.env`.

### Host/runtime flags

| Key | Meaning |
|---|---|
| `AGENT_HOST_RECEIVE_LOG` | Internal/debug receive-log toggle in the host |
| `SUBAGENT_MAX_PARALLELISM` | Max entries per `call_subagents` batch |
| `SUBAGENT_BATCH_TIMEOUT_SECONDS` | Batch wall-clock timeout |
| `SUBAGENT_BATCH_MAX_CALLBACK_ROUNDS` | Max callback-resume rounds for batch execution |

Notes:

- these are read directly from `os.environ` in the runtime
- they can be set in `.env`, but unlike the typed `HostConfig` fields they are not currently normalized there

### Evaluator web defaults

| Key | Meaning |
|---|---|
| `AGENT_EVAL_DEFAULT_ENV_PATH` | Default env path shown by the web UI |
| `AGENT_EVAL_DEFAULT_AGENT` | Default selected agent in the web UI |
| `AGENT_EVAL_DEFAULT_INITIALIZER` | Default selected initializer in the web UI |

These are set by `agent_framework_evaluator web` at process startup. They are runtime wiring, not stable user config.

---

## Example `.env`

```env
OPENAI_API_KEY=sk-...
DEFAULT_PROVIDER=openai
DEFAULT_MODEL=gpt-4.1,gpt-4o-mini

AGENT_DIRECTORY=agents
TOOLS_DIRECTORY=tools
WORLD_DIRECTORY=world
ROOT_AGENT=root

SKILLS_DIRECTORIES=skills,src/agent_framework_skills/skills
SKILLS_CATALOG_MAX_TOKENS=2000

COMMANDS_DIRECTORY=commands

MCP_ENABLED=true
# MCP_CONFIG_PATH=.mcp.json

MISSING_TOOL_POLICY=graceful

MEMORY_ENABLED=true
MEMORY_AUTO_STORE_THRESHOLD_BYTES=32768
MEMORY_BUILTIN_TOOLS_ENABLED=true
MEMORY_DEFAULT_PROJECTION_MODE=catalog_and_selected_content
MEMORY_BACKEND=memory
MEMORY_QUERY_PROVIDER=catalog
MEMORY_PROJECTOR=xml
MEMORY_GLOBAL_SCOPES=
MEMORY_GROUP_SCOPES=
MEMORY_USE_CASE_SCOPES=
MEMORY_ENABLE_AGENT_SCOPE=false

AGENT_EVAL_INITIALIZER_DIR=eval/initializers
AGENT_EVAL_MODEL=gpt-4.1
```

---

## Common mistakes

- using spaces instead of commas in comma-separated values
- assuming `AGENT_DIRECTORY` is relative to cwd instead of the `.env` file
- setting `SKILLS_DIRECTORY` and forgetting the directory does not exist
- expecting `MISSING_TOOL_POLICY=graceful` to surface tool import problems early
- forgetting `OPENAI_API_KEY` when using OpenAI-backed agents or evaluator scoring
- assuming `SUBAGENT_*` limits are typed host config fields; they are currently read directly from the environment

---

## Companion references

- `references/framework-usage.md` for runtime behavior
- `references/agent-usage.md` for agent `.md` and `.json` decisions
- `references/tool-authoring.md` for custom tool implementation
- `references/memory-usage.md` for memory-specific keys and patterns
