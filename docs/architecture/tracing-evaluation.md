# Tracing, Audit & Evaluation

> This document is part of the `agent_framework` architecture reference.
> See also: [Overview](./overview.md) · [Agent Runtime](./agent-runtime.md) · [Host & Orchestration](./host-orchestration.md) · [Extension Points](./extension-points.md) · [Interface Specifications](./interfaces.md)

---

## 1. Overview

The framework provides three complementary observability mechanisms:

| Mechanism | Module | Purpose | Output |
|-----------|--------|---------|--------|
| **Audit Tracing** | `audit_trace.py` | Immutable per-run records of agent calls, LLM I/O, decisions, callbacks | JSONL file in `logs/` |
| **LLM Trace Logging** | `llm_trace_logging.py` | Provider-level request/response logging for debugging | Console and/or per-agent log files |
| **Lifecycle Tracing** | `trace_logging.py` | Console-visible hook events for development | stdout |

And one testing mechanism:

| Mechanism | Module | Purpose |
|-----------|--------|---------|
| **Evaluation** | `evaluator.py` | Regression testing of agent behavior against expected outputs |

---

## 2. Audit Tracing (`audit_trace.py`)

### 2.1 Design

Audit tracing uses the **immutable record with progressive construction** pattern. Each agent run produces one `AgentCallAuditRecord`. The record starts frozen and is updated via `dataclasses.replace()` as the run proceeds — adding LLM requests, responses, decisions, callbacks, and events. When the run completes, the final record is appended as a JSONL line to the output file.

No record is ever directly mutated. This makes the audit trail trustworthy: any observation of a record is a snapshot of its state at that moment.

### 2.2 `InMemoryAuditTracer`

```python
@dataclass(slots=True)
class InMemoryAuditTracer:
    output_dir: Path
    active_records: dict[str, AgentCallAuditRecord]  # run_id → record
    output_path: Path                                  # trace-YYMMDD_HHMMSS.jsonl
```

**`__post_init__`**: Creates `output_dir` if needed. Sets `output_path` to `trace-{YYMMDD_HHMMSS}.jsonl`.

**Methods:**

| Method | Parameters | Effect |
|--------|-----------|--------|
| `start_agent_call(run_id, caller_id, agent_name, system_prompt, system_prompt_sources, user_prompt, user_prompt_sources)` | — | Creates initial frozen `AgentCallAuditRecord`; stores in `active_records[run_id]` |
| `record_llm_request(*, run_id, payload)` | `payload: Any` | `replace()` record with `llm_message_sent=payload` |
| `record_llm_response(*, run_id, raw_text, parsed_payload)` | — | `replace()` record with `llm_message_received=(raw_text, parsed_payload)` |
| `record_decision(*, run_id, decision)` | `decision: AgentDecision` | `replace()` record with `agent_decision=decision_to_dict(decision)` |
| `record_callback(*, run_id, intent, prompt, target, response)` | — | Appends `CallbackAuditRecord` to record's `callbacks` tuple |
| `record_event(*, run_id, event)` | `event: dict` | Appends to record's `events` tuple |
| `finish_agent_call(*, run_id)` | — | Pops record from `active_records`; appends `record.to_jsonable()` as JSONL line to `output_path` |

### 2.3 `AgentCallAuditRecord`

```python
@dataclass(frozen=True, slots=True)
class AgentCallAuditRecord:
    timestamp: str                          # ISO 8601
    run_id: str
    caller_id: str | None
    agent_name: str
    system_prompt: str
    system_prompt_sources: tuple[str, ...]  # template file paths
    user_prompt: str
    user_prompt_sources: tuple[str, ...]
    llm_message_sent: Any                   # raw provider request payload
    llm_message_received: Any               # (raw_text, parsed_payload) tuple
    model_response: Any                     # parsed payload
    agent_decision: dict | None             # decision_to_dict() result
    callbacks: tuple[CallbackAuditRecord, ...]
    skill_invocations: tuple[SkillInvocationRecord, ...]
    events: tuple[dict, ...]
```

