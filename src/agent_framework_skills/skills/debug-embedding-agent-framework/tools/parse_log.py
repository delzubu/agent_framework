"""parse_log.py — utilities for inspecting agent_framework JSONL audit logs.

Usage (CLI):
    python parse_log.py summarize <log.jsonl>
    python parse_log.py params    <log.jsonl> <run_id>
    python parse_log.py plan      <log.jsonl> <run_id>
    python parse_log.py decisions <log.jsonl> <run_id>
    python parse_log.py subagents <log.jsonl> <run_id>
    python parse_log.py llm       <log.jsonl> <run_id>

Usage (import):
    from tools.parse_log import load_events, get_bound_parameters, get_plan_updates, ...
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def load_events(path: str | Path) -> list[dict[str, Any]]:
    """Load all events from a JSONL audit log."""
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def filter_run(events: list[dict], run_id: str, *, include_children: bool = True) -> list[dict]:
    """Events for a specific run. include_children=True also includes subagent runs."""
    if include_children:
        return [e for e in events if (e.get("context") or {}).get("run_id", "").startswith(run_id)]
    return [e for e in events if (e.get("context") or {}).get("run_id") == run_id]


def filter_kind(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("kind") == kind]


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------

def summarize(events: list[dict]) -> list[dict]:
    """Return a list of {run_id, agent, caller, status} for every distinct run."""
    runs: dict[str, dict] = {}
    for e in events:
        ctx = e.get("context") or {}
        run_id = ctx.get("run_id")
        if not run_id:
            continue
        if run_id not in runs:
            runs[run_id] = {
                "run_id": run_id,
                "agent": ctx.get("agent_id") or "",
                "caller": ctx.get("caller_id") or "",
                "status": None,
            }
        if e.get("kind") == "runtime.agent_finished":
            runs[run_id]["status"] = e["payload"].get("status")
        if e.get("kind") == "runtime.audit.agent_call_started":
            runs[run_id]["agent"] = e["payload"].get("agent_name") or runs[run_id]["agent"]
    return list(runs.values())


# ---------------------------------------------------------------------------
# Parameter binding
# ---------------------------------------------------------------------------

def get_bound_parameters(events: list[dict], run_id: str) -> dict[str, Any] | None:
    """Return the fully bound parameters for a run (from runtime.parameters_bound).
    Falls back to runtime.audit.agent_call_started seed parameters if absent."""
    for e in filter_run(events, run_id, include_children=False):
        if e.get("kind") == "runtime.parameters_bound":
            return e["payload"].get("bound_parameters")
    # Fallback: seed params from agent_call_started
    for e in filter_run(events, run_id, include_children=False):
        if e.get("kind") == "runtime.audit.agent_call_started":
            return e["payload"].get("parameters")
    return None


# ---------------------------------------------------------------------------
# Planning agent
# ---------------------------------------------------------------------------

def get_plan_updates(events: list[dict], run_id: str) -> list[dict]:
    """Return all plan_updated named events for a run, in order."""
    result = []
    for e in filter_run(events, run_id, include_children=False):
        if e.get("kind") == "runtime.audit.named_event":
            ev = e["payload"].get("event") or {}
            if ev.get("type") == "plan_updated":
                result.append(ev)
    return result


def get_final_plan(events: list[dict], run_id: str) -> list[dict]:
    """Return the final plan steps (last plan_updated event)."""
    updates = get_plan_updates(events, run_id)
    return updates[-1].get("plan", []) if updates else []


def get_replans(events: list[dict], run_id: str) -> list[dict]:
    """Return only replan events (is_initial=False)."""
    return [u for u in get_plan_updates(events, run_id) if not u.get("is_initial", True)]


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

def get_decisions(events: list[dict], run_id: str) -> list[dict]:
    """Return all decision payloads made by the agent (not its subagents)."""
    result = []
    for e in filter_run(events, run_id, include_children=False):
        if e.get("kind") == "runtime.audit.decision":
            result.append(e["payload"].get("decision") or e["payload"])
    return result


# ---------------------------------------------------------------------------
# Subagent results
# ---------------------------------------------------------------------------

def get_subagent_results(events: list[dict], run_id: str) -> dict[str, dict]:
    """Return {subagent_id: agent_finished_payload} for all direct child runs."""
    results = {}
    prefix = run_id + "."
    for e in events:
        if e.get("kind") != "runtime.agent_finished":
            continue
        child_run_id = (e.get("context") or {}).get("run_id", "")
        if not child_run_id.startswith(prefix):
            continue
        # Only direct children (one extra segment after prefix)
        remainder = child_run_id[len(prefix):]
        if "." in remainder:
            continue
        results[remainder] = e.get("payload") or {}
    return results


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

def get_llm_calls(events: list[dict], run_id: str) -> list[dict]:
    """Return paired {request, response} dicts for each LLM call in this run.
    Unpaired requests/responses are included with the missing side as None."""
    requests: dict[str, dict] = {}
    responses: dict[str, dict] = {}
    for e in filter_run(events, run_id, include_children=False):
        kind = e.get("kind")
        span = e.get("span_id") or ""
        if kind == "llm.request":
            requests[span] = e["payload"]
        elif kind in ("llm.response", "llm.error"):
            responses[span] = e["payload"]
    spans = list(dict.fromkeys(list(requests) + list(responses)))
    return [{"request": requests.get(s), "response": responses.get(s)} for s in spans]


def get_prompts(events: list[dict], run_id: str) -> dict[str, str]:
    """Return {system_prompt, user_prompt} from agent_call_started."""
    for e in filter_run(events, run_id, include_children=False):
        if e.get("kind") == "runtime.audit.agent_call_started":
            p = e.get("payload") or {}
            return {"system_prompt": p.get("system_prompt", ""), "user_prompt": p.get("user_prompt", "")}
    return {}


# ---------------------------------------------------------------------------
# Agent type detection
# ---------------------------------------------------------------------------

def detect_agent_type(events: list[dict], run_id: str) -> str:
    """Guess agent type from log evidence.
    Returns 'planning', 'workflow', or 'standard'."""
    run_events = filter_run(events, run_id, include_children=False)
    has_llm = any(e.get("kind") in ("llm.request", "llm.response") for e in run_events)
    has_plan = any(
        e.get("kind") == "runtime.audit.named_event"
        and (e["payload"].get("event") or {}).get("type") == "plan_updated"
        for e in run_events
    )
    if has_plan:
        return "planning"
    if not has_llm:
        return "workflow"
    return "standard"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_summarize(log: str) -> None:
    events = load_events(log)
    runs = summarize(events)
    # Show root runs first, then children
    for r in sorted(runs, key=lambda x: x["run_id"]):
        depth = r["run_id"].count(".")
        indent = "  " * max(0, depth - 1)
        atype = detect_agent_type(events, r["run_id"])
        status = r["status"] or "?"
        print(f"{indent}{r['run_id']}  [{atype}]  status={status}  caller={r['caller']}")


def _cmd_params(log: str, run_id: str) -> None:
    events = load_events(log)
    params = get_bound_parameters(events, run_id)
    if params is None:
        print(f"No parameter data found for run_id={run_id!r}", file=sys.stderr)
        return
    print(json.dumps(params, indent=2, ensure_ascii=False, default=str))


def _cmd_plan(log: str, run_id: str) -> None:
    events = load_events(log)
    updates = get_plan_updates(events, run_id)
    if not updates:
        print(f"No plan_updated events found for run_id={run_id!r}", file=sys.stderr)
        return
    for u in updates:
        tag = "INITIAL" if u.get("is_initial") else f"REPLAN rev={u.get('plan_revision')}"
        added = u.get("added_step_ids") or []
        print(f"\n--- {tag}  added={added} ---")
        for step in u.get("plan", []):
            deps = step.get("depends_on") or []
            print(f"  {step['id']}  ({step['kind']})  deps={deps}")
            for k, v in (step.get("parameters") or {}).items():
                print(f"    {k}: {v!r}")


def _cmd_decisions(log: str, run_id: str) -> None:
    events = load_events(log)
    decisions = get_decisions(events, run_id)
    for i, d in enumerate(decisions):
        kind = d.get("kind")
        detail = d.get("tool_name") or d.get("subagent_id") or d.get("skill_name") or ""
        msg = (d.get("message") or "")[:80]
        print(f"[{i}] {kind}  {detail}  {msg!r}")


def _cmd_subagents(log: str, run_id: str) -> None:
    events = load_events(log)
    results = get_subagent_results(events, run_id)
    if not results:
        print(f"No subagent results found for run_id={run_id!r}")
        return
    for subagent_id, payload in results.items():
        status = payload.get("status")
        msg = (payload.get("message") or "")[:120]
        print(f"\n{subagent_id}  status={status}")
        print(f"  message: {msg!r}")
        if payload.get("response"):
            print(f"  response: {json.dumps(payload['response'], ensure_ascii=False, default=str)[:200]}")


def _cmd_llm(log: str, run_id: str) -> None:
    events = load_events(log)
    calls = get_llm_calls(events, run_id)
    if not calls:
        print(f"No LLM calls found for run_id={run_id!r} (workflow agent?)")
        return
    for i, call in enumerate(calls):
        req = call.get("request") or {}
        resp = call.get("response") or {}
        model = req.get("model_name") or resp.get("model_name") or "?"
        usage = resp.get("usage") or {}
        print(f"\n[{i}] model={model}  tokens={usage}")
        if resp.get("raw_text"):
            print(f"  raw_output: {resp['raw_text'][:300]!r}")


_COMMANDS = {
    "summarize": (_cmd_summarize, 1, "summarize <log>"),
    "params":    (_cmd_params,    2, "params    <log> <run_id>"),
    "plan":      (_cmd_plan,      2, "plan      <log> <run_id>"),
    "decisions": (_cmd_decisions, 2, "decisions <log> <run_id>"),
    "subagents": (_cmd_subagents, 2, "subagents <log> <run_id>"),
    "llm":       (_cmd_llm,       2, "llm       <log> <run_id>"),
}

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in _COMMANDS:
        print("Usage:")
        for _, (_, _, usage) in _COMMANDS.items():
            print(f"  python parse_log.py {usage}")
        sys.exit(1)
    cmd, (fn, nargs, _) = args[0], _COMMANDS[args[0]]
    if len(args) - 1 < nargs:
        print(f"Usage: python parse_log.py {_COMMANDS[cmd][2]}", file=sys.stderr)
        sys.exit(1)
    fn(*args[1:nargs + 1])
