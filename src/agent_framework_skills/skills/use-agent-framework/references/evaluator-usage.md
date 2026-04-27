# agent_framework_evaluator — Evaluator Usage Reference

## CLI subcommands

All subcommands: `python -m agent_framework_evaluator <cmd>`

### `web` — local FastAPI UI with WebSocket trace streaming
```bash
python -m agent_framework_evaluator web \
  --env .env \
  --agent my_agent \
  --initializer my_initializer.py \
  --agent-model-override gpt-4.1 \
  --agent-model-override-scope root_only \
  --host 127.0.0.1 \
  --port 8123
```
- `--agent` — default agent id (overridable in the UI)
- `--initializer` — initializer `.py` filename (relative to `AGENT_EVAL_INITIALIZER_DIR` from `.env`)
- `--agent-model-override` — optional default model override for the tested agent
- `--agent-model-override-scope` — `root_only` (tested agent only) or `all_agents` (whole run)
- Browser launch is enabled by default; pass `--no-open-browser` to suppress it
- Default URL: `http://127.0.0.1:8123/`

### `run` — headless single-agent invocation
```bash
python -m agent_framework_evaluator run \
  --env .env \
  --agent my_agent \
  --agent-model-override gpt-4.1 \
  --agent-model-override-scope all_agents \
  --prompt "Hello" \
  --prompt-file prompt.txt \
  --setup my_initializer.py \
  --output result.json \
  --trace-jsonl trace.jsonl \
  --trace-llm-dir logs/
```
- `--prompt` or `--prompt-file`: source of the prompt (or `get_prompt_template()` from `--setup`)
- `--agent-model-override`: optional run-scoped override for the agent under test
- `--agent-model-override-scope`: `root_only` or `all_agents`
- `--output`: writes `{"status": ..., "message": ...}` JSON; defaults to stdout
- `--trace-jsonl`: append unified runtime trace events (JSONL)
- `--trace-llm-dir`: per-agent LLM request/response logs under the directory

### `evaluate` — CLI evaluation without web UI
```bash
# All cases from an initializer
python -m agent_framework_evaluator evaluate \
  --env .env \
  --initializer my_initializer.py \
  --agent my_agent \
  --agent-model-override gpt-4.1 \
  --agent-model-override-scope root_only \
  --output results.json \
  --verbose

# Single case by 0-based index
python -m agent_framework_evaluator evaluate \
  --env .env \
  --initializer my_initializer.py \
  --case 2

# Standalone case file (no initializer)
python -m agent_framework_evaluator evaluate \
  --env .env \
  --case-file tests/cases/my_case.md \
  --agent my_agent
```
- PASS threshold: `average_score >= 7.0`
- Output JSON fields: `case_index`, `title`, `run_result`, `llm_result`, `code_result`, `average_score`, `selected_payload`, `result_field`
- `--verbose`: prints `run_result` per case in batch mode

---

## Configuration (`.env`)

```
AGENT_EVAL_INITIALIZER_DIR=path/to/initializers   # directory scanned for *.py initializers
```

All other `.env` keys are inherited from `agent_framework` (see framework-usage.md).

The evaluator UI model field is free text with completions from `.env` `DEFAULT_MODEL`. If `DEFAULT_MODEL` contains `gpt-4.1,gpt-4o-mini`, the UI offers those as suggestions and leaves the field empty by default.

---

## Initializer module

An initializer `.py` module lives under `AGENT_EVAL_INITIALIZER_DIR`. It wires the evaluator to an agent.

```python
# my_initializer.py
from agent_framework_evaluator.case_markdown import MarkdownCaseLoader

DEFAULT_AGENT = "my_agent"
DEFAULT_EVAL_MODEL = "gpt-4o"   # optional; model used to score outputs
DEFAULT_AGENT_MODEL_OVERRIDE = "gpt-4.1"          # optional; model used to run the agent under test
DEFAULT_AGENT_MODEL_OVERRIDE_SCOPE = "root_only"  # optional; root_only | all_agents

def get_test_cases():
    """Return list of case dicts for batch evaluation."""
    loader = MarkdownCaseLoader(
        base_dir=__file__,           # directory anchor
        glob_pattern="cases/*.md",   # relative to base_dir
    )
    return loader.get_test_cases()

def get_prompt_template() -> str:
    """Default prompt for `run` subcommand (optional)."""
    return "Summarise {{instruction}}"

def get_default_agent_model_override() -> str:
    return "gpt-4.1"

def get_default_agent_model_override_scope() -> str:
    return "root_only"

def setup(host):
    """Called before each run. Inject state, register tools, etc. (optional)."""
    pass
```

