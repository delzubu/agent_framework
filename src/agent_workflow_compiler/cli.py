"""Command-line interface for agent_workflow_compiler."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .log_reader import read_events, planning_run_ids
from .plan_extractor import extract_plan
from .emitter.behavior import emit_behavior
from .emitter.json_def import emit_json, emit_sidecar
from .emitter.markdown import emit_markdown


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_workflow_compiler",
        description="Compile a planning agent's audit log into a deterministic workflow agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # compile subcommand
    compile_p = sub.add_parser("compile", help="Compile a planning run into a workflow agent.")
    compile_p.add_argument("--log", required=True, help="Path to the JSONL audit log file.")
    compile_p.add_argument(
        "--planning-call",
        type=int,
        default=1,
        metavar="N",
        help="Which planning call to compile (1-indexed, default: 1).",
    )
    compile_p.add_argument(
        "--run-id",
        default=None,
        help="Exact run_id of the planning call to compile (overrides --planning-call).",
    )
    compile_p.add_argument(
        "--agent-id",
        default=None,
        help="ID for the generated agent (default: <source_agent_id>_compiled).",
    )
    compile_p.add_argument(
        "--output-dir",
        default=".",
        help="Directory where the generated files are written (default: current directory).",
    )
    compile_p.add_argument(
        "--source-agent-path",
        default=None,
        help="Path to the original agent's .md file for frontmatter copying.",
    )

    # list subcommand
    list_p = sub.add_parser("list", help="List planning calls found in a log file.")
    list_p.add_argument("--log", required=True, help="Path to the JSONL audit log file.")

    return parser


def _cmd_list(args: argparse.Namespace) -> int:
    events = read_events(args.log)
    run_ids = planning_run_ids(events)
    if not run_ids:
        print("No planning calls found in the log.")
        return 0
    print(f"Found {len(run_ids)} planning call(s):")
    for i, rid in enumerate(run_ids, 1):
        print(f"  {i}. {rid}")
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    events = read_events(args.log)
    run_ids = planning_run_ids(events)

    if not run_ids:
        print("Error: no planning calls found in the log.", file=sys.stderr)
        return 1

    if args.run_id:
        run_id = args.run_id
        if run_id not in run_ids:
            print(
                f"Error: run_id {run_id!r} not found in the log.\n"
                f"Available run_ids:\n" + "\n".join(f"  {r}" for r in run_ids),
                file=sys.stderr,
            )
            return 1
    else:
        idx = args.planning_call - 1
        if idx < 0 or idx >= len(run_ids):
            print(
                f"Error: --planning-call {args.planning_call} is out of range "
                f"(log has {len(run_ids)} planning call(s)).",
                file=sys.stderr,
            )
            return 1
        run_id = run_ids[idx]

    compilation = extract_plan(events, run_id)

    agent_id = args.agent_id or f"{compilation.source_agent_id}_compiled"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / f"{agent_id}.md"
    json_path = output_dir / f"{agent_id}.workflow.json"
    behavior_path = output_dir / f"{agent_id}.py"
    sidecar_path = output_dir / f"{agent_id}.json"

    emit_markdown(
        compilation,
        agent_id=agent_id,
        output_path=md_path,
        source_agent_path=args.source_agent_path,
    )
    emit_json(compilation, agent_id=agent_id, output_path=json_path)
    emit_behavior(compilation, agent_id=agent_id, output_path=behavior_path)
    emit_sidecar(
        agent_id=agent_id,
        output_path=sidecar_path,
        source_agent_path=args.source_agent_path,
    )

    print(f"Compiled {len(compilation.final_steps)} steps from run: {run_id}")
    print(f"  {md_path}")
    print(f"  {json_path}")
    print(f"  {behavior_path}")
    print(f"  {sidecar_path}")
    if compilation.replan_checkpoints:
        print(
            f"  {len(compilation.replan_checkpoints)} replan checkpoint(s) — "
            f"edit _on_step_end() in the behavior file to activate re-routing."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "compile":
        return _cmd_compile(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