**`to_jsonable() -> dict`**: Serializes the record to a JSON-serializable dict for JSONL output.

### 2.4 `CallbackAuditRecord`

```python
@dataclass(frozen=True, slots=True)
class CallbackAuditRecord:
    timestamp: str
    intent: str | None          # callback_intent from AgentDecision
    prompt: str                 # the question/message sent to caller
    target: str | None          # caller_id
    response: str | None        # the response received (set after resolution)
```

### 2.4b `SkillInvocationRecord`

```python
@dataclass(frozen=True, slots=True)
class SkillInvocationRecord:
    timestamp: str
    skill_name: str
    parameters: dict            # parameters from the invoke_skill decision
    inventory: tuple[str, ...]  # file paths discovered in the skill directory (not content)
```

One record is created per `invoke_skill` decision. The `inventory` field lists the paths of all files found in the skill's directory at load time — it records what was available, not what the model actually read. Individual resource file reads made via `read_skill_resource` are captured automatically through the existing tool tracing path (`ToolStartEvent` / `ToolEndEvent`).

`AgentCallAuditRecord` carries a `skill_invocations: tuple[SkillInvocationRecord, ...]` field. It starts as an empty tuple and is extended (via `dataclasses.replace()`) each time `handle_skill_invocation()` completes successfully, following the same progressive-construction pattern used for `callbacks` and `events`.

### 2.5 JSONL Output Format

Each line in `logs/trace-YYMMDD_HHMMSS.jsonl` is a JSON-serialized `AgentCallAuditRecord`. One line per agent run. Lines are appended in completion order. The file is never overwritten — each session creates a new timestamped file.

### 2.6 Wiring Audit Tracing

**Via `AgentHost.from_env()`:** Automatically creates and wires an `InMemoryAuditTracer` with output to `logs/`.

**Manually:**
```python
host.enable_audit_trace(output_dir=Path("logs"))
# Wires model driver trace callbacks to record LLM I/O
```

Audit tracing is also invoked directly from `Agent.run()` — the agent calls `host.audit_tracer.start_agent_call()` at the beginning and `finish_agent_call()` in the `finally` block.

---

## 3. LLM Trace Logging (`llm_trace_logging.py`)

### 3.1 Purpose

LLM trace logging captures the exact request and response payloads at the provider level — the actual JSON sent to and received from the LLM API. Useful for debugging prompt engineering, observing model behavior, and auditing provider-level interactions.

### 3.2 `LlmTraceLogger`

```python
class LlmTraceLogger:
    target: str         # "console", "file", or "both"
    output_dir: Path
```

**`log_provider_request(event: ProviderRequestTrace)`**:
```
[REQUEST] agent_id → provider_name/model_name
{json-formatted input_payload}
```

**`log_provider_response(event: ProviderResponseTrace)`**:
```
[RESPONSE] agent_id ← provider_name/model_name
{raw_text truncated or full}
```

**Console output:** Uses ANSI color codes (blue for requests, green for responses).

**File output:**
- `{agent_id}.log` — per-agent log file
- `llm-trace.log` — combined log for all agents

Both outputs can be active simultaneously (`"both"` target).

### 3.3 `attach_to_host(host, *, target, output_dir)`

Chains the logger onto existing model driver trace callbacks (preserving any existing callbacks):

```python
def attach_to_host(host: AgentHost, *, target: str, output_dir: Path) -> None:
    logger = LlmTraceLogger(target=target, output_dir=output_dir)

    # Chain onto existing callbacks
    existing_on_request = host.model_driver.on_request_trace
    existing_on_response = host.model_driver.on_response_trace

    def chained_request(event):
        if existing_on_request:
            existing_on_request(event)
        logger.log_provider_request(event)

    def chained_response(event):
        if existing_on_response:
            existing_on_response(event)
        logger.log_provider_response(event)

    host.model_driver.set_trace_callbacks(
        on_request=chained_request,
        on_response=chained_response,
    )
```

### 3.4 Enabling via CLI

