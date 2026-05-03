# agent_framework_evaluator — web UI and CLI for running and evaluating agents

## Source tree

```
agent_framework_evaluator/
├── app.py                    FastAPI app — REST endpoints + WebSocket run handler
├── cli.py                    Entry point: web / run / evaluate subcommands
├── session_manager.py        SessionRecord — stores run state, last_run_result
├── evaluation.py             run_evaluation, select_agent_result_field,
│                             CASE_NO_CALLBACKS_POSTFIX
├── case_markdown.py          MarkdownCaseLoader — parses .md case files, resolves @refs
├── initializer_catalog.py    Discovers initializer .py files by convention
├── auto_user_reply.py        Automated callback responses for headless evaluation
├── runtime/
│   ├── session_runner.py     run_once — core single-run executor
│   ├── runner_host.py        AgentHost subclass for evaluator runs
│   ├── setup_loader.py       Loads initializer register()/setup() hooks
│   └── debug_subscriber.py   Trace subscriber for evaluator debug output
└── web/                      Static frontend (JS/HTML) — thin WebSocket observer only
```

## Rules

**Evaluator orchestration is server-side.** The JS frontend is a thin WebSocket observer. Do not re-introduce client-side result forwarding, batch loops, or `no_callbacks` postfix injection into `web/app.js`.
