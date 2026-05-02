# Planning Replan Prompt Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `system.plan_execute.md` so the model is permitted to emit `submit_plan` (replan) during the reflect phase, not only in Phase 1.

**Architecture:** The runtime (`PlanningTurnDriver._reflect_phase`) already handles `submit_plan` during reflect (line 514 of `turn_driver.py`). The bug is entirely in the prompt template: line 23 says emit `final_message` when `<end_of_plan>` appears, and line 27 marks `submit_plan` as "Phase 1 only". Fixing those two lines and adding a regression test to `test_system_plan_execute.py` is the entire change.

**Tech Stack:** Python, pytest

---

## File Map

| Action | File |
|--------|------|
| Modify | `src/agent_framework/agents/system.plan_execute.md` |
| Modify | `tests/planning/test_system_plan_execute.py` |

---

### Task 1: Write the failing regression tests

These tests should fail on the current template and pass after the fix.

**Files:**
- Modify: `tests/planning/test_system_plan_execute.py`

- [ ] **Step 1: Write two failing tests at the bottom of the file**

Add after the last test in `tests/planning/test_system_plan_execute.py`:

```python
# ---------------------------------------------------------------------------
# Replan contract — submit_plan must be allowed during reflect (bug #94)
# ---------------------------------------------------------------------------

def test_template_does_not_restrict_submit_plan_to_phase_1():
    """submit_plan must be described as valid in reflect, not Phase 1 only."""
    # The phrase "Phase 1 only" next to submit_plan was the bug — it told the
    # model replanning is forbidden during reflect.
    import re
    # Find the submit_plan bullet in the decision kinds list
    match = re.search(r"- `submit_plan`[^\n]*", _SYSTEM_PLAN_EXECUTE_TEMPLATE)
    assert match, "submit_plan bullet not found in template"
    line = match.group(0)
    assert "Phase 1 only" not in line, (
        f"submit_plan description must not say 'Phase 1 only'; got: {line!r}"
    )


def test_template_end_of_plan_allows_replan():
    """Phase 3 description must mention submit_plan as a valid option alongside final_message."""
    # The bug: Phase 3 said 'emit final_message' unconditionally.
    # The fix: Phase 3 must also allow submit_plan for replan when results require it.
    phase3_idx = _SYSTEM_PLAN_EXECUTE_TEMPLATE.find("Phase 3")
    assert phase3_idx != -1, "Phase 3 heading not found in template"
    phase3_text = _SYSTEM_PLAN_EXECUTE_TEMPLATE[phase3_idx:]
    assert "submit_plan" in phase3_text, (
        "Phase 3 description must mention submit_plan as a valid replan option"
    )
```

- [ ] **Step 2: Run the tests to confirm they fail**

```
pytest tests/planning/test_system_plan_execute.py::test_template_does_not_restrict_submit_plan_to_phase_1 tests/planning/test_system_plan_execute.py::test_template_end_of_plan_allows_replan -v
```

Expected: FAIL — `AssertionError: submit_plan description must not say 'Phase 1 only'` and `AssertionError: Phase 3 description must mention submit_plan`

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/planning/test_system_plan_execute.py
git commit -m "test: add failing regression tests for replan prompt bug #94"
```

---

### Task 2: Fix the prompt template

**Files:**
- Modify: `src/agent_framework/agents/system.plan_execute.md`

Two targeted edits to the template:

**Edit A** — Phase 3 heading (line 23): Replace unconditional `final_message` mandate with a three-way choice that includes replanning.

**Edit B** — `submit_plan` bullet in the Decision kinds section (line 27): Remove "Phase 1 only" restriction.

- [ ] **Step 1: Fix Phase 3 — allow submit_plan alongside final_message**

In `src/agent_framework/agents/system.plan_execute.md`, find:

```
**Phase 3 — Reflect and finalize.** When `<end_of_plan>` appears, review results and emit `final_message` with your synthesized response.
```

Replace with:

```
**Phase 3 — Reflect and finalize.** When `<end_of_plan>` appears, review all step results. Then emit one of:
- `final_message` — when all objectives are met; include your synthesized response.
- `submit_plan` — when results reveal the plan was incomplete or needs revision; include a revised `plan` array with only the remaining steps not yet completed.
- `callback` — when a required input is missing and you cannot proceed without external clarification.

Do not emit `final_message` before all required objectives are met. Do not replan unnecessarily — only emit `submit_plan` when the results genuinely require it.
```

- [ ] **Step 2: Fix the submit_plan bullet — remove "Phase 1 only"**

In `src/agent_framework/agents/system.plan_execute.md`, find:

```
- `submit_plan` — submit your execution plan (Phase 1 only); requires `plan` field
```

Replace with:

```
- `submit_plan` — submit or revise the execution plan; requires `plan` field. Use in Phase 1 to emit the initial plan, or in Phase 3 (reflect) to replan when intermediate results require it.
```

- [ ] **Step 3: Run the full planning test suite**

```
pytest tests/planning/ -v
```

Expected: All tests pass, including the two new regression tests.

- [ ] **Step 4: Commit the fix**

```bash
git add src/agent_framework/agents/system.plan_execute.md
git commit -m "fix: allow submit_plan replan in reflect phase (Phase 3) — bug #94

The prompt template restricted submit_plan to 'Phase 1 only' and told the
model to unconditionally emit final_message when end_of_plan appeared.
The runtime already handled replan via _reflect_phase → _apply_replan;
only the model instruction was wrong."
```

---

### Task 3: Push

- [ ] **Step 1: Push to remote**

```bash
git push
```

---

## Verification

After completing all tasks, the following must be true:

1. `pytest tests/planning/` passes with no failures.
2. `system.plan_execute.md` no longer contains "Phase 1 only" adjacent to `submit_plan`.
3. Phase 3 section in the template lists `submit_plan` as a valid option.
4. The two new tests in `test_system_plan_execute.py` pass.