```bash
python -m agent_framework --llm-trace console --instruction "..."
python -m agent_framework --llm-trace file --llm-trace-dir ./logs --instruction "..."
python -m agent_framework --llm-trace both --instruction "..."
```

---

## 4. Lifecycle Tracing (`trace_logging.py`)

`TraceLoggingBehavior` is a built-in `AgentBehavior` that logs lifecycle events to the console during development. It serves as both a debugging tool and a reference implementation for behaviors that hook into all lifecycle events.

```python
class TraceLoggingBehavior(AgentBehavior):
    def attach(self, agent: Agent) -> None:
        agent.onPreAgent += self._on_pre_agent      # → "[AGENT START] agent_id"
        agent.onPostAgent += self._on_post_agent    # → "[AGENT END] agent_id: result"
        agent.onPreTool += self._on_pre_tool        # → "[TOOL CALL] tool_name {params}"
        agent.onPostTool += self._on_post_tool      # → "[TOOL RESULT] tool_name: result"
        agent.onPreSubagent += self._on_pre_subagent    # → "[SUBAGENT CALL] subagent_id"
        agent.onPostSubagent += self._on_post_subagent  # → "[SUBAGENT RESULT] subagent_id: result"

def build_behavior() -> AgentBehavior:
    return TraceLoggingBehavior()
```

Use by adding `"trace_logging"` to the agent's sidecar JSON `behaviors` list, with `trace_logging.py` in the `behaviors/` directory.

---

## 5. Trace Viewer (`trace_viewer.html`)

`trace_viewer.html` (in the repo root) is a self-contained single-page web application for browsing JSONL audit trace files. No server required — open directly in a browser.

**Features:**
- Load a JSONL trace file by path
- Hierarchical tree view of agent call records
- Collapsible nodes at varying depth levels
- Warm parchment color scheme (cream backgrounds, brown accents)
- Displays system prompts, user prompts, LLM messages, decisions, callbacks, and events

---

## 6. Evaluation System (`evaluator.py`)

### 6.1 Overview

The evaluation system provides regression testing for agent behavior. Two evaluator implementations cover different testing styles:

| Evaluator | Input Format | Use Case |
|-----------|-------------|----------|
| `AgentPromptEvaluator` | XML file | Test agent behavior from natural language prompts |
| `OpenAiConversationEvaluator` | JSON file | Test raw provider-level inputs |

Both use LLM-based scoring (`OpenAiResultJudge`) and optional JSON Schema validation.

### 6.2 `RecordingAgentHost`

All evaluators use `RecordingAgentHost` to intercept and record interactions without modifying agent behavior:

```python
class RecordingAgentHost(AgentHost):
    interactions: list[RecordedInteraction]
    auto_input_response: str | None = None
```

**`RecordedInteraction`:**
```python
@dataclass(frozen=True)
class RecordedInteraction:
    kind: str           # "subagent_call", "tool_call", "callback", "user_input"
    caller_id: str | None
    callee_id: str | None
    payload: dict       # full parameters/arguments

    def to_dict(self) -> dict: ...
```

Overridden methods: `call_subagent`, `execute_tool`, `resolve_callback`, `request_user_input` — all append a `RecordedInteraction` then delegate to the base `AgentHost`.

**`auto_input_response`:** When set, `request_user_input()` returns this string instead of prompting the console. Required for non-interactive evaluation.

**`from_host(host) -> RecordingAgentHost`:** Creates a recording host from an existing `AgentHost`, copying all fields.

### 6.3 `AgentPromptEvaluator` — XML-Based Evaluation

**Input file format:**
```xml
<evaluation>
  <evaluator>Evaluate whether the agent correctly identified the answer.</evaluator>
  <schema>path/to/expected_schema.json</schema>  <!-- optional -->
  <scene>
    <prompt>What is the capital of France?</prompt>
    <expected>Paris</expected>
  </scene>
  <scene>
    <prompt>Find me a recipe for chocolate cake.</prompt>
    <expected>A chocolate cake recipe with ingredients and steps.</expected>
  </scene>
</evaluation>
```

