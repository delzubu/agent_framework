from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from agent_framework_evaluator.evaluation import CASE_NO_CALLBACKS_POSTFIX
from agent_framework_evaluator.runtime.session_runner import SessionRunner

DEFAULT_ENV_ARGUMENT = ".env"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent evaluator and debugger.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    web = subparsers.add_parser("web")
    web.add_argument(
        "--env",
        default=DEFAULT_ENV_ARGUMENT,
        help="Path to .env (default: ./.env; overridable in the UI).",
    )
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
    run.add_argument("--env", default=DEFAULT_ENV_ARGUMENT)
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

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Run and evaluate test cases without the web UI.",
    )
    evaluate.add_argument("--env", default=DEFAULT_ENV_ARGUMENT, help="Path to .env file (default: ./.env).")
    src = evaluate.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--initializer",
        metavar="PATH",
        help="Initializer .py; runs all cases unless --case is set.",
    )
    src.add_argument(
        "--case-file",
        metavar="PATH",
        help="Standalone case .md file (no initializer required).",
    )
    evaluate.add_argument(
        "--case",
        metavar="N",
        type=int,
        default=None,
        help="Select a single case by 0-based index (requires --initializer).",
    )
    evaluate.add_argument(
        "--agent",
        default=None,
        help="Agent id to run (default: initializer DEFAULT_AGENT or 'root').",
    )
    evaluate.add_argument("--output", metavar="FILE", help="Write full JSON result to file.")
    evaluate.add_argument(
        "--verbose",
        action="store_true",
        help="Batch only: include per-case run result in addition to summary table.",
    )

    return parser


def _ensure_default_env_argument(args: argparse.Namespace) -> None:
    """Treat omitted/empty ``--env`` as if the caller had passed ``--env .env``."""
    if hasattr(args, "env") and not getattr(args, "env", None):
        args.env = DEFAULT_ENV_ARGUMENT


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


