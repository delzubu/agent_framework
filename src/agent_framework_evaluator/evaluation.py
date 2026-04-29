"""Post-run LLM evaluation for the agent evaluator web UI."""

from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_framework.errors import ModelDriverError
from agent_framework.host import AgentHost
from agent_framework.model import (
    DEFAULT_RESPONSE_MODE,
    AsyncToSyncAdapter,
    ModelContext,
    merge_runtime_system_into_messages,
)

CASE_NO_CALLBACKS_POSTFIX = """

## MANDATORY ASSUMPTIONS RULE
YOU MUST NOT ask for any further questions or clarification from the user.
Make assumptions to provide the best answer possible, given this input.
"""

EVALUATOR_SYSTEM_PROMPT = """
Evaluate the LLM agent output very critically. The input contains the agent's system prompt, the user \
prompt, optional evaluation criteria, and the agent result, each in XML tags. \
Use the evaluation criteria as guidance but also apply your own reasoning.

Score 1-10:
  1-3  critical failure or fundamental task misunderstanding
  4-5  partial completion with significant gaps
  6-7  some requirements are missing but there are some that are met
  8-9  all explicit requirements covered; minor improvements possible
  10   complete and accurate match to all criteria

You must be very strict and critical in your evaluation. The purpose of this excersise is to identify bugs and shortcomings early in the agent development process.

Return a JSON object with no markdown fences and exactly these keys:

{
  "score": integer 1-10,
  "evaluation": [
    {
        "criteria": "criteria that was checked",
        "passed": boolean,
        "reason": "reason for the passed or failed"
    }
  ],
  "result": text describing the overall evaluation of results
}

You must include at least 5 criteria that were checked by you (excluding the evaluation criteria provided) and all the criteria that was provided in the evaluation criteria. ideally, I expect at least 8-10 distinct criteria that was evaluated.

Score must be close to the proportion of passed criteria (scaled to 1-10), but if there \
is a strong reason to deviate from the proportion, you can adjust it by +/- 2 at maximum. \
Usually, you will reduce it because critical output is missing or format is incorrect. 

If there is an adjustment, you must explicitly explain the reason for the adjustment in the result, a generic text like "there are minor inconsistencies" is not sufficient. Name each and every specific item to be resolved to reduce the adjustment (or items that are outstanding and contribute to the adjustment).

If all the criteria is met and you don't see any potential improvement, the score must be 10.
"""

EvaluatorLogCallback = Callable[[dict[str, Any]], None]


def _emit_evaluator_log(
    callback: EvaluatorLogCallback | None,
    *,
    level: str,
    kind: str,
    title: str,
    summary: str,
    payload: dict[str, Any],
) -> None:
    """Emit a structured evaluator diagnostic if the caller supplied a sink."""
    if callback is None:
        return
    callback(
        {
            "level": level,
            "kind": kind,
            "title": title,
            "summary": summary,
            "payload": payload,
        }
    )