Keep the two model concepts separate:

- `DEFAULT_EVAL_MODEL` selects the evaluator/scoring LLM
- `DEFAULT_AGENT_MODEL_OVERRIDE` selects the model used to run the tested agent

### Agent model override scopes

- `root_only` — only the tested/top-level agent uses the override; subagents keep their configured models
- `all_agents` — every agent invoked during that run uses the override, superseding `.env` defaults, `.env` `AGENT_MODELS`, and adjacent runtime `.json` `model` declarations

### `MarkdownCaseLoader`
```python
MarkdownCaseLoader(
    base_dir=__file__,           # str or Path; directory anchor for glob
    glob_pattern="cases/*.md",   # glob relative to base_dir's parent
    evaluator_registry=None,     # dict mapping code_evaluator names to callables
    resolver=None,               # optional FileReferenceResolver for @filename tokens
)
```
`get_test_cases()` returns a list of case dicts.

---

## Case file format (`.md`)

Three sections separated by `---` lines (exactly three dashes, nothing else on the line):

```markdown
---
title: My test case title
result_field: message          # optional; which field to score (default: message)
code_evaluator: my_fn          # optional; name registered in evaluator_registry
---
This is the prompt sent to the agent.

Use @filename tokens to inject file contents.
---
Evaluation criteria:
- The agent should mention X
- The response must not contain Y
- Format must be JSON
```

### Frontmatter fields
| Field | Description |
|-------|-------------|
| `title` | Human-readable label shown in UI and batch output |
| `result_field` | Which field of `AgentResult` to score (`message`, `status`) |
| `code_evaluator` | Comma-separated names of callables in `evaluator_registry` for additional scoring (e.g. `fn1` or `fn1, fn2, fn3`) |
| `flags` | Comma-separated arbitrary strings passed to code evaluators as a `set[str]` (e.g. `strict, json_required`) |
| `agent` | Agent id to run this case against; used to auto-populate `--agent` when running with `--case-file` |
| `initializer` | Initializer `.py` filename; used to load the setup module when running with `--case-file`, and to filter cases out of a batch that belongs to a different initializer |

**Conflict rules for `--case-file`:**
- If `agent` is in frontmatter and no `--agent` CLI flag: frontmatter value is used.
- If `agent` is in frontmatter and `--agent` is supplied but differs: the case is **skipped** (nothing runs).
- If `initializer` is in frontmatter: the setup module is loaded automatically (custom tools, etc.).

**Filtering in `MarkdownCaseLoader`:** pass `initializer_ref=` to the loader and cases whose `initializer` frontmatter differs are automatically excluded from the batch.

### `@filename` injection in case prompts
Tokens in the prompt section are expanded before the agent sees the prompt:
- `@config.yaml` → `<file name="config.yaml">\n...content...\n</file>`
- `@"path with spaces.txt"` — quote paths containing spaces
- Binary files → base64-encoded `<file encoding="base64">` block
- Missing file → token left unchanged

Base directory for resolution: the directory containing the case `.md` file.

---

## Evaluation scoring

The evaluator runs a second LLM call to score the agent's output.

**Scoring scale:**
- 1–3: critical failure
- 4–5: partial, significant gaps
- 6–7: some requirements met, some missing
- 8–9: all explicit requirements covered, minor improvements possible
- 10: complete and accurate

**Score formula:** proportion of passed criteria, scaled 1–10. Deviations of ±2 allowed with explicit justification in output.

**Minimum criteria:** at least 8–10 distinct criteria checked (≥5 auto-derived, plus all criteria from the case file).

### LLM evaluation result JSON
```json
{
  "score": 8,
  "evaluation": [
    {"criteria": "Response is in JSON format", "passed": true, "reason": "..."},
    {"criteria": "Contains required field 'name'", "passed": false, "reason": "..."}
  ],
  "result": "Overall assessment text"
}
```

### Code evaluators
Multiple code evaluators can be listed in `code_evaluator` (comma-separated). Each runs sequentially and produces its own output. The `average_score` is computed across the LLM score and all code evaluator scores that return a result (non-None).

