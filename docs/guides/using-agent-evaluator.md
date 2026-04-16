# Using the Agent Evaluator

This is a practical, beginner-friendly guide to testing AI agents with the **`agent_framework_evaluator`** package.  No prior prompt engineering experience is required — if you can describe what a "good answer" looks like in plain English, you can write evaluation tests.

For agent and tool authoring basics, start with [Using the agent framework](using-agent-framework.md).  For architecture internals, see [Agent Evaluator & Web Runtime](../architecture/agent-evaluator-web-runtime.md).

---

## What problem does this solve?

Imagine you have built an AI agent that summarises customer support tickets.  You run it manually a few times, it looks fine, and you move on.  A week later someone changes the system prompt to improve tone — and without realising it, the agent now occasionally leaves out the urgency level.  Nobody catches it until a customer complains.

Manual testing does not scale.  You need a way to:

1. Define precisely what a "good" answer looks like for a representative set of inputs.
2. Run those inputs against your agent automatically.
3. Get a score that tells you whether the agent is still meeting your quality bar.

That is exactly what the agent evaluator does.  You write **test cases** — a prompt and a checklist of criteria — and the evaluator uses a second LLM to judge whether the agent's response meets those criteria.  You get a score from 1 to 10 for each case, a list of which criteria passed or failed, and a written verdict.

The key insight is that you write your criteria in plain English:

> - The summary must mention the wrong colour
> - The summary must not exceed two sentences
> - The tone must be professional and neutral

No code.  No regex.  Just descriptions of what you care about.

---

## How it works — the big picture

```
  Your test case (.md file)
  ┌─────────────────────────┐
  │ Prompt for the agent    │
  │ Evaluation criteria     │
  └────────────┬────────────┘
               │ run
               ▼
  ┌─────────────────────────┐
  │  Your agent             │  ← runs as normal
  └────────────┬────────────┘
               │ agent result
               ▼
  ┌─────────────────────────┐
  │  Evaluator LLM          │  ← a second LLM call
  │  (reads system prompt,  │
  │   user prompt, criteria,│
  │   and agent result)     │
  └────────────┬────────────┘
               │
               ▼
  Score 1–10 + per-criteria pass/fail + written verdict
```

The evaluator LLM is strict — it is instructed to be critical and find gaps.  A score of 8 or 9 means all explicit requirements are covered with minor room for improvement.  A score of 6–7 means something is missing.  Below 5 means significant failure.

---

## Installation

Install with web dependencies (FastAPI, Uvicorn):

```bash
pip install "agent_framework[web]"
```

For development, `pip install "agent_framework[dev]"` already includes the same stack.

The console script `agent-eval` is equivalent to `python -m agent_framework_evaluator`:

```bash
agent-eval evaluate --env .env --initializer path/to/init.py
# same as:
python -m agent_framework_evaluator evaluate --env .env --initializer path/to/init.py
```

---

## Configuration

The evaluator reads the same `.env` file as the main framework.  At minimum you need:

```
# Which LLM provider to use
DEFAULT_PROVIDER=openai
OPENAI_API_KEY=sk-...

# Which model to use for your agent
DEFAULT_MODEL=gpt-4o-mini

# Directories
AGENT_DIRECTORY=agents
TOOLS_DIRECTORY=tools
WORLD_DIRECTORY=sandbox
ROOT_AGENT=root
```

All directory paths are resolved relative to the folder that contains the `.env` file.

**Two models are in play** during an evaluation run:

| Model | Configuration | Purpose |
|-------|--------------|---------|
| Agent model | `DEFAULT_MODEL` | Runs your agent |
| Evaluator model | `AGENT_EVAL_MODEL` (optional) | Scores the result |

If `AGENT_EVAL_MODEL` is not set, the evaluator uses `DEFAULT_MODEL`.  You can also pin the evaluator model per initializer by setting `DEFAULT_EVAL_MODEL` in the initializer file (see below).

A good rule of thumb: use a fast, cheap model (like `gpt-4o-mini`) as the evaluator and your production model as the agent.  The evaluator task is straightforward JSON output; it does not need a frontier model.

