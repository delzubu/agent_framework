"""Emit a human-readable JSON workflow definition file."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import CompiledStep, PlanCompilation


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
