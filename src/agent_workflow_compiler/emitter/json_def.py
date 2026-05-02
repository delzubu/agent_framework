"""Emit a human-readable JSON workflow definition file and the runtime sidecar."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import CompiledStep, PlanCompilation

_RUNTIME_FIELDS = ("provider", "model", "temperature", "can_query_caller", "can_use_host_interaction")


def emit_sidecar(
    agent_id: str,
    output_path: str | Path,
    *,
    source_agent_path: str | Path | None = None,
    behavior_module: str | None = None,
) -> None:
    """Write the ``<agent>.json`` runtime sidecar consumed by ``load_runtime_metadata``.

    Copies runtime fields (provider, model, temperature, …) from the source
    agent's ``.json`` sidecar if present. The ``behavior`` field is always set
    to the generated behavior module. ``planning`` is intentionally excluded —
    compiled workflow agents are deterministic and don't use planning.
    """
    behavior_mod = behavior_module or agent_id
    sidecar: dict = {"behavior": behavior_mod}

    if source_agent_path is not None:
        source_json = Path(source_agent_path).with_suffix(".json")
        if source_json.exists():
            try:
                source_data = json.loads(source_json.read_text(encoding="utf-8"))
                for field in _RUNTIME_FIELDS:
                    if field in source_data:
                        sidecar[field] = source_data[field]
            except (OSError, json.JSONDecodeError):
                pass

    Path(output_path).write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def emit_json(compilation: PlanCompilation, agent_id: str, output_path: str | Path) -> None:
    """Write a ``<agent>.workflow.json`` file describing the compiled workflow.

    This file is for human inspection and future tooling. The runtime does not
    consume it directly — the behavior file constructs the workflow in Python.

    Args:
        compilation: Compiled planning data.
        agent_id: New agent identifier.
        output_path: Where to write the JSON file.
    """
    workflow: dict = {
        "agent_id": agent_id,
        "source_run_id": compilation.source_run_id,
        "source_agent_id": compilation.source_agent_id,
        "entry_step": compilation.final_steps[0].step_id if compilation.final_steps else None,
        "steps": [_step_to_dict(s) for s in compilation.final_steps],
        "replan_checkpoints": [
            {
                "after_step_id": cp.after_step_id,
                "plan_revision": cp.plan_revision,
                "added_step_ids": cp.added_step_ids,
                "trigger_message": cp.trigger_message,
            }
            for cp in compilation.replan_checkpoints
        ],
        "invocation_parameters": compilation.invocation_parameters,
    }
    Path(output_path).write_text(
        json.dumps(workflow, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _step_to_dict(step: CompiledStep) -> dict:
    return {
        "step_id": step.step_id,
        "kind": step.kind,
        "tool_name": step.tool_name,
        "subagent_id": step.subagent_id,
        "skill_name": step.skill_name,
        "parameters": step.parameters,
        "depends_on": step.depends_on,
        "next_step": step.next_step,
    }