---

## Your first test case — a standalone `.md` file

The simplest way to run an evaluation is with a single Markdown file.  No initializer needed.

**File:** `docs/guides/evaluator-examples/quickstart/cases/hello.md`

```markdown
---
title: Basic greeting
---
Say hello to Alice and tell her today is a great day to learn something new.
---
- The response must address Alice by name
- The response must contain a greeting (hello, hi, hey, or similar)
- The response must mention learning or something new
- The response must be friendly and positive in tone
- The response must be concise — no more than three sentences
```

The file has three sections, separated by a line containing only `---`:

1. **Frontmatter** — metadata like `title`
2. **Prompt** — what gets sent to your agent
3. **Criteria** — one bullet per thing you want to check

Run it:

```bash
python -m agent_framework_evaluator evaluate \
  --env .env \
  --case-file docs/guides/evaluator-examples/quickstart/cases/hello.md
```

You will see output like:

```json
{
  "run_result": {
    "status": "completed",
    "message": "Hello Alice! Today is a wonderful day to learn something new..."
  },
  "llm_result": {
    "score": 9,
    "overall_verdict": "The response addresses Alice by name, contains a friendly greeting, mentions learning, maintains a positive tone, and is concise.",
    "evaluation": [
      { "criteria": "Addresses Alice by name", "passed": true, "reason": "Response opens with 'Hello Alice'" },
      { "criteria": "Contains a greeting", "passed": true, "reason": "Uses 'Hello'" },
      ...
    ]
  },
  "average_score": 9.0,
  "result_field": "message",
  "selected_payload": "Hello Alice! Today is a wonderful day..."
}
```

That is all you need to run your first evaluation.

---

## Understanding the case file format

Every case file follows the same structure:

```
---
<frontmatter>
---
<prompt>
---
<evaluation criteria>
```

### The frontmatter section

Frontmatter contains `key: value` pairs.  Supported fields:

| Field | Required | Default | Meaning |
|-------|----------|---------|---------|
| `title` | no | filename | Short human-readable name shown in reports |
| `result_field` | no | `message` | Which field of the agent result to evaluate (see [Evaluating structured output](#evaluating-structured-output)) |
| `code_evaluator` | no | none | Name of a programmatic evaluator function (see [Programmatic evaluators](#programmatic-evaluators)) |
| `case_run_mode` | no | `standard` | Set to `no_callbacks` to prevent the agent from asking clarifying questions during batch runs |

Example with all fields:

```markdown
---
title: Extract order details
result_field: parameters
code_evaluator: check_order_schema
case_run_mode: no_callbacks
---
```

### The prompt section

This is the instruction that gets sent to your agent verbatim.  Write it exactly as you would type it into a chat interface.  Be as specific as your real users would be — if your users often send vague instructions, your test prompts should sometimes be vague too.

### The criteria section

Each line that starts with `-` is one criterion.  The evaluator LLM checks each one independently and marks it passed or failed.  The score is roughly proportional to the number that pass (scaled 1–10), with the evaluator allowed to adjust by up to ±2 when something critical is missing or surprisingly good.

**Writing good criteria is the most important skill in evaluation.**  Here are the principles:

**Be specific and verifiable.**  The evaluator LLM can only check what it can read.  Vague criteria produce unreliable scores.

| Weak | Strong |
|------|--------|
| The response is good | The response mentions the customer's order number |
| The answer is correct | The answer states 1889 as the completion year |
| Proper format | The response is a Markdown table with exactly three columns |

**Cover both what should be present and what should not.**  Omission bugs are common.

```markdown
- The summary must mention the delivery delay
- The summary must NOT include the customer's personal address
- The summary must not add information not present in the original ticket
```

**Include format requirements explicitly.**

```markdown
- The response must be two sentences or fewer
- The response must use bullet points, not prose
- The response must be in JSON format with keys: name, email, date
```

**Add at least 4–5 criteria per case.**  The evaluator is instructed to check at least 8–10 things total, so it will supplement yours with its own reasoning — but having more explicit criteria gives you more control over what matters.

**Think about edge cases and failure modes for your specific agent.**

```markdown
- The response must not hallucinate facts not provided in the context
- The response must handle the misspelled word "teh" gracefully
- The agent must not ask clarifying questions — it should make reasonable assumptions
```

---

## Running multiple cases with an initializer

For more than one or two cases, you need an **initializer** — a Python file that tells the evaluator where to find your case files and how to configure the run.

Here is the minimal initializer from the quickstart example:

**File:** `docs/guides/evaluator-examples/quickstart/init.py`

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_framework_evaluator.case_markdown import MarkdownCaseLoader

_HERE = Path(__file__).resolve().parent

CASES_GLOB = "cases/*.md"    # relative to this file
DEFAULT_AGENT = "root"       # the agent id to invoke
DEFAULT_EVAL_MODEL = ""      # leave empty to use DEFAULT_MODEL from .env

_LOADER = MarkdownCaseLoader(_HERE, CASES_GLOB)


def get_test_cases() -> list[dict[str, Any]]:
    return _LOADER.get_test_cases()
```

That is the complete file.  `MarkdownCaseLoader` discovers all `.md` files matching the glob, parses them, and caches the results.  The cache invalidates automatically when any file changes.

**`get_test_cases()`** is the one function the evaluator always calls.  You must define it.

### Naming and organising case files

Case files are loaded in sorted order by filename.  Use numeric prefixes to control ordering:

```
cases/
  01_happy_path.md
  02_edge_case_empty_input.md
  03_edge_case_long_input.md
  04_regression_ticket_8823.md
```

Name your cases descriptively.  When a batch run fails at score 5, you want to immediately know which scenario failed without opening the file.

### Running with an initializer

```bash
# Run all cases
python -m agent_framework_evaluator evaluate \
  --env .env \
  --initializer docs/guides/evaluator-examples/quickstart/init.py

# Run only case 0 (0-based index, sorted by filename)
python -m agent_framework_evaluator evaluate \
  --env .env \
  --initializer docs/guides/evaluator-examples/quickstart/init.py \
  --case 0

# Verbose: show per-case run result details in the summary
python -m agent_framework_evaluator evaluate \
  --env .env \
  --initializer docs/guides/evaluator-examples/quickstart/init.py \
  --verbose

# Save full results to a JSON file
python -m agent_framework_evaluator evaluate \
  --env .env \
  --initializer docs/guides/evaluator-examples/quickstart/init.py \
  --output results.json
```

A batch run prints a summary table:

```
Case                                  Score  Verdict
------------------------------------  -----  -------
Basic greeting                          9.0  PASS
Capital cities — accuracy check         8.5  PASS
```

### All CLI flags

| Flag | Meaning |
|------|---------|
| `--env FILE` | Path to `.env` (default: `.env`) |
| `--initializer PATH` | Initializer `.py` — runs all cases unless `--case N` is set |
| `--case-file PATH` | Standalone case `.md` — mutually exclusive with `--initializer` |
| `--case N` | 0-based case index (requires `--initializer`) |
| `--agent ID` | Override the agent id from the initializer |
| `--output FILE` | Write full JSON result(s) to this file |
| `--verbose` | Include per-case run result detail in stdout summary |

---

## Single-case output format

When you run a single case (with `--case-file` or `--initializer --case N`), the output is a JSON object:

```json
{
  "run_result": {
    "status": "completed",
    "message": "The agent's response text..."
  },
  "llm_result": {
    "score": 8.5,
    "overall_verdict": "All core requirements met; minor phrasing improvement possible.",
    "evaluation": [
      {
        "criteria": "Mentions the customer's name",
        "passed": true,
        "reason": "Response opens with 'Dear Alice'"
      },
      {
        "criteria": "Summary is two sentences or fewer",
        "passed": false,
        "reason": "Response contains four sentences."
      }
    ]
  },
  "code_result": null,
  "average_score": 8.5,
  "result_field": "message",
  "selected_payload": "The agent's response text..."
}
```

`average_score` averages `llm_result.score` and `code_result.score` when both are present, otherwise it equals the one that ran.

`selected_payload` is the exact text that was sent to the evaluator LLM — it comes from the field named in `result_field` (default: `message`).

---

## Evaluating structured output

Some agents return structured data rather than a prose response.  For example, an extraction agent might return:

```json
{
  "status": "completed",
  "message": "Extracted successfully.",
  "parameters": {
    "order_number": 4821,
    "customer_name": "John Smith",
    "order_date": "2024-03-15",
    "amount": 149.99
  }
}
```

In this case, evaluating the `message` field ("Extracted successfully.") tells you nothing useful.  You want to evaluate `parameters`.

Set `result_field: parameters` in the frontmatter:

```markdown
---
title: Extract order details
result_field: parameters
---
Extract the order details from: "Order #4821 placed by John Smith..."
---
- The result must contain an order_number field with value 4821
- The result must contain a customer_name field with value "John Smith"
- All four fields must be present in the structured output
```

The evaluator will serialise the `parameters` dict to JSON and send that to the scoring LLM.

You can use dot notation for nested fields: `result_field: parameters.address.city`.

### Passing the full agent result to the evaluator

Use `result_field: .` (a single dot) to serialise the entire agent result dict as JSON and send it to the evaluator.  This is useful when:

- Your agent returns a rich multi-field object and you want the evaluator to assess the whole thing holistically (not just one field).
- You are unsure which field matters and want the evaluator to look at everything.
- You are debugging and want to see the full result in the evaluator trace.

```markdown
---
title: Full result holistic check
result_field: .
case_run_mode: no_callbacks
---
Extract the user's name, email, and subscription tier from the profile text below.
Profile: "Alice Chen, alice@example.com, subscribed to Pro plan since January 2023."
---
- The result must include a name field containing "Alice Chen"
- The result must include an email field containing "alice@example.com"
- The result must include a tier or plan field indicating "Pro"
- All three fields must be present; missing fields are a failure
- No field should be null or empty
```

The evaluator LLM will see the entire JSON blob, so your criteria can reference any field by name even without specifying `result_field: parameters.X`.  The tradeoff is that the evaluator receives more text, which can slightly affect scoring accuracy for very large payloads.

**Important:** if the field you name does not exist in the agent result, the evaluator exits with an error (exit code 1) rather than silently scoring an empty payload.  This is intentional — a missing field is a bug, not a "no score" case.

See the full example: [`evaluator-examples/multi-case/cases/03_result_field.md`](evaluator-examples/multi-case/cases/03_result_field.md)

### What about evaluating the full LLM conversation?

There is currently no built-in mechanism to pass the full multi-turn conversation history (all LLM requests, assistant replies, and tool outputs) to the evaluator.  This is a planned feature (see GitHub issue #17 for status).  For now, the evaluator receives only the system prompt and first user turn from the agent's first LLM call, plus the final agent result.

---

## Preventing the agent from asking questions

Agents sometimes ask clarifying questions instead of answering directly.  During interactive use that is fine, but in a batch evaluation run there is nobody to answer them — the run will hang.

Add `case_run_mode: no_callbacks` to the frontmatter:

```markdown
---
title: Summarise a ticket
case_run_mode: no_callbacks
---
```

This appends a mandatory instruction to the prompt telling the agent to make assumptions and not ask questions.  Use it whenever your agent might stall.

---

## Programmatic evaluators

The LLM evaluator is good at open-ended quality judgement, but some checks are better done in code.  For example:

- Does the output parse as valid JSON?
- Is a numeric value within an acceptable range?
- Does a URL in the response resolve to a 200 OK?

Add a `code_evaluator` to your initializer:

```python
from collections.abc import Callable
from typing import Any

_EVALUATORS: dict[str, Callable[..., Any]] = {}

def _evaluator(name: str):
    def deco(fn):
        _EVALUATORS[name] = fn
        return fn
    return deco

@_evaluator("check_non_empty")
def _check_non_empty(prompt: str, agent_message: str) -> dict[str, Any]:
    ok = bool(str(agent_message).strip())
    return {
        "score": 8 if ok else 2,
        "result": "Non-empty." if ok else "Empty response — fail.",
        "evaluation": [
            {
                "criteria": "Agent produced non-empty output",
                "passed": ok,
                "reason": "ok" if ok else "Response was blank.",
            }
        ],
    }
```

Pass the registry to the loader:

```python
_LOADER = MarkdownCaseLoader(_HERE, CASES_GLOB, _EVALUATORS)
```

Reference it in a case file's frontmatter:

```markdown
---
title: Non-empty check
code_evaluator: check_non_empty
---
Reply with a single word: hello
---
- The response should be non-empty
- The response should be exactly one word
```

The code evaluator function receives:
- `prompt: str` — the prompt that was sent to the agent
- `agent_message: str` — the agent result (the field named by `result_field`)

It must return a dict with the same shape as the LLM evaluator output: `score`, `result`, `evaluation`.

When both a code evaluator and LLM criteria are present, both run independently.  `average_score` in the output averages the two scores.  If you only want code evaluation and no LLM scoring, leave the criteria section empty.

The complete working example is in [`evaluator-examples/multi-case/init.py`](evaluator-examples/multi-case/init.py) and [`evaluator-examples/multi-case/cases/02_programmatic_check.md`](evaluator-examples/multi-case/cases/02_programmatic_check.md).

---

## Using the web UI

The web UI is ideal for interactive debugging — running a single case, watching the agent's trace in real time, and checking evaluation results step by step before you commit to a full batch run.

### Starting the server

```bash
python -m agent_framework_evaluator web --env .env
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--env` | `.env` | Path to environment file |
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `8123` | Port |
| `--open-browser` | off | Open the default browser automatically |

Open `http://127.0.0.1:8123/` in your browser.

### Running a case interactively

1. **Agent field** — type the agent id you want to test (e.g. `root`).  The field autocompletes from your agent catalog.
2. **Prompt field** — paste the prompt from a case file (or type a new one).
3. Click **Run**.

The **Spans** pane on the right shows a live trace of everything that happened: each LLM call, each tool invocation, each sub-agent, and any decisions the agent made.  This is invaluable for understanding why an agent produced a particular output.

The **Conversation** pane shows the final result and any messages.

### Running a batch from the UI

Open an initializer in the UI.  The **Run all cases** button streams results progressively — you see each case appear as it finishes, which is much faster to review than waiting for a full batch to complete before seeing anything.

### Evaluating a result

After a run, click **Evaluate**.  The UI sends the stored result to the evaluator LLM using the criteria from the case file.  You will see the score, per-criteria pass/fail, and the verdict appear in the Conversation pane.

**Tip:** set the log-level dropdown to `debug` before evaluating.  The Spans pane will show the full evaluator input (system prompt, user prompt, criteria, agent result) and the raw LLM response.  This is the fastest way to diagnose a surprising score.

### Debugging with traces

The channel checkboxes control what appears in the Spans pane:

| Channel | What it shows |
|---------|--------------|
| `runtime` | Agent start/finish, decisions, tool calls |
| `llm` | Every LLM request and response |
| `tool` | Tool execution details |
| `log` | Python log messages |
| `user` | User-facing messages |

If your agent is producing a wrong answer, turn on the `llm` channel at `debug` level and inspect the first LLM request — check whether the system prompt is being assembled correctly.  Most prompt issues are immediately visible there.

### Answering clarification requests

If your agent asks a clarifying question during a run, the **Conversation** pane will show the question with a **Reply** box.  Type your answer and press Enter.  You can also answer over HTTP if the WebSocket disconnects:

```http
POST /api/sessions/{session_id}/user-input
Content-Type: application/json

{ "prompt_id": "<uuid shown in the UI>", "text": "Your answer" }
```

---

## Realistic example walkthrough

The [`evaluator-examples/realistic/`](evaluator-examples/realistic/) folder contains three cases that exercise common real-world quality concerns:

**01 — Summarise a support ticket** ([`cases/01_summarize.md`](evaluator-examples/realistic/cases/01_summarize.md))**:**  Tests that the agent summarises accurately, strips emotional language, and stays within two sentences.

**02 — Reformat data as a table** ([`cases/02_format_check.md`](evaluator-examples/realistic/cases/02_format_check.md))**:**  Tests format compliance (Markdown table), completeness (all four rows), and character encoding.

**03 — No hallucination** ([`cases/03_no_hallucination.md`](evaluator-examples/realistic/cases/03_no_hallucination.md))**:**  Tests that the agent answers only from provided context and does not invent facts.

Run them all:

```bash
python -m agent_framework_evaluator evaluate \
  --env .env \
  --initializer docs/guides/evaluator-examples/realistic/init.py \
  --verbose
```

These cases all use `case_run_mode: no_callbacks` to prevent the agent from asking questions in batch mode.

---

## Setting up tools, sub-agents, and MCP

When testing agents that depend on specific tools, external services, or sub-agents, you need a way to register those dependencies before the agent run begins.  This is done through the **setup module** — a Python file with lifecycle hooks that the evaluator calls automatically.

### The key insight: your initializer IS the setup module

You do not need two separate files.  The same `init.py` that defines `get_test_cases()` can also define `register()`, `suite_setup()`, and the other hooks.  The evaluator loads the initializer as a setup module automatically.

This means you can put everything — test cases, tool registration, lifecycle hooks — in one file:

```python
# my_suite/init.py

from __future__ import annotations
from pathlib import Path
from typing import Any
from agent_framework_evaluator.case_markdown import MarkdownCaseLoader

_HERE = Path(__file__).resolve().parent
CASES_GLOB = "cases/*.md"
DEFAULT_AGENT = "root"
DEFAULT_EVAL_MODEL = "gpt-4o-mini"

_LOADER = MarkdownCaseLoader(_HERE, CASES_GLOB)


def get_test_cases() -> list[dict[str, Any]]:
    """Test case discovery — always required."""
    return _LOADER.get_test_cases()


def register(host, session_context):
    """Called once after the host is created, before any test runs.
    Use this to register custom tools or override host configuration."""
    from my_project.tools import MockEmailTool, MockDatabaseTool
    host.tool_registry.register(MockEmailTool())
    host.tool_registry.register(MockDatabaseTool(connection_string="sqlite:///:memory:"))


def suite_setup(session_context):
    """Called once at the start of a session (before the first case)."""
    print(f"Starting test suite for session {session_context.session_id}")


def test_setup(case_dict, session_context):
    """Called before each individual case run."""
    print(f"Starting case: {case_dict.get('prompt', '')[:60]}")


def test_teardown(case_dict, session_context):
    """Called after each individual case run, even if it failed."""
    pass


def suite_teardown(session_context):
    """Called when the session closes (browser tab close, CLI completion, etc.)."""
    print("Suite complete — cleaning up.")
```

### Why use tool registration in tests?

Agents that call real external services (email, databases, APIs) are problematic to test:

- They may have side effects (sending real emails, modifying real data).
- They may be unavailable in CI environments.
- They may be slow or rate-limited.

By registering mock versions in `register()`, your agent runs against a controlled, fast, side-effect-free implementation.  The mock can record what the agent tried to do, which you can then assert in a `code_evaluator`.

### Tool registration API

`register()` receives the live `AgentHost` instance.  The tool registry supports:

```python
def register(host, session_context):
    # Register any Tool subclass instance
    host.tool_registry.register(my_custom_tool_instance)

    # The built-in tools (Read, Write, Bash, etc.) are already registered
    # by default.  You can add alongside them.

    # You can also adjust host-level configuration:
    host.config.missing_tool_policy = "graceful"  # skip unloadable tools
```

Tool objects must be subclasses of `agent_framework.tool.Tool`.  See the built-in tools in `src/agent_framework/builtin_tools/` for reference implementations.

### Configuring sub-agents

Sub-agents are file-based: the evaluator discovers them from `AGENT_DIRECTORY` in `.env`.  There is currently no programmatic API to register agents dynamically per test.

To test an agent that uses sub-agents, make sure the sub-agent `.md` files are in `AGENT_DIRECTORY` (or a configured path) before the run.  The host will discover them automatically.

If you need different sub-agent configurations per test, the current approach is to use separate `.env` files and separate test suites.

### Configuring MCP servers

MCP (Model Context Protocol) servers are configured at the host level via `.env`, not per test:

```
# .env
MCP_ENABLED=true
MCP_CONFIG_PATH=path/to/.mcp.json    # auto-discovered from cwd upward if omitted
```

The `.mcp.json` file lists server connections (stdio or HTTP).  All tests in a suite share the same MCP configuration.  There is currently no mechanism to inject different MCP servers per test case.

If your test suite needs MCP tools available, ensure `MCP_ENABLED=true` in your `.env` and the server processes are reachable.  The `register()` hook fires after the MCP bridge is already set up, so MCP tools are already in `host.tool_registry` by the time `register()` runs — you can inspect or supplement them there.

### Lifecycle hooks reference

| Hook | When | Common uses |
|------|------|-------------|
| `register(host, session_context)` | After host creation, before first run | Register tools, adjust config |
| `suite_setup(session_context)` | Once, before the first test case | Start servers, seed databases |
| `test_setup(case_dict, session_context)` | Before each case | Reset per-case state |
| `test_teardown(case_dict, session_context)` | After each case (even on failure) | Assert side effects, clean up |
| `suite_teardown(session_context)` | On session close | Stop servers, final cleanup |

`session_context` carries `session_id`, `agent_id`, `env_path`, and `setup_path` — useful for logging and conditional logic.

`case_dict` in `test_setup` / `test_teardown` contains `{ "prompt": "...", ... }` for the current case.

### When you do need a separate file

If you want to reuse the same setup logic across multiple initializers, put it in a dedicated `setup.py` and pass it explicitly:

```bash
python -m agent_framework_evaluator evaluate \
  --env .env \
  --initializer init.py \
  --setup shared_setup.py
```

Or select it in the **Setup File** field in the web UI.  The separate setup file takes precedence over hooks defined in the initializer.

---

## Tracing and saving run logs

### Saving traces to files (headless)

```bash
python -m agent_framework_evaluator evaluate \
  --env .env \
  --initializer init.py \
  --trace-jsonl logs/run.jsonl
```

The JSONL file contains one event per line and can be opened with `trace_viewer.html` (bundled in the package).

### The `run` subcommand — single agent invocation

The `evaluate` subcommand always calls the evaluator LLM.  If you just want to run the agent without scoring, use `run`:

```bash
python -m agent_framework_evaluator run \
  --env .env \
  --agent root \
  --prompt "Your instruction here"
```

| Flag | Meaning |
|------|---------|
| `--prompt TEXT` or `--prompt-file PATH` | Instruction to the agent |
| `--setup PATH` | Optional setup module |
| `--output FILE` | Write JSON result to file |
| `--trace-jsonl FILE` | Append trace events to JSONL |
| `--trace-llm-dir DIR` | Write LLM channel events to per-agent log files |

---

## Iterating on your evaluation suite

Here is a practical workflow for building up a test suite from scratch:

**1. Start with one happy-path case.**  Pick the most common input your agent will receive and write 5–8 criteria for it.  Run it against your current agent.  If it scores below 7, fix the agent before adding more cases.

**2. Add edge cases one at a time.**  Think about what could go wrong: empty input, very long input, input with special characters, ambiguous phrasing, input that is close to a failure mode.  Add one case per scenario.

**3. Add regression cases for bugs you fix.**  Every time you find and fix a bug, add a case that would have caught it.  Name it with the bug or ticket reference: `04_regression_ticket_8823.md`.

**4. Use `no_callbacks` for batch runs.**  Most cases should use `case_run_mode: no_callbacks` unless you are specifically testing the agent's ability to ask good clarifying questions.

**5. Review surprising scores.**  When a case scores lower or higher than expected, run it in the web UI with log level `debug` and look at the full evaluator input.  Common causes:
- The criteria are too vague (the evaluator cannot tell if they are met)
- The `result_field` is wrong (you are evaluating the wrong part of the output)
- The agent's system prompt changed and now does something different

**6. Aim for a "baseline" batch that all passes at 8+.**  Once you have that, you can run the batch after every change to your agent and immediately see regressions.

---

## HTTP API (reference)

If you are building tooling around the evaluator or calling it programmatically, here are the relevant endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/sessions` | Create a session. Body: `{ "env_path": ".env" }`. Returns `{ "session_id" }`. |
| `POST` | `/api/evaluate-result` | Score the last run. Body: `session_id`, `evaluator_prompt`, optional `result_field`, optional `log_level`. Returns 400 if no run result or field missing. |
| `POST` | `/api/evaluate-case` | Score one initializer case. Body: `session_id`, `initializer`, `case_index`, optional `log_level`. |
| `POST` | `/api/evaluate-batch` | Run and score all cases. Body: `session_id`, `initializer`, optional `case_indices`, optional `log_level`. Streams NDJSON — one line per case. |
| `POST` | `/api/sessions/{id}/close` | Finalize session; runs `suite_teardown`. |
| `POST` | `/api/sessions/{id}/user-input` | Deliver input for an active clarification wait. Body: `{ "prompt_id": "<uuid>", "text": "answer" }`. |
| `GET` | `/api/agents` | List available agent ids. |
| `WebSocket` | `/ws/{session_id}` | Live trace stream; send `{ "type": "run", ... }` to start a run. |

The evaluate endpoints always read the agent result from the **server-side session** (`last_run_result`).  They do not accept an agent result in the request body — you must run the agent first.

---

## Troubleshooting

| Symptom | What to check |
|---------|--------------|
| `ModuleNotFoundError: fastapi` | Install `agent_framework[web]` or `[dev]`. |
| Empty agent list in the web UI | Check `.env` path and `AGENT_DIRECTORY`.  Try `GET /api/agents?env_path=.env` directly. |
| Run fails immediately | Check API key, model name, and server stderr. |
| Agent hangs waiting for input | Add `case_run_mode: no_callbacks` to the case frontmatter. |
| Score is 0 with "Evaluator failed" verdict | The evaluator LLM call failed.  Set log level to `debug` and look at the evaluator trace for the error. |
| Score is surprisingly low | Open the case in the web UI, set log level `debug`, run, and click Evaluate.  Check `selected_payload_preview` in the trace — confirm the evaluator received the text you expected. |
| `result field 'X' not found` error | The `result_field` in frontmatter does not match the agent's output structure.  Check the agent result in the `run_result` field of the CLI output. |
| Case file not discovered | Check the `CASES_GLOB` pattern in your initializer.  The glob is relative to the initializer file's directory.  Run `python -c "from pathlib import Path; print(list(Path('path/to/init').parent.glob('cases/*.md')))"` to debug. |

---

## Examples reference

All examples live in [`evaluator-examples/`](evaluator-examples/) next to this file:

| Folder | What it demonstrates |
|--------|---------------------|
| [`quickstart/`](evaluator-examples/quickstart/) | Minimal two-case setup: a greeting and a factual question |
| [`multi-case/`](evaluator-examples/multi-case/) | LLM scoring, programmatic code evaluator, and `result_field` for structured output |
| [`realistic/`](evaluator-examples/realistic/) | Real-world scenarios: summarisation, table formatting, hallucination resistance |

Start with `quickstart/` to verify your setup works, then copy `multi-case/` as the base for your own test suite.

---

## Further reading

- [Agent Evaluator & Web Runtime (architecture)](../architecture/agent-evaluator-web-runtime.md)
- [Tracing & Evaluation](../architecture/tracing-evaluation.md)
- [Host & Orchestration](../architecture/host-orchestration.md)
- [Using the agent framework](using-agent-framework.md)