**Signature options:**
```python
# Basic — no flags
def check_format(prompt: str, agent_message: str) -> dict | None:
    ...

# With flags — receives the case's flags set
def check_strict(prompt: str, agent_message: str, *, flags: set[str]) -> dict | None:
    ...

# Also works with **kwargs
def check_any(prompt: str, agent_message: str, **kwargs) -> dict | None:
    ...
```

Returning `None` opts the evaluator out — it is excluded from scoring and the average is computed without it. This lets an evaluator skip itself based on flags or other conditions.

```python
def check_json_format(prompt: str, agent_message: str, *, flags: set[str]) -> dict | None:
    if "json_required" not in flags:
        return None  # not applicable for this case — excluded from average
    ok = agent_message.strip().startswith("{")
    return {"score": 10.0 if ok else 0.0, "passed": ok, "reason": "Must be JSON"}

def check_length(prompt: str, agent_message: str) -> dict:
    ok = len(agent_message) < 500
    return {"score": 10.0 if ok else 3.0, "passed": ok, "reason": "Response length"}
```

Case file with flags:
```markdown
---
title: My case
code_evaluator: check_json_format, check_length
flags: json_required, strict
---
Prompt here.
---
Criteria here.
```

Output `code_results` is a list — one entry per evaluator in declaration order. `None` entries indicate opted-out evaluators (excluded from average).

---

## Run modes

| Mode | Behavior |
|------|----------|
| `standard` | Agent waits for user input on callbacks |
| `no_callbacks` | Agent is instructed to make assumptions; callbacks auto-answered |

The `no_callbacks` postfix is injected server-side into the prompt before the agent runs. Agents cannot distinguish it from a normal prompt instruction.

Auto-answer behavior in `no_callbacks`:
- `information_request` / `question` → `EVALUATOR_AUTO_CLARIFICATION_REPLY`
- `confirmation` → `"y"`
- `permission` → `"allow"`

---

## Web UI API (FastAPI)

The web server exposes these key endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/run` | WebSocket | Stream a single agent run with trace events |
| `/api/evaluate-result` | POST | Score the last run result for a session |
| `/api/evaluate-case` | POST | Score a specific case's result |
| `/api/evaluate-batch` | POST | Run and score all cases (NDJSON streaming) |
| `/api/sessions` | GET | List active sessions |
| `/api/initializers` | GET | List available initializer scripts |

All evaluation orchestration (case_run_mode postfix, result_field selection, batch iteration) is server-side. The web UI (`web/app.js`) is a thin observer only.

---

## `SessionRecord` and `last_run_result`

`SessionRecord.last_run_result` stores the payload dict from the most recent `run_once` call per session:
```python
{
  "status": "completed",
  "message": "...",
  # additional fields depending on result_field selection
}
```
The evaluate endpoints read `last_run_result` directly — do not re-introduce client-side forwarding.

---

## `run_evaluation` (programmatic)

```python
from agent_framework_evaluator.evaluation import run_evaluation, run_code_evaluation, select_agent_result_field

llm_result = run_evaluation(
    env_path=Path(".env"),
    evaluator_prompt="Evaluation criteria here",
    agent_message="The agent's output to score",
    model_override="gpt-4o",    # optional
)

# Select a field from the run result
selected = select_agent_result_field(run_result_dict, "message")

# Code evaluator
code_result = run_code_evaluation(my_fn, prompt="...", agent_message="...")
```

---

## Common patterns

### Run a single case headlessly
```bash
python -m agent_framework_evaluator evaluate \
  --env .env --case-file cases/my_case.md --agent my_agent
```

### Batch all cases, write results
```bash
python -m agent_framework_evaluator evaluate \
  --env .env --initializer my_initializer.py --output results.json --verbose
```

### Programmatic run + evaluate
```python
from agent_framework_evaluator.runtime.session_runner import SessionRunner
from agent_framework_evaluator.evaluation import run_evaluation, select_agent_result_field
from pathlib import Path

runner = SessionRunner(".env")
run_result = runner.run_once(agent_id="my_agent", prompt="Hello")
selected = select_agent_result_field(run_result, "message")
llm_result = run_evaluation(
    env_path=Path(".env"),
    evaluator_prompt="Agent must greet the user.",
    agent_message=selected,
)
print(llm_result["score"])
```
