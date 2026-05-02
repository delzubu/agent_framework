"""Emit a Markdown agent definition file for the compiled workflow agent."""
from __future__ import annotations

from pathlib import Path

from ..models import CompiledStep, PlanCompilation


def emit_markdown(
    compilation: PlanCompilation,
    agent_id: str,
    output_path: str | Path,
    *,
    source_agent_path: str | Path | None = None,
    behavior_module: str | None = None,
) -> None:
    """Write a ``<agent>.md`` agent definition file.

    Copies frontmatter from *source_agent_path* if provided; otherwise generates
    minimal frontmatter. The system prompt section describes the workflow so the
    file remains human-readable as a specification.

    Args:
        compilation: Compiled planning data.
        agent_id: New agent identifier.
        output_path: Where to write the ``.md`` file.
        source_agent_path: Optional path to the original agent's ``.md`` file.
            Its frontmatter (id, role, parameters, subagents, allowed_tools, etc.)
            is copied and adapted for the new compiled agent.
        behavior_module: Python module path for the behavior class. Defaults to
            ``<agent_id>_behavior``.
    """
    behavior_mod = behavior_module or f"{agent_id}_behavior"
    frontmatter = _build_frontmatter(
        compilation=compilation,
        agent_id=agent_id,
        behavior_module=behavior_mod,
        source_agent_path=source_agent_path,
    )
    workflow_description = _build_workflow_description(compilation)
    content = f"---\n{frontmatter}---\n{workflow_description}\n---\n{{{{instruction}}}}\n"
    Path(output_path).write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_frontmatter(
    *,
    compilation: PlanCompilation,
    agent_id: str,
    behavior_module: str,
    source_agent_path: str | Path | None,
) -> str:
    """Build the YAML frontmatter block (without leading/trailing ``---``)."""
    if source_agent_path is not None:
        return _adapt_source_frontmatter(
            Path(source_agent_path),
            agent_id=agent_id,
            behavior_module=behavior_module,
        )
    # Minimal generated frontmatter
    lines = [
        f"id: {agent_id}",
        f"role: {compilation.source_agent_id}",
        f"behavior: {behavior_module}",
        "parameters:",
        "  instruction:",
        "    description: Agent instruction.",
        "    required: false",
    ]
    return "\n".join(lines) + "\n"


def _adapt_source_frontmatter(
    source_path: Path,
    *,
    agent_id: str,
    behavior_module: str,
) -> str:
    """Read the source agent's frontmatter and adapt it for the compiled agent."""
    try:
        text = source_path.read_text(encoding="utf-8")
    except OSError:
        return f"id: {agent_id}\nbehavior: {behavior_module}\n"

    # Extract the YAML block between the first pair of --- markers
    parts = text.split("---")
    if len(parts) < 3:
        return f"id: {agent_id}\nbehavior: {behavior_module}\n"

    yaml_block = parts[1].strip()
    lines = yaml_block.splitlines()

    # Replace or add id and behavior fields; strip planning: block (lives in .json sidecar)
    new_lines: list[str] = []
    found_id = False
    found_behavior = False
    in_planning_block = False
    for line in lines:
        stripped = line.lstrip()
        # Detect start of planning: block
        if stripped.startswith("planning:"):
            in_planning_block = True
            continue
        # Detect end of planning: block (next top-level key)
        if in_planning_block:
            if line and not line[0].isspace():
                in_planning_block = False
            else:
                continue
        if stripped.startswith("id:"):
            new_lines.append(f"id: {agent_id}")
            found_id = True
        elif stripped.startswith("behavior:"):
            new_lines.append(f"behavior: {behavior_module}")
            found_behavior = True
        else:
            new_lines.append(line)

    if not found_id:
        new_lines.insert(0, f"id: {agent_id}")
    if not found_behavior:
        new_lines.append(f"behavior: {behavior_module}")

    return "\n".join(new_lines) + "\n"


def _build_workflow_description(compilation: PlanCompilation) -> str:
    """Build the system prompt section describing the workflow."""
    lines = [
        "This agent runs as a deterministic compiled workflow.",
        f"Compiled from planning run: {compilation.source_run_id}",
        f"Original agent: {compilation.source_agent_id}",
        "",
        "## Workflow steps",
        "",
    ]
    for i, step in enumerate(compilation.final_steps, 1):
        target = step.tool_name or step.subagent_id or step.skill_name or "?"
        lines.append(f"{i}. **{step.step_id}** ({step.kind} → {target})")
        if step.depends_on:
            lines.append(f"   depends on: {', '.join(step.depends_on)}")

    if compilation.replan_checkpoints:
        lines += [
            "",
            "## Replan checkpoints",
            "",
            "The source run replanned at the following points.",
            "Edit `_on_step_end()` in the behavior file to activate dynamic re-routing.",
            "",
        ]
        for cp in compilation.replan_checkpoints:
            lines.append(
                f"- After **{cp.after_step_id}** (revision {cp.plan_revision}): "
                f"added {cp.added_step_ids}"
            )
    return "\n".join(lines)
