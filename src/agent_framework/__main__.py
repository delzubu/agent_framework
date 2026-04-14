"""CLI entrypoint for running the configured root agent."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from agent_framework.agent_registry import normalize_agent_id
from agent_framework.evaluator import AgentPromptEvaluator, OpenAiConversationEvaluator, OpenAiResultJudge
from agent_framework.host import AgentHost


def _resolve_instruction_argument(raw_instruction: str) -> str:
    """Resolve a CLI instruction string or `@file` indirection."""
    if raw_instruction.startswith("@"):
        return Path(raw_instruction[1:]).read_text(encoding="utf-8")
    return raw_instruction


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser for the runtime module."""
    parser = argparse.ArgumentParser(description="Run the agent-adventure runtime.")
    parser.add_argument(
        "--console",
        action="store_true",
        help="Run the configured root agent using console input and output.",
    )
    parser.add_argument(
        "--env",
        default=".env",
        help="Path to the .env file used to configure the runtime.",
    )
    parser.add_argument(
        "--instruction",
        help="Initial instruction for the root agent. Prefix with @ to load the instruction from a file.",
    )
    parser.add_argument(
        "--evaluate",
        help="Path to an XML evaluation file containing <prompt>, <evaluator>, and <schema> segments.",
    )
    parser.add_argument(
        "--evaluate-openai",
        help="Path to a JSON evaluation file containing raw input_json scenes for OpenAI-backed evaluation.",
    )
    parser.add_argument(
        "--agent",
        help="Logical agent id to run or evaluate instead of the configured root agent.",
    )
    parser.add_argument(
        "--llm-trace",
        choices=("console", "file", "both"),
        help="Enable global LLM request/response tracing at the host level.",
    )
    parser.add_argument(
        "--llm-trace-dir",
        default="logs",
        help="Directory used for host-level LLM trace files.",
    )
    parser.add_argument(
        "--runtime-trace-jsonl",
        metavar="PATH",
        default=None,
        help="Append unified trace events (runtime, mirrored console trace, user, llm, …) as JSONL to PATH.",
    )
    parser.add_argument(
        "--runtime-trace-python-logs",
        action="store_true",
        help="Mirror Python logging into the unified tracer (requires --runtime-trace-jsonl).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Override the default model(s) from .env. "
            "Accepts a comma-separated list for fallback priority "
            "(e.g. 'gpt-4o,gpt-4o-mini')."
        ),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    host_factory=AgentHost.from_env_console,
) -> int:
    """Run the CLI.

    Args:
        argv: Optional argument vector for tests or embedding.
        host_factory: Injectable host factory used to create the runtime host.

    Returns:
        Process-style exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.console and not args.evaluate and not args.evaluate_openai:
        parser.print_help()
        return 2

    model_override = args.model or None
    if model_override is not None:
        host = AgentHost.from_env_console(args.env, model_override=model_override)
    else:
        host = host_factory(args.env)
    if args.runtime_trace_jsonl:
        from pathlib import Path

        from agent_framework.tracing import CompositeRuntimeTracer
        from agent_framework.tracing_subscribers.jsonl_subscriber import JsonlTraceSubscriber

        host.runtime_tracer = CompositeRuntimeTracer(
            subscribers=[JsonlTraceSubscriber(Path(args.runtime_trace_jsonl))]
        )
        if args.runtime_trace_python_logs:
            import logging

            from agent_framework.tracing_consumers.log_handler import LoggingTraceHandler

            logging.getLogger().addHandler(LoggingTraceHandler(host.runtime_tracer))
    elif args.runtime_trace_python_logs:
        parser.error("--runtime-trace-python-logs requires --runtime-trace-jsonl")
    if args.llm_trace:
        host.enable_llm_trace_logging(target=args.llm_trace, output_dir=args.llm_trace_dir)
    if args.evaluate is not None:
        judge = OpenAiResultJudge(
            api_key=host.config.openai_api_key,
            model_name=host.config.default_model[0],
        )
        eval_agent = normalize_agent_id(args.agent) if args.agent else args.agent
        summary = AgentPromptEvaluator(host=host, judge=judge, agent_id=eval_agent).evaluate_file(
            args.evaluate,
            agent_id=eval_agent,
        )
        print(summary.to_markdown_table())
        return 0
    if args.evaluate_openai is not None:
        if not args.agent:
            raise ValueError("--evaluate-openai requires --agent.")
        judge = OpenAiResultJudge(
            api_key=host.config.openai_api_key,
            model_name=host.config.default_model[0],
        )
        summary = OpenAiConversationEvaluator(
            host=host, judge=judge, agent_id=normalize_agent_id(args.agent)
        ).evaluate_file(
            args.evaluate_openai
        )
        print(summary.to_markdown_table())
        return 0

    if args.instruction is not None:
        instruction = _resolve_instruction_argument(args.instruction)
        result = (
            host.run_agent(normalize_agent_id(args.agent), initial_instruction=instruction)
            if args.agent
            else host.run_root(initial_instruction=instruction)
        )
        if result.message:
            print(result.message)
        return 0

    host.run_console()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
