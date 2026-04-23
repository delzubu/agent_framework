# Programmatic Workflow Execution Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a first-class framework API for deterministic programmatic agent workflows that reuses native subagent orchestration semantics, including hooks, audit events, transcript updates, callback handling, and trace parity.

**Architecture:** Refactor the existing `Agent.handle_subagent_call(...)` and `Agent.handle_subagent_calls(...)` logic into reusable internal orchestration helpers, then expose a small agent-owned workflow execution surface that behaviors can call from `before_run(...)`. The new API should execute framework-owned steps, not host-side shortcuts, so parent-visible artifacts remain identical to model-driven orchestration.

**Tech Stack:** Python 3.13, pytest, markdown-defined agents, `AgentBehavior`, `AgentHost`, runtime trace/audit events.

---

### Task 1: Lock down parity requirements with failing tests

**Files:**
- Modify: `tests/test_framework_runtime.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Write a failing test for programmatic single-subagent orchestration parity**

Add a test that:
- creates an agent with a behavior that short-circuits in `before_run(...)`
- calls the new programmatic workflow API instead of returning to the LLM loop
- runs one child agent
- asserts the parent trace/audit path still emits the same subagent lifecycle artifacts as native `call_subagent`
- asserts the parent run receives transcript/conversation updates for `<subagent_call>` and `<subagent_result>`

**Step 2: Run the test to verify it fails**

Run: `pytest tests/test_framework_runtime.py::test_programmatic_single_subagent_workflow_matches_native_trace_contract -q`

Expected: FAIL because no programmatic workflow execution API exists yet.

**Step 3: Write a failing test for programmatic parallel-subagent orchestration parity**

Add a second test that:
- uses the same behavior-driven short-circuit pattern
- runs a parallel child batch
- asserts `subagent_batch_started` / `subagent_batch_finished` audit events still appear
- asserts the parent prompt/transcript gets `<subagent_results>`
- asserts callback-capable batch machinery is still reached through framework-owned execution

**Step 4: Run the second test to verify it fails**

Run: `pytest tests/test_framework_runtime.py::test_programmatic_parallel_workflow_matches_native_batch_trace_contract -q`

Expected: FAIL because the reusable orchestration surface is not implemented.

**Step 5: Commit**

```bash
git add tests/test_framework_runtime.py
git commit -m "test: define programmatic workflow parity expectations"
```

### Task 2: Extract reusable internal subagent orchestration helpers

**Files:**
- Modify: `src/agent_framework/agents/agent.py`
- Modify: `src/agent_framework/agents/agent_host_protocol.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Extract single-subagent orchestration into an internal helper**

In `src/agent_framework/agents/agent.py`, factor `handle_subagent_call(...)` into:
- validation
- hook setup
- audit/transcript/conversation emission
- `host.call_subagent(...)`
- result/error merge

Keep the public behavior unchanged by making `handle_subagent_call(...)` delegate to the helper.

**Step 2: Extract batch orchestration into an internal helper**

In the same file, factor `handle_subagent_calls(...)` so the batch orchestration path can be invoked without constructing an `AgentDecision` first.

Keep existing decision-driven behavior unchanged.

**Step 3: Add a minimal typed protocol surface for programmatic execution**

Update `src/agent_framework/agents/agent_host_protocol.py` only if needed for helper typing. Do not widen it more than required.

**Step 4: Run focused tests**

Run: `pytest tests/test_framework_runtime.py -q`

Expected: existing `call_subagent` and `call_subagents` tests still pass.

**Step 5: Commit**

```bash
git add src/agent_framework/agents/agent.py src/agent_framework/agents/agent_host_protocol.py tests/test_framework_runtime.py
git commit -m "refactor: extract reusable subagent orchestration helpers"
```

### Task 3: Add first-class programmatic workflow execution

**Files:**
- Modify: `src/agent_framework/agents/agent.py`
- Modify: `src/agent_framework/agent.py`
- Modify: `src/agent_framework/__init__.py`
- Test: `tests/test_framework_runtime.py`

**Step 1: Add an agent-owned programmatic workflow API**

