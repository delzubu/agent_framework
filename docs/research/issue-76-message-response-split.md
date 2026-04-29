# Issue #76: Structured response field instead of serialized JSON `message`

## Summary

**Recommendation: introduce a first-class `response` field on `AgentResult` and `AgentDecision`, orthogonal to `message`, and remove the current practice of serializing structured output into `message`.**

## Problem

The current `AgentResult.message` / `AgentResult.parameters` contract conflates two concerns:

1. **Human-readable prose** — a natural-language summary for the caller (parent agent, evaluator, or user).
2. **Structured payload** — typed output the caller processes programmatically.

These are served by the same `message` field today, via a three-layer encoding:

- `system.json_object.md:75-76` instructs the model: "If `parameters` is populated, `message` must contain the serialized JSON (escaped as string). In this case, `parameters.message` may hold a user-facing message."
- `_subagent_result_payload` (`agents/agent.py:152-176`) re-wraps `{message, ...params}` into JSON again when forwarding to a parent conversation: `json.dumps({"message": message, **parameters})`.
- The evaluator's `select_agent_result_field` must then parse `message` as JSON to extract structured fields, or fall back to `parameters` depending on `injection_mode`.

The resulting failure modes observed in planning rollout:

- Models emit `parameters` without re-serializing them into `message` (or vice versa) — either the prose disappears from the caller's view or the structured payload is unreachable.
- Parent agents receiving a subagent result see a JSON string where they expected prose, requiring a second parse.
- `AgentDecision.parameters` (tool/subagent *call inputs*) and `AgentResult.parameters` (structured *output*) share the same field name with different semantics — contributors conflate them, models conflate them.

## Proposed contract

| Field | Type | Responsibility |
|---|---|---|
| `message` | `str` | Human-readable text only. **Never** holds JSON. Required for text-mode agents. |
| `response` | `dict[str, Any] \| None` | The structured payload. Replaces `AgentResult.parameters`. |

Rules:
- The framework **never** auto-serializes `response` into `message` or vice versa.
- A model emitting a `final_message` with structured output sets `response`. A model emitting prose sets `message`. Both fields may be set simultaneously (short prose summary + full structured payload).
- `AgentDecision.parameters` retains its current meaning (call inputs for tool/subagent decisions). Only the *result* side changes.
- Subagent handoff emits a typed envelope: `<subagent_result message="...">{json}</subagent_result>`. Parents consume the channel they need.

## Decision rationale

The alternative — keeping a single `message` field — was rejected because:
- It forces the model to know which serialization mode is active and double-write correctly; models drift from this instruction reliably.
- Every downstream consumer has to re-parse a string that may or may not be valid JSON, and then decide which of `message`, `parameters`, and `parsed_message` to use as the authoritative value. That is O(consumers × modes) guessing, not a contract.
- Persistence and tracing store strings; round-trips lose typing and break dot-traversal (`select_agent_result_field`).

## Affected surfaces

| Surface | Change |
|---|---|
| `agents/agent_result.py` | Add `response: dict \| None`. Phase 2: remove `parameters`, `parameters_injection`. |
| `agents/agent_decision.py` | Add `response: dict \| None` to `AgentDecision`. Parser reads `response` first, falls back to `parameters` with deprecation log. |
| `agents/agent.py` | `_subagent_result_payload` replaced by typed envelope helper. `handle_final_message` populates `response`. |
| `agents/result_envelope.py` (new) | `render_subagent_envelope(*, message, response) -> str`. |
| `agents/system*.md` (5 files) | Remove "serialize `parameters` into `message`" instruction. Add "put structured output in `response`". |
| `planning/turn_driver.py` | `_make_result` populates `response`. Subagent step result extracted via envelope. |
| `evaluator.py`, `evaluation.py` | `select_agent_result_field` traverses `response.<path>` first, then `parameters.<path>` (Phase 1). `_agent_result_payload` includes `response`. |
| `audit_trace.py`, `runtime_trace_behavior.py` | Record `response` alongside `message` in trace payloads. |
| Tests | New `tests/test_agent_response_field.py`. Existing tests updated Phase 2. |

## Migration strategy

### Phase 1 — additive (non-breaking)

- Add `response` field to `AgentResult` and `AgentDecision` (optional, `None` default).
- Parser: accepts `response` from model output; if absent, mirrors `parameters` into `response` and logs a deprecation warning (no silent swallowing — the warning is observable via log level WARNING).
- Result builders (`handle_final_message`, `PlanningTurnDriver._make_result`) populate both `response` (from `decision.response`) and `parameters` (mirror) for one release.
- Envelope helper introduced alongside old `_subagent_result_payload`; old helper routes to envelope when `response` is present.
- Evaluator: `select_agent_result_field` tries `response.<path>` first, then `parameters.<path>`.
- System prompts updated to teach the new contract.

### Phase 2 — breaking cleanup

- Drop `AgentResult.parameters` and the mirror.
- Drop `_subagent_result_payload` and `parameters_injection`.
- Strict validation: `final_message` with non-empty structured payload must populate `response`; absence raises `ValueError`.
- System prompts: remove all legacy `parameters` result instructions.
- Update all tests.

## Decisions made

| Question | Decision |
|---|---|
| Type of `response` | `dict[str, Any] \| None` only. Lists/scalars wrap in `{"value": ...}` at the model prompt level. |
| Callback contract | Unchanged (`kind` + `callback_intent`). Out of scope. |
| Empty `message` | Required for text mode; optional otherwise; never required to mirror `response`. |
| Compat window | Two commits (Phase 1 then Phase 2) in the same branch/PR. |
| `AgentDecision.parameters` | Retained (call inputs). Only the result side changes. |

## Pros and cons

**Pros:**
- Model instruction is mechanical and unambiguous. No double-write.
- Evaluator, tracing, and persistence treat structured data as data (not strings).
- `_subagent_result_payload` and `parameters_injection` disappear; the framework no longer makes semantic decisions about how to merge two opaque bags.
- `select_agent_result_field` becomes a simple dot-traversal with a well-defined primary target.

**Cons:**
- Phase 2 is a breaking change on `AgentResult` — any code reading `result.parameters` breaks. Mitigation: Phase 1 mirror + one-release window.
- System prompt updates require re-testing every agent that relies on the old double-write convention.
- The typed envelope requires XML parsing on the parent side (or structured tagging); the current string-in-string approach lets the parent treat results as opaque text. Mitigation: envelope is XML-tagged, parseable with standard `re` or `xml.etree`; the existing `<subagent_result>` message shape is already XML-tagged in some prompt templates.
