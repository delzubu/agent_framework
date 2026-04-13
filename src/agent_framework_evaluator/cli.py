from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from agent_framework_evaluator.runtime.session_runner import SessionRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent evaluator and debugger.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    web = subparsers.add_parser("web")
    web.add_argument("--env", default=".env")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8123)
    web.add_argument("--open-browser", action="store_true")

    run = subparsers.add_parser("run")
    run.add_argument("--env", default=".env")
    run.add_argument("--agent", required=True)
    run.add_argument("--setup")
    run.add_argument("--prompt")
    run.add_argument("--prompt-file")
    run.add_argument("--output")
    run.add_argument(
        "--trace-jsonl",
        metavar="PATH",
        default=None,
        help="Append unified trace events to a JSONL file.",
    )
    run.add_argument(
        "--trace-llm-dir",
        metavar="DIR",
        default=None,
        help="Write llm-channel events to per-agent logs under DIR.",
    )

    return parser


def _cmd_web(args: argparse.Namespace) -> int:
    import uvicorn

    url = f"http://{args.host}:{args.port}/"
    if args.open_browser:
        webbrowser.open(url)
    uvicorn.run(
        "agent_framework_evaluator.app:app",
        host=args.host,
        port=args.port,
        factory=False,
    )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    elif args.prompt:
        prompt = args.prompt
    else:
        print("error: provide --prompt or --prompt-file", file=sys.stderr)
        return 2

    from agent_framework.tracing import CompositeRuntimeTracer
    from agent_framework.tracing_subscribers.jsonl_subscriber import JsonlTraceSubscriber
    from agent_framework.tracing_subscribers.llm_trace_file_subscriber import LlmTraceFileSubscriber

    subs: list[object] = []
    if args.trace_jsonl:
        subs.append(JsonlTraceSubscriber(Path(args.trace_jsonl)))
    if args.trace_llm_dir:
        subs.append(LlmTraceFileSubscriber(Path(args.trace_llm_dir)))
    merged_tracer = CompositeRuntimeTracer(subscribers=subs) if subs else None

    runner = SessionRunner(args.env)
    try:
        result = runner.run_once(
            agent_id=args.agent,
            prompt=prompt,
            setup_path=Path(args.setup) if args.setup else None,
            runtime_tracer=merged_tracer,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {"status": result["status"], "message": result["message"]}
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "web":
        return _cmd_web(args)
    if args.command == "run":
        return _cmd_run(args)
    return 0
