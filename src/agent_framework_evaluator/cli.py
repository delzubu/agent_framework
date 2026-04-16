from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from agent_framework_evaluator.runtime.session_runner import SessionRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent evaluator and debugger.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    web = subparsers.add_parser("web")
    web.add_argument("--env", default=".env", help="Path to .env (default for the web UI; overridable in the UI).")
    web.add_argument(
        "--agent",
        default=None,
        help="Default agent id for the web UI (datalist still overridable).",
    )
    web.add_argument(
        "--initializer",
        default=None,
        metavar="NAME",
        help="Default initializer .py (under AGENT_EVAL_INITIALIZER_DIR from .env); overridable in the UI.",
    )
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8123)
    web.add_argument("--open-browser", action="store_true")

    run = subparsers.add_parser("run")
    run.add_argument("--env", default=".env")
    run.add_argument("--agent", default=None)
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

    env_abs = str(Path(args.env).resolve())
    os.environ["AGENT_EVAL_DEFAULT_ENV_PATH"] = env_abs
    for key in ("AGENT_EVAL_DEFAULT_AGENT", "AGENT_EVAL_DEFAULT_INITIALIZER"):
        os.environ.pop(key, None)
    if args.agent:
        os.environ["AGENT_EVAL_DEFAULT_AGENT"] = args.agent
    if args.initializer:
        os.environ["AGENT_EVAL_DEFAULT_INITIALIZER"] = args.initializer

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
    setup_module = None
    if args.setup:
        from agent_framework_evaluator.runtime.setup_loader import load_setup_module

        setup_module = load_setup_module(Path(args.setup))

    agent_id: str | None = args.agent
    if agent_id is None and setup_module is not None:
        agent_id = getattr(setup_module, "DEFAULT_AGENT", None)
    if agent_id is None:
        print("error: provide --agent or set DEFAULT_AGENT in --setup script", file=sys.stderr)
        return 2

    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    elif args.prompt:
        prompt = args.prompt
    elif setup_module is not None and hasattr(setup_module, "get_prompt_template"):
        prompt = setup_module.get_prompt_template()
    else:
        print("error: provide --prompt, --prompt-file, or get_prompt_template() in --setup script", file=sys.stderr)
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
            agent_id=agent_id,
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
