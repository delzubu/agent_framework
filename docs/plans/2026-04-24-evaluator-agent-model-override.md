# Evaluator Agent Model Override Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add evaluator-side model override controls for the agent under test, available from both the web UI and CLI, with two explicit scopes: `root_only` (only the tested/top-level agent uses the override) and `all_agents` (every agent in that run uses the override). The override must default to unselected, must be optionally prefilled by an initializer, and the UI should offer a dropdown of model names parsed from `.env`.

**Architecture:** Keep model selection responsibilities in the runtime layers that already resolve agents and hosts instead of adding evaluator-only branching. Use two override paths: a host-wide `all_agents` override that forces every loaded agent definition to use the selected model tuple, and a top-level `root_only` override that applies only to the root invocation clone inside `AgentHost.run_agent(...)`, leaving child agent resolution untouched. The evaluator should pass a small run-scoped override contract through session/web/CLI layers, and the web UI should fetch model options from the server based on the selected `.env`.

**Tech Stack:** Python 3.13, FastAPI, vanilla JS evaluator UI, `HostConfig`, `AgentRegistry`, `AgentHost`, pytest.

---

### Task 1: Define the override contract and lock down semantics with tests

**Files:**
- Modify: `tests/test_evaluator_sessions.py`
- Modify: `tests/test_evaluator_cli.py`
- Modify: `tests/test_agent_registry.py`
- Modify: `tests/test_framework_runtime.py`

**Step 1: Add failing tests for root-only override semantics**

Add a focused runtime test that proves:
- the root/tested agent can be forced to one model tuple for a single run
- subagents invoked from that root run still use their configured model sources
- the override does not mutate the cached registry definition for later runs

This should explicitly cover an agent with a companion/runtime `model` setting so the test protects the requested behavior, not just `DEFAULT_MODEL`.

**Step 2: Add failing tests for all-agents override semantics**

Add a registry/host test that proves an `all_agents` override supersedes:
- `.env` `DEFAULT_MODEL`
- `.env` `AGENT_MODELS`
- per-agent companion/runtime `model`

for all agents loaded during that run.

**Step 3: Add failing evaluator tests for transport and defaults**

Add evaluator tests that prove:
- the websocket `run` payload can carry `agent_model_override` and `agent_model_override_scope`
- initializer defaults can prefill those fields when explicitly defined
- absent initializer defaults keep the override unselected
- CLI parser accepts the new flags for `web`, `run`, and `evaluate`

**Step 4: Run the focused tests to verify the gap**

Run:

```powershell
pytest tests/test_framework_runtime.py tests/test_agent_registry.py tests/test_evaluator_sessions.py tests/test_evaluator_cli.py -q
```

Expected: failures because the evaluator/runtime does not yet expose the requested override behavior.

### Task 2: Add clean runtime support for root-only vs all-agents override

**Files:**
- Modify: `src/agent_framework/agent_registry.py`
- Modify: `src/agent_framework/host.py`
- Modify: `src/agent_framework/config.py` if a typed override helper or dataclass is warranted
- Modify: `tests/test_framework_runtime.py`
- Modify: `tests/test_agent_registry.py`

**Step 1: Introduce a small typed override representation**

Add a narrow internal model override contract, for example:
- selected model tuple
- scope enum/value: `root_only` or `all_agents`

Keep it runtime-scoped; do not bake evaluator concepts into core runtime code.

**Step 2: Implement the `all_agents` path at agent-load time**

Update agent loading so an explicit runtime-wide override can force `Agent.from_markdown(..., model_override=...)` for every loaded agent definition. This is the only way to reliably override companion/runtime `model` declarations as well as `.env` defaults.

Do not implement this by mutating `HostConfig.default_model` alone; that only changes fallback behavior and does not satisfy the requested semantics.

**Step 3: Implement the `root_only` path at root invocation time**

Update `AgentHost.run_agent(...)` so it can apply a one-run override only to the root invocation clone before execution starts. Child agent resolution must remain unchanged.

Keep this out of `agent.py`; the selection belongs in host/registry resolution.

**Step 4: Preserve current behavior when no override is selected**

No selection must continue to mean:
- root agent uses current resolution order
- subagents use current resolution order
- no initializer default means no override shown/applied

**Step 5: Re-run focused runtime tests**

Run:

```powershell
pytest tests/test_framework_runtime.py tests/test_agent_registry.py -q
```

Expected: the new root-only and all-agents semantics pass without regressing existing model resolution.

### Task 3: Thread the override through evaluator runtime and initializer loading

**Files:**
- Modify: `src/agent_framework_evaluator/runtime/session_runner.py`
- Modify: `src/agent_framework_evaluator/app.py`
- Modify: `src/agent_framework_evaluator/initializer_catalog.py`
- Modify: `src/agent_framework_evaluator/cli.py`
- Modify: `tests/test_evaluator_sessions.py`
- Modify: `tests/test_evaluator_cli.py`

**Step 1: Extend `SessionRunner.run_once(...)`**