**`parse_input_file(path) -> EvaluationInput`:**
```python
@dataclass(frozen=True)
class EvaluationInput:
    scenes: tuple[EvaluationScene, ...]
    evaluator_prompt: str        # the <evaluator> instruction for the LLM judge
    schema: dict | None          # parsed JSON Schema (if <schema> element present)
```

**`evaluate_file(path, *, agent_id) -> EvaluationSummary`:**

For each scene:
1. Creates a `RecordingAgentHost`
2. Runs the agent with `scene.prompt` as instruction
3. Calls `_evaluate_prompt(prompt, expected, agent_output, interactions, evaluator_prompt, schema)`
4. Writes JSON artifact: `{path.stem}.out.json`

**`_evaluate_prompt(...)` steps:**
1. **LLM judge:** `OpenAiResultJudge.score(evaluator_prompt, prompt, expected, result, interactions)` → `JudgeResult(score, output_text, payload)` — score is 1–10
2. **Schema validation (if schema provided):** `_schema_score(result_text, schema)` → 0 or 10
3. **Overall score:** Average of available scores
4. Returns `PromptScore`

### 6.4 `OpenAiConversationEvaluator` — JSON-Based Evaluation

For testing at the raw provider level. Sends exact provider input payloads:

**Input file format:**
```json
{
  "scenes": [
    {
      "input_json": {"messages": [...], "model": "gpt-4o-mini", ...},
      "expected_output": "The expected response text",
      "evaluation_criteria": "Check that the response is helpful and accurate.",
      "format_evaluation": "validate_json_output"
    }
  ]
}
```

**`evaluate_file(path) -> EvaluationSummary`:**

For each scene:
1. Sets `context.exact_input_payload = scene.input_json` — bypasses normal prompt assembly
2. Runs the model driver directly
3. Applies `format_evaluation` (if set): dispatches to named format validator
4. LLM-judges the result against `evaluation_criteria`

**Format evaluators** (`DEFAULT_FORMAT_EVALUATORS`):
- `"validate_json_output"` — checks output is valid JSON
- `"validate_json_object_output"` — checks output is a JSON object (dict, not array/primitive)

**`FormatEvaluator`** type alias: `Callable[[str], FormatEvaluationResult]`

```python
@dataclass(frozen=True)
class FormatEvaluationResult:
    score: float            # 0 or 10
    output_text: str        # explanation
    normalized_output: str  # processed output (e.g., stripped JSON)
```

### 6.5 `OpenAiResultJudge`

LLM-based evaluation scoring using OpenAI:

```python
class OpenAiResultJudge:
    def score(
        self, *,
        evaluator_prompt: str,
        prompt: str,
        expected: str,
        result: str,
        interactions: list[RecordedInteraction],
    ) -> JudgeResult
```

Constructs a judge prompt combining `evaluator_prompt`, `prompt`, `expected`, `result`, and `interactions`. Calls OpenAI to get a JSON response with a `score` field (1–10 scale). Returns `JudgeResult(score, output_text, payload)`.

### 6.6 Evaluation Output

**`EvaluationSummary`:**
```python
@dataclass(frozen=True)
class EvaluationSummary:
    prompt_scores: tuple[PromptScore, ...]
    overall_score: float

    def to_json(self) -> str: ...
    def to_markdown_table(self) -> str: ...
```

**`PromptScore`:**
```python
@dataclass(frozen=True)
class PromptScore:
    prompt: str
    expected: str
    agent_output: str
    llm_evaluator_output: str
    llm_evaluator_payload: dict
    schema_evaluator_output: str
    interactions: tuple[RecordedInteraction, ...]
    llm_score: float | None
    schema_score: float | None
    overall_score: float
```

### 6.7 Running Evaluations

**XML evaluation:**
```bash
python -m agent_framework --evaluate tests/eval.xml
python -m agent_framework --evaluate tests/eval.xml --agent specific_agent_id
```

**JSON/OpenAI evaluation:**
```bash
python -m agent_framework --evaluate-openai tests/eval.json --agent my_agent_id
```

Output artifact: `{eval_filename}.out.json` written alongside the input file.