def _env_key_values(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        v = raw_value.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
            v = v[1:-1]
        out[key.strip()] = v
    return out


def _eval_model_override(env_path: str | Path) -> tuple[str, ...] | None:
    vals = _env_key_values(Path(env_path))
    raw = vals.get("AGENT_EVAL_MODEL", "").strip()
    if not raw:
        return None
    t = tuple(m.strip() for m in raw.split(",") if m.strip())
    return t or None


def _content_to_str(content: Any) -> str:
    """Flatten OpenAI-style message content (str, or list of text/image parts) to a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                ptype = part.get("type")
                if ptype == "text" and "text" in part:
                    parts.append(str(part["text"]))
                elif "text" in part:
                    parts.append(str(part["text"]))
                elif ptype == "image_url":
                    parts.append("[image]")
        return "\n".join(p for p in parts if p)
    return str(content)


def _normalise_role(role: Any) -> str:
    if role is None:
        return ""
    val = getattr(role, "value", None)
    if isinstance(val, str):
        return val.strip().lower()
    s = str(role).strip().lower()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s


def _normalise_to_messages(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    if not isinstance(payload, dict):
        return []
    # Prefer a non-empty message list. OpenAI Responses traces use "input"; chat
    # completions use "messages". If "input" is present but wrong-shaped/empty,
    # fall back to "messages" (fixes dropped follow-up user turns).
    for key in ("input", "messages"):
        v = payload.get(key)
        if isinstance(v, list):
            msgs = [m for m in v if isinstance(m, dict)]
            if msgs:
                return msgs
    return []


def extract_first_llm_request_prompts(input_payload: Any) -> dict[str, Any]:
    """Extract system and every ``user`` message from the first provider request (in order).

    Multiple user turns are common (task text, then skills catalog, etc.).
    """
    msgs = _normalise_to_messages(input_payload)
    system = ""
    users: list[str] = []
    for m in msgs:
        role = _normalise_role(m.get("role"))
        content = m.get("content")
        c = _content_to_str(content)
        if role == "system" and not system:
            system = c
        elif role == "user":
            users.append(c)
    return {
        "system_prompt": system,
        "user_prompt": users[0] if users else "",
        "user_messages": users,
    }


def extract_initial_prompts(input_payload: Any) -> dict[str, str]:
    """Extract first system and first user message (evaluation / backward compatibility)."""
    d = extract_first_llm_request_prompts(input_payload)
    return {"system_prompt": d["system_prompt"], "user_prompt": d["user_prompt"]}


def format_eval_input(
    system_prompt: str,
    user_prompt: str,
    criteria: str,
    agent_message: str,
) -> str:
    """Build XML-tagged user content for the evaluator model."""
    return (
        f"<system_prompt>\n{system_prompt}\n</system_prompt>\n\n"
        f"<user_prompt>\n{user_prompt}\n</user_prompt>\n\n"
        f"<evaluation_criteria>\n{criteria}\n</evaluation_criteria>\n\n"
        f"<agent_result>\n{agent_message}\n</agent_result>\n"
    )


def _coerce_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _stringify_result_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _traverse_dict(d: dict, parts: list[str]) -> Any:
    current: Any = d
    for part in parts:
        if not part:
            continue
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


_MISSING = object()


def select_agent_result_field(agent_result: Any, field_name: Any) -> str | None:
    """Select *field_name* (dot-delimited path) from *agent_result*.

    Returns ``None`` when the path does not exist in the result dict, so callers
    can distinguish a missing field from an empty value and raise an appropriate
    error.  Returns the full stringified payload when *field_name* is ``"."``.

    For structured paths (e.g. ``response.status``), ``response`` is checked
    before ``parameters`` so that the new typed channel takes precedence.
    """
    field = str(field_name or "message").strip() or "message"
    if field == ".":
        return _stringify_result_value(agent_result)
    if isinstance(agent_result, str):
        return agent_result
    if isinstance(agent_result, dict):
        parts = [p for p in field.split(".") if p]
        # Try the path directly first (handles "message", "response", "response.x", …).
        value = _traverse_dict(agent_result, parts)
        if value is not _MISSING:
            return _stringify_result_value(value)
        # For bare sub-paths (e.g. "status", "count") try "response.<path>" first,
        # then "parameters.<path>" (decision call inputs still live there).
        if len(parts) >= 1 and parts[0] not in ("response", "parameters", "message", "status"):
            for container_key in ("response", "parameters"):
                container = agent_result.get(container_key)
                if isinstance(container, dict):
                    value = _traverse_dict(container, parts)
                    if value is not _MISSING:
                        return _stringify_result_value(value)
        return None
    return _stringify_result_value(agent_result)


def failed_evaluator_result(error_message: str) -> dict[str, Any]:
    """Return a zero-score result with the error in verdict and criterion reasoning."""
    msg = (error_message or "").strip() or "Evaluator failed."
    return {
        "score": 0.0,
        "overall_verdict": msg,
        "evaluation": [
            {
                "criteria": "Evaluator run",
                "passed": False,
                "reason": msg,
            }
        ],
    }


def parse_eval_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Map evaluator LLM JSON to API / UI fields."""
    raw_score = payload.get("score", 7.0)
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 7.0
    score = min(10.0, max(1.0, score))

    result_text = _coerce_str(payload.get("result", ""))
    evaluation_rows: list[dict[str, Any]] = []
    raw_ev = payload.get("evaluation", [])
    if isinstance(raw_ev, list):
        for row in raw_ev:
            if not isinstance(row, dict):
                continue
            raw_passed = row.get("passed", False)
            if isinstance(raw_passed, str):
                passed = raw_passed.strip().lower() in ("true", "1", "yes")
            else:
                passed = bool(raw_passed)
            evaluation_rows.append(
                {
                    "criteria": _coerce_str(row.get("criteria")),
                    "passed": passed,
                    "reason": _coerce_str(row.get("reason")),
                }
            )

    if not result_text:
        legacy = payload.get("verdict")
        if isinstance(legacy, str) and legacy.strip():
            result_text = legacy

    if not evaluation_rows:
        hits = payload.get("hits", [])
        misses = payload.get("misses", [])
        if isinstance(hits, list):
            for h in hits:
                if isinstance(h, str) and h.strip():
                    evaluation_rows.append({"criteria": h.strip(), "passed": True, "reason": ""})
        if isinstance(misses, list):
            for m in misses:
                if isinstance(m, str) and m.strip():
                    evaluation_rows.append({"criteria": m.strip(), "passed": False, "reason": ""})

    return {
        "score": score,
        "overall_verdict": result_text,
        "evaluation": evaluation_rows,
    }


def _sync_driver_for_evaluator(host: AgentHost) -> Any:
    """Mirror :meth:`AgentHost.get_model_driver` without requiring an Agent instance."""
    raw = host.get_model_driver_raw()
    if asyncio.iscoroutinefunction(getattr(raw, "decide", None)):
        return AsyncToSyncAdapter(raw)
    return raw


def _call_evaluator(
    fn: Callable[..., Any],
    prompt: str,
    agent_message: str,
    flags: set[str],
) -> Any:
    """Call fn with flags kwarg if its signature accepts it, otherwise without."""
    import inspect

    sig = inspect.signature(fn)
    params = sig.parameters
    accepts_flags = "flags" in params or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    if accepts_flags:
        return fn(prompt, agent_message, flags=flags)
    return fn(prompt, agent_message)


def run_code_evaluation(
    code_evaluator: Callable[..., Any],
    *,
    prompt: str,
    agent_message: str,
    flags: set[str] | None = None,
) -> dict[str, Any] | None:
    """Run a programmatic evaluator.

    Returns None if the evaluator opts out (returns None); otherwise the parsed
    result dict. Raises ValueError for non-dict, non-None returns.
    """
    raw = _call_evaluator(code_evaluator, prompt, agent_message, flags or set())
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("code_evaluator must return a dict or None.")
    return parse_eval_response(raw)


def run_code_evaluations(
    code_evaluators: list[Callable[..., Any]],
    *,
    prompt: str,
    agent_message: str,
    flags: set[str] | None = None,
) -> list[dict[str, Any] | None]:
    """Run all code evaluators sequentially.

    Returns one entry per evaluator. None entries (opted-out evaluators) are
    excluded from score averaging by callers.
    """
    _flags = flags or set()
    results: list[dict[str, Any] | None] = []
    for fn in code_evaluators:
        result = run_code_evaluation(fn, prompt=prompt, agent_message=agent_message, flags=_flags)
        if result is not None:
            result["score"] = min(10.0, max(0.0, float(result["score"])))
        results.append(result)
    return results


def run_evaluation(
    *,
    env_path: str | Path,
    evaluator_prompt: str,
    agent_message: str,
    system_prompt: str = "",
    user_prompt: str = "",
    model_override: str | tuple[str, ...] | None = None,
    log_callback: EvaluatorLogCallback | None = None,
) -> dict[str, Any]:
    """Call the evaluator LLM once. Does not run the agent loop."""
    env_path = Path(env_path)
    override = model_override if model_override is not None else _eval_model_override(env_path)
    host = AgentHost.from_env(env_path, model_override=override)
    user_content = format_eval_input(
        system_prompt,
        user_prompt,
        evaluator_prompt,
        agent_message,
    )
    _emit_evaluator_log(
        log_callback,
        level="debug",
        kind="evaluator.input_prepared",
        title="Evaluator input prepared",
        summary="Prepared input for evaluator scoring.",
        payload={
            "env_path": str(env_path),
            "model_override": override,
            "evaluator_prompt": evaluator_prompt,
            "agent_message": agent_message,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "formatted_user_content": user_content,
        },
    )
    # Same assembly as Agent.build_context / AgentHost.complete: evaluator task text plus
    # agents/system.md (tools/agents placeholders) and agents/system.json_object.md for json_object.
    eval_system = EVALUATOR_SYSTEM_PROMPT.strip()
    raw_ctx = ModelContext(
        system_prompt=eval_system,
        user_prompt="",
        messages=(
            {"role": "system", "content": eval_system},
            {"role": "user", "content": user_content},
        ),
        response_mode=DEFAULT_RESPONSE_MODE,
        tools=(),
        subagents=(),
        skills=(),
    )
    context = merge_runtime_system_into_messages(raw_ctx)
    driver = _sync_driver_for_evaluator(host)
    provider_name = host.config.default_provider
    model_names = host.config.default_model
    temperature = 0.2
    _emit_evaluator_log(
        log_callback,
        level="debug",
        kind="evaluator.llm_prompt_prepared",
        title="Evaluator LLM prompt prepared",
        summary="Prepared full prompt for evaluator model call.",
        payload={
            "provider_name": provider_name,
            "model_names": model_names,
            "temperature": temperature,
            "response_mode": context.response_mode,
            "messages": list(context.messages),
            "tools": list(context.tools),
            "subagents": list(context.subagents),
            "skills": list(context.skills),
        },
    )
    try:
        response = driver.decide(
            agent_id=None,
            provider_name=provider_name,
            model_names=model_names,
            temperature=temperature,
            context=context,
        )
    except ModelDriverError as exc:
        _emit_evaluator_log(
            log_callback,
            level="warning",
            kind="evaluator.failed",
            title="Evaluator failed",
            summary=str(exc),
            payload={
                "stage": "driver.decide",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            },
        )
        return failed_evaluator_result(str(exc))
    except Exception as exc:
        _emit_evaluator_log(
            log_callback,
            level="warning",
            kind="evaluator.failed",
            title="Evaluator failed",
            summary=str(exc),
            payload={
                "stage": "driver.decide",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            },
        )
        return failed_evaluator_result(str(exc))
    payload = response.payload
    if not isinstance(payload, dict):
        result = failed_evaluator_result(
            "Evaluator response is not a JSON object."
            + (f" Raw: {response.raw_text[:500]}" if getattr(response, "raw_text", None) else "")
        )
        _emit_evaluator_log(
            log_callback,
            level="warning",
            kind="evaluator.failed",
            title="Evaluator response was invalid",
            summary="Evaluator response is not a JSON object.",
            payload={
                "stage": "parse_response",
                "raw_text": getattr(response, "raw_text", ""),
                "parsed_payload": payload,
                "result": result,
            },
        )
        return result
    try:
        result = parse_eval_response(payload)
    except (TypeError, ValueError) as exc:
        result = failed_evaluator_result(str(exc))
        _emit_evaluator_log(
            log_callback,
            level="warning",
            kind="evaluator.failed",
            title="Evaluator response parsing failed",
            summary=str(exc),
            payload={
                "stage": "parse_eval_response",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                "raw_payload": payload,
                "result": result,
            },
        )
        return result
    _emit_evaluator_log(
        log_callback,
        level="debug",
        kind="evaluator.result",
        title="Evaluator result",
        summary="Evaluator scoring completed.",
        payload={
            "result": result,
            "raw_payload": payload,
            "raw_text": getattr(response, "raw_text", ""),
        },
    )
    return result