Allow the evaluator runner to accept:
- `agent_model_override`
- `agent_model_override_scope`

and pass them to the underlying host creation/root-run path without affecting evaluator scoring model selection (`DEFAULT_EVAL_MODEL` remains separate).

**Step 2: Extend initializer defaults cleanly**

Add dedicated initializer accessors for the agent-under-test override, for example:
- `DEFAULT_AGENT_MODEL_OVERRIDE` / `get_default_agent_model_override()`
- `DEFAULT_AGENT_MODEL_OVERRIDE_SCOPE` / `get_default_agent_model_override_scope()`

Do not overload `DEFAULT_EVAL_MODEL`; that is for the evaluator/scoring LLM only.

If an initializer does not define these values, return empty/default values that keep the UI unselected.

**Step 3: Extend web and headless CLI surfaces**

Add CLI flags for:
- `web`: defaults injected into the UI session bootstrap
- `run`: actual execution override
- `evaluate`: actual execution override for case runs

Suggested flags:
- `--agent-model-override`
- `--agent-model-override-scope {root_only,all_agents}`

**Step 4: Extend the websocket/API payload contract**

The browser `run` payload should carry the override fields, and the initializer/defaults endpoints should return:
- available model options from `.env`
- selected override model if one is configured by initializer or CLI default
- selected scope if one is configured

Keep the payload explicit rather than encoding scope into the model string.

**Step 5: Re-run evaluator backend tests**

Run:

```powershell
pytest tests/test_evaluator_sessions.py tests/test_evaluator_cli.py -q
```

Expected: evaluator runtime and CLI now pass the override contract through cleanly.

### Task 4: Add the web UI controls and wire them to existing evaluator flow

**Files:**
- Modify: `src/agent_framework_evaluator/web/index.html`
- Modify: `src/agent_framework_evaluator/web/app.js`
- Modify: `src/agent_framework_evaluator/web/styles.css` if layout needs minor support
- Modify: `src/agent_framework_evaluator/app.py`
- Modify: `tests/test_evaluator_cli.py` or evaluator app tests where endpoint coverage belongs

**Step 1: Add explicit UI controls**

Add two controls in the left rail:
- a model dropdown whose options come from `.env` `DEFAULT_MODEL` parsing (`HostConfig.default_model`)
- a scope selector with `Tested agent only` and `Whole run`

The model dropdown must include an empty option and remain unselected by default.

**Step 2: Fetch model options from the server**

Expose the parsed `.env` model list through a server response instead of duplicating `.env` parsing in the browser.

Refresh options when the env-path changes so the dropdown always matches the currently selected environment.

**Step 3: Apply initializer defaults conservatively**

When an initializer provides override defaults:
- preselect the matching model if it exists in the server-provided options
- preselect the requested scope

When it does not:
- keep the model dropdown empty
- keep the scope at its inert default

Do not auto-select the first model from `.env`.

**Step 4: Send the override on run**

Update the websocket `run` message so it sends the new fields only as plain structured data. No additional client-side model logic should exist beyond:
- storing selected values
- sending them with the run request
- reflecting initializer/default responses

**Step 5: Verify web defaults and endpoint behavior**

Add/adjust endpoint tests so the UI bootstrap payload contains:
- `env_path`
- agent/initializer defaults
- model option list
- optional CLI-provided default selection

### Task 5: Cover docs and end-to-end behavior

**Files:**
- Modify: `docs/guides/using-agent-framework.md`
- Modify: `docs/pages/reference/developer-documentation.md`
- Modify: `src/agent_framework_skills/skills/authoring-agents/references/evaluator-usage.md`
- Modify: `tests/test_docs_pages_config.py`

**Step 1: Document the two override scopes**

Explain clearly:
- `root_only`: only the tested agent is forced to the chosen model
- `all_agents`: every agent in the run is forced to the chosen model

Also note that evaluator scoring model selection remains a separate feature.

**Step 2: Document initializer hooks and CLI flags**

Add examples showing:
- how an initializer can prefill agent override model/scope
- how CLI users can set the override in `web`, `run`, and `evaluate`
- that leaving the override blank preserves configured behavior

**Step 3: Run the relevant verification suite**

Run:

```powershell
pytest tests/test_framework_runtime.py tests/test_agent_registry.py tests/test_evaluator_sessions.py tests/test_evaluator_cli.py tests/test_docs_pages_config.py -q
```

If UI endpoint coverage reveals gaps, add the missing tests before considering the feature complete.

### Design Notes

- Treat `.env` model options for the UI as the parsed `DEFAULT_MODEL` tuple only. This keeps the dropdown deterministic and aligned with the user request. Do not attempt to mine companion JSON or `AGENT_MODELS` into the UI selector in the first pass.
- Keep agent-under-test override selection separate from evaluator/scoring LLM selection. They solve different problems and should not share field names or initializer hooks.
- Avoid mutating cached `Agent` objects for root-only override. Apply the override to the per-run clone used for execution.
- Avoid hiding the override scope in evaluator-only code paths. The runtime must own the semantics so UI and CLI share the same implementation.