Implement a small public API on `Agent`, for example:

```python
agent.execute_programmatic_workflow(
    host=host,
    run=run,
    caller_id=caller_id,
    workflow=workflow,
)
```

The API must:
- support single-subagent and batch-subagent workflow steps
- delegate to the extracted internal helpers
- preserve parent agent identity, run lineage, and callback semantics

**Step 2: Support a minimal structured workflow model**

Add a small workflow surface that is enough for this first iteration:
- `call_subagent`
- `call_subagents`
- `return`

Do not build a full DSL engine yet. Keep the model easy to extend later.

**Step 3: Export the new API from the package surface**

If the new workflow classes or dataclasses are part of the public feature, expose them through:
- `src/agent_framework/agent.py`
- `src/agent_framework/__init__.py`

**Step 4: Make the parity tests pass**

Run:
- `pytest tests/test_framework_runtime.py::test_programmatic_single_subagent_workflow_matches_native_trace_contract -q`
- `pytest tests/test_framework_runtime.py::test_programmatic_parallel_workflow_matches_native_batch_trace_contract -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add src/agent_framework/agents/agent.py src/agent_framework/agent.py src/agent_framework/__init__.py tests/test_framework_runtime.py
git commit -m "feat: add programmatic workflow execution"
```

### Task 4: Prove callback and evaluator trace compatibility

**Files:**
- Modify: `tests/test_framework_runtime.py`
- Modify: `tests/test_evaluator_sessions.py`
- Test: `tests/test_framework_runtime.py`
- Test: `tests/test_evaluator_sessions.py`

**Step 1: Add a focused callback-resume regression**

Add a test showing that a programmatic workflow path still reuses native callback handling, rather than bypassing it through a host shortcut.

**Step 2: Add evaluator-side usage/trace compatibility coverage if needed**

If the new audit/runtime events affect evaluator aggregation, add a focused test in `tests/test_evaluator_sessions.py` to prove the evaluator still consumes the same trace structure.

**Step 3: Run focused regressions**

Run:
- `pytest tests/test_framework_runtime.py tests/test_evaluator_sessions.py -q`

Expected: PASS.

**Step 4: Commit**

```bash
git add tests/test_framework_runtime.py tests/test_evaluator_sessions.py
git commit -m "test: cover programmatic workflow callback parity"
```

### Task 5: Document the supported pattern

**Files:**
- Modify: `docs/guides/using-agent-framework.md`
- Modify: `docs/pages/reference/developer-documentation.md`
- Modify: `tests/test_docs_pages_config.py`
- Test: `tests/test_docs_pages_config.py`

**Step 1: Document the feature as a supported behavior pattern**

Describe:
- when to use programmatic workflows instead of an LLM decision loop
- that `before_run(...)` may now delegate to framework-owned workflow execution
- the currently supported workflow step kinds
- parity guarantees and current limits

**Step 2: Add docs-presence assertions**

Update `tests/test_docs_pages_config.py` so the new public feature is covered by docs checks.

**Step 3: Run docs tests**

Run: `pytest tests/test_docs_pages_config.py -q`

Expected: PASS.

**Step 4: Commit**

```bash
git add docs/guides/using-agent-framework.md docs/pages/reference/developer-documentation.md tests/test_docs_pages_config.py
git commit -m "docs: document programmatic workflow execution"
```

### Task 6: Final verification

**Files:**
- Modify: none
- Test: `tests/test_framework_runtime.py`
- Test: `tests/test_evaluator_sessions.py`
- Test: `tests/test_docs_pages_config.py`

**Step 1: Run the focused verification suite**

Run:
- `pytest tests/test_framework_runtime.py tests/test_evaluator_sessions.py tests/test_docs_pages_config.py -q`

Expected: PASS.

**Step 2: Run a broader runtime safety pass**

Run:
- `pytest tests/test_parallel_subagents.py tests/test_runtime_trace_behavior.py tests/test_headless.py -q`

Expected: PASS.

**Step 3: Commit final cleanup if needed**

```bash
git add -A
git commit -m "chore: finalize programmatic workflow execution"
```