def _cmd_evaluate(args: argparse.Namespace) -> int:
    from agent_framework_evaluator.case_markdown import parse_case_markdown_file
    from agent_framework_evaluator.evaluation import (
        run_code_evaluations,
        run_evaluation,
        select_agent_result_field,
    )
    from agent_framework_evaluator.initializer_catalog import (
        load_initializer_default_agent,
        load_initializer_default_eval_model,
        load_raw_test_cases,
        resolve_env_path,
        resolve_setup_path_for_run,
    )

    env_file = resolve_env_path(args.env)

    def _run_single_case(
        *,
        agent_id: str,
        prompt: str,
        criteria: str,
        result_field: str,
        code_evaluators: list,
        flags: set,
        setup_path: "Path | None",
        eval_model: "str | tuple | None",
    ) -> dict[str, object]:
        runner = SessionRunner(args.env)
        run_result = runner.run_once(
            agent_id=agent_id,
            prompt=prompt.rstrip() + CASE_NO_CALLBACKS_POSTFIX,
            setup_path=setup_path,
        )
        selected = select_agent_result_field(run_result, result_field)
        if selected is None:
            print(
                f"error: result_field '{result_field}' not present in agent result",
                file=sys.stderr,
            )
            sys.exit(1)
        llm = run_evaluation(
            env_path=env_file,
            evaluator_prompt=criteria,
            agent_message=selected,
            model_override=eval_model if eval_model else None,
        )
        llm["score"] = min(10.0, max(0.0, float(llm["score"])))
        code_results = run_code_evaluations(code_evaluators, prompt=prompt, agent_message=selected, flags=flags)
        parts = [float(llm["score"])] + [float(r["score"]) for r in code_results if r is not None]
        average = sum(parts) / len(parts)
        return {
            "run_result": run_result,
            "llm_result": llm,
            "code_results": code_results,
            "average_score": average,
            "selected_payload": selected,
            "result_field": result_field,
        }

    if args.case_file:
        # --case-file: standalone .md, no initializer
        case_path = Path(args.case_file)
        if not case_path.exists():
            print(f"error: case file not found: {case_path}", file=sys.stderr)
            return 1
        case = parse_case_markdown_file(path=case_path, evaluator_registry={})
        if case is None:
            print(f"error: could not parse case file: {case_path}", file=sys.stderr)
            return 1
        fm_agent = case.get("fm_agent")
        fm_initializer = case.get("fm_initializer")
        # Conflict: both CLI and frontmatter specify an agent but they disagree.
        if args.agent and fm_agent and args.agent != fm_agent:
            print(
                f"info: skipping {case_path.name} — frontmatter agent={fm_agent!r} "
                f"does not match --agent {args.agent!r}",
                file=sys.stderr,
            )
            return 0
        # Conflict: both CLI initializer (via --agent default path) and frontmatter disagree.
        # (--case-file and --initializer are mutually exclusive, so no CLI initializer here;
        #  this guard is for future-proofing and env-based overrides.)
        agent_id = args.agent or fm_agent or "root"
        setup_path = resolve_setup_path_for_run(env_file, fm_initializer) if fm_initializer else None
        result = _run_single_case(
            agent_id=agent_id,
            prompt=case["prompt"],
            criteria=str(case.get("evaluation_criteria", "") or ""),
            result_field=str(case.get("result_field", "message") or "message"),
            code_evaluators=case.get("code_evaluators", []),
            flags=case.get("flags", set()),
            setup_path=setup_path,
            eval_model=None,
        )
        text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0

    # --initializer path
    initializer = args.initializer
    cases = load_raw_test_cases(env_file, initializer)
    if not cases:
        print(f"error: no cases found for initializer: {initializer}", file=sys.stderr)
        return 1
    setup_path = resolve_setup_path_for_run(env_file, initializer)
    default_agent = args.agent or load_initializer_default_agent(env_file, initializer) or "root"
    eval_model = load_initializer_default_eval_model(env_file, initializer)

    if args.case is not None:
        # Single case by index
        if args.case >= len(cases):
            print(
                f"error: --case {args.case} out of range (0..{len(cases)-1})", file=sys.stderr
            )
            return 1
        case = cases[args.case]
        result = _run_single_case(
            agent_id=default_agent,
            prompt=str(case.get("prompt", "")),
            criteria=str(case.get("evaluation_criteria", "") or ""),
            result_field=str(case.get("result_field", "message") or "message"),
            code_evaluators=case.get("code_evaluators", []),
            flags=case.get("flags", set()),
            setup_path=setup_path,
            eval_model=eval_model,
        )
        text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0

    # Full batch
    batch_results: list[dict[str, object]] = []
    for i, case in enumerate(cases):
        title = str(case.get("title", f"Case {i}"))
        print(f"[{i+1}/{len(cases)}] {title} …", flush=True)
        try:
            result = _run_single_case(
                agent_id=default_agent,
                prompt=str(case.get("prompt", "")),
                criteria=str(case.get("evaluation_criteria", "") or ""),
                result_field=str(case.get("result_field", "message") or "message"),
                code_evaluators=case.get("code_evaluators", []),
                flags=case.get("flags", set()),
                setup_path=setup_path,
                eval_model=eval_model,
            )
            avg = result["average_score"]
            verdict = "PASS" if float(avg) >= 7.0 else "FAIL"
            print(f"  score={float(avg):.1f}  {verdict}")
            if args.verbose:
                print(f"  run_result={json.dumps(result['run_result'], ensure_ascii=False, default=str)}")
            batch_results.append({"case_index": i, "title": title, **result})
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            batch_results.append({"case_index": i, "title": title, "error": str(exc)})

    if args.output:
        text = json.dumps(batch_results, indent=2, ensure_ascii=False, default=str)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"\nFull results written to {args.output}")

    all_errored = batch_results and all("error" in r for r in batch_results)
    return 1 if all_errored else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _ensure_default_env_argument(args)
    if args.command == "web":
        return _cmd_web(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "evaluate":
        return _cmd_evaluate(args)
    return 0
