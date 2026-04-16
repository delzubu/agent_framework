from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_framework_evaluator.app import create_app
from agent_framework_evaluator.cli import build_parser, main
from agent_framework_evaluator.runtime.session_runner import SessionRunner


def test_evaluator_cli_has_web_and_run_subcommands() -> None:
    parser = build_parser()
    args = parser.parse_args(["web", "--env", ".env"])
    assert args.command == "web"
    args = parser.parse_args(
        [
            "web",
            "--env",
            "custom/.env",
            "--agent",
            "myagent",
            "--initializer",
            "seed.py",
        ],
    )
    assert args.command == "web"
    assert args.env == "custom/.env"
    assert args.agent == "myagent"
    assert args.initializer == "seed.py"
    args = parser.parse_args(["run", "--agent", "root", "--prompt", "hi"])
    assert args.command == "run"


def test_create_app_exists() -> None:
    app = create_app()
    assert app is not None


def test_api_agents_endpoint_lists_discovered_agents() -> None:
    client = TestClient(create_app())
    response = client.get("/api/agents")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    assert isinstance(data["agents"], list)


def test_api_agent_system_prompt_404_unknown_agent() -> None:
    client = TestClient(create_app())
    r = client.get(
        "/api/agent-system-prompt",
        params={"env_path": ".env", "agent_id": "__no_such_agent_id__"},
    )
    assert r.status_code == 404


def test_api_last_prompts_unknown_session() -> None:
    client = TestClient(create_app())
    r = client.get("/api/sessions/00000000-0000-0000-0000-000000000000/last-prompts")
    assert r.status_code == 404


def test_api_last_prompts_empty_when_no_run(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    env_f = tmp_path / ".env"
    env_f.write_text("", encoding="utf-8")
    client = TestClient(create_app())
    sid = client.post("/api/sessions", json={"env_path": str(env_f)}).json()["session_id"]
    r = client.get(f"/api/sessions/{sid}/last-prompts")
    assert r.status_code == 200
    data = r.json()
    assert data.get("system_prompt") == ""
    assert data.get("user_prompt") == ""
    assert data.get("instruction_entered") == ""
    assert data.get("user_messages") == []


def test_user_input_http_unknown_session() -> None:
    client = TestClient(create_app())
    r = client.post(
        "/api/sessions/00000000-0000-0000-0000-000000000000/user-input",
        json={"prompt_id": str(uuid.uuid4()), "text": "x"},
    )
    assert r.status_code == 404


def test_user_input_http_409_when_nothing_pending() -> None:
    client = TestClient(create_app())
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    r = client.post(
        f"/api/sessions/{sid}/user-input",
        json={"prompt_id": str(uuid.uuid4()), "text": "y"},
    )
    assert r.status_code == 409


def test_auto_reply_skips_confirmation_when_no_callbacks_mode() -> None:
    from agent_framework_evaluator.auto_user_reply import reply_text_for_outbox_item

    pid = "abc"
    assert reply_text_for_outbox_item({"kind": "confirmation", "prompt": "ok?", "prompt_id": pid}) == "y"
    assert (
        reply_text_for_outbox_item(
            {"kind": "confirmation", "prompt": "ok?", "prompt_id": pid},
            case_run_mode="no_callbacks",
        )
        is None
    )
    assert (
        reply_text_for_outbox_item(
            {"kind": "permission", "prompt_id": pid, "request": {}},
            case_run_mode="no_callbacks",
        )
        is None
    )


def test_evaluator_llm_merge_includes_system_md_and_json_object_template() -> None:
    """Evaluator scoring uses the same runtime stack as agents (system.md + system.json_object.md)."""
    from agent_framework.model import DEFAULT_RESPONSE_MODE, ModelContext, merge_runtime_system_into_messages

    from agent_framework_evaluator.evaluation import EVALUATOR_SYSTEM_PROMPT

    eval_system = EVALUATOR_SYSTEM_PROMPT.strip()
    merged = merge_runtime_system_into_messages(
        ModelContext(
            system_prompt=eval_system,
            user_prompt="",
            messages=(
                {"role": "system", "content": eval_system},
                {"role": "user", "content": "<agent_result>x</agent_result>"},
            ),
            response_mode=DEFAULT_RESPONSE_MODE,
            tools=(),
            subagents=(),
            skills=(),
        )
    )
    first = merged.messages[0]["content"]
    assert "You are currently producing a final JSON object" in first
    assert "<allowed_tools>" in first


def test_run_evaluation_emits_debug_callback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from agent_framework.model import ModelResponse
    from agent_framework_evaluator import evaluation

    class FakeDriver:
        def decide(self, **kwargs):
            assert kwargs["provider_name"] == "fake"
            return ModelResponse(
                payload={
                    "score": 9,
                    "result": "Looks good.",
                    "evaluation": [{"criteria": "format", "passed": True, "reason": ""}],
                },
                raw_text='{"score":9}',
            )

    class FakeConfig:
        default_provider = "fake"
        default_model = ("fake-model",)

    class FakeHost:
        config = FakeConfig()

        def get_model_driver_raw(self):
            return FakeDriver()

    monkeypatch.setattr(evaluation.AgentHost, "from_env", lambda *_, **__: FakeHost())
    events: list[dict[str, object]] = []
    result = evaluation.run_evaluation(
        env_path=tmp_path / ".env",
        evaluator_prompt="Check it.",
        agent_message="ok",
        system_prompt="system",
        user_prompt="user",
        log_callback=events.append,
    )

    assert result["score"] == 9.0
    kinds = [event["kind"] for event in events]
    assert "evaluator.input_prepared" in kinds
    assert "evaluator.llm_prompt_prepared" in kinds
    assert "evaluator.result" in kinds
    llm_event = next(event for event in events if event["kind"] == "evaluator.llm_prompt_prepared")
    assert isinstance(llm_event["payload"], dict)
    assert llm_event["payload"]["messages"]


def test_evaluator_log_callback_emits_structured_trace_events() -> None:
    from agent_framework_evaluator.app import _make_evaluator_log_callback
    from agent_framework.agent_event_publisher import agent_events
    from agent_framework.tracing import TraceContext
    from agent_framework.tracing_bridge import active_tracer_scope

    class FakeTracer:
        def __init__(self) -> None:
            self.events = []

        def publish(self, event) -> None:
            self.events.append(event)

    tracer = FakeTracer()
    debug_tracer = FakeTracer()
    try:
        agent_events.attach_log_sources()
        with active_tracer_scope(tracer, TraceContext(session_id="sess-1")):
            callback = _make_evaluator_log_callback(
                tracer=tracer,
                session_id="sess-1",
                configured_level="warning",
            )
            assert callback is not None
            callback({"level": "debug", "kind": "evaluator.input_prepared", "payload": {}})
            callback({"level": "warning", "kind": "evaluator.failed", "payload": {"error": "x"}})
        assert [event.kind for event in tracer.events] == [
            "evaluator.input_prepared",
            "evaluator.failed",
        ]
        assert tracer.events[0].level == "debug"
        assert tracer.events[1].level == "warning"
        assert tracer.events[0].context.session_id == "sess-1"
        assert tracer.events[1].context.session_id == "sess-1"

        with active_tracer_scope(debug_tracer, TraceContext(session_id="sess-2")):
            debug_callback = _make_evaluator_log_callback(
                tracer=debug_tracer,
                session_id="sess-2",
                configured_level="debug",
            )
            assert debug_callback is not None
            debug_callback({"level": "debug", "kind": "evaluator.input_prepared", "payload": {}})
        assert len(debug_tracer.events) == 1
        assert debug_tracer.events[0].level == "debug"
    finally:
        agent_events.detach_log_sources()


def test_api_evaluate_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(**_: object) -> dict[str, object]:
        return {
            "score": 8.0,
            "overall_verdict": "Good.",
            "evaluation": [
                {"criteria": "tone", "passed": True, "reason": ""},
                {"criteria": "accuracy", "passed": False, "reason": "minor"},
            ],
        }

    monkeypatch.setattr("agent_framework_evaluator.app.run_evaluation", fake_run)
    client = TestClient(create_app())
    r = client.post(
        "/api/evaluate-result",
        json={
            "session_id": "",
            "evaluator_prompt": "Check tone and accuracy.",
            "agent_message": "ok",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert "score" in data
    assert 0.0 <= float(data["score"]) <= 10.0
    assert "overall_verdict" in data
    assert "evaluation" in data
    assert isinstance(data["evaluation"], list)
    assert len(data["evaluation"]) == 2


def test_api_evaluate_result_logs_full_evaluator_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_framework_evaluator.session_manager import session_manager

    def fake_run(**kwargs: object) -> dict[str, object]:
        callback = kwargs.get("log_callback")
        assert callable(callback)
        callback(
            {
                "level": "debug",
                "kind": "evaluator.input_prepared",
                "title": "Evaluator input prepared",
                "summary": "Prepared input for evaluator scoring.",
                "payload": {
                    "evaluator_prompt": kwargs["evaluator_prompt"],
                    "agent_message": kwargs["agent_message"],
                    "system_prompt": kwargs["system_prompt"],
                    "user_prompt": kwargs["user_prompt"],
                    "formatted_user_content": "<evaluation payload>",
                },
            }
        )
        return {"score": 8.0, "overall_verdict": "Good.", "evaluation": []}

    monkeypatch.setattr("agent_framework_evaluator.app.run_evaluation", fake_run)
    client = TestClient(create_app())
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    rec = session_manager.get(sid)
    assert rec is not None
    rec.last_run_prompts = {"system_prompt": "system-full", "user_prompt": "user-full"}

    r = client.post(
        "/api/evaluate-result",
        json={
            "session_id": sid,
            "evaluator_prompt": "criteria-full",
            "agent_message": "agent-full",
            "log_level": "debug",
        },
    )

    assert r.status_code == 200
    events = rec.debugger.drain(sid)
    input_events = [event for event in events if event.kind == "evaluator.input_prepared"]
    assert len(input_events) == 1
    payload = input_events[0].payload
    assert payload["evaluator_prompt"] == "criteria-full"
    assert payload["agent_message"] == "agent-full"
    assert payload["system_prompt"] == "system-full"
    assert payload["user_prompt"] == "user-full"
    assert payload["formatted_user_content"] == "<evaluation payload>"


def test_api_evaluate_case_selects_result_field(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_load_raw_test_cases(*_: object) -> list[dict[str, object]]:
        return [
            {
                "evaluation_criteria": "Check parameters.",
                "prompt": "prompt",
                "result_field": "parameters",
            }
        ]

    def fake_run(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "score": 8.0,
            "overall_verdict": "Good.",
            "evaluation": [],
        }

    monkeypatch.setattr("agent_framework_evaluator.app.load_raw_test_cases", fake_load_raw_test_cases)
    monkeypatch.setattr("agent_framework_evaluator.app.load_initializer_default_eval_model", lambda *_: None)
    monkeypatch.setattr("agent_framework_evaluator.app.run_evaluation", fake_run)
    client = TestClient(create_app())
    r = client.post(
        "/api/evaluate-case",
        json={
            "session_id": "",
            "initializer": "seed.py",
            "case_index": 0,
            "agent_message": "fallback",
            "agent_result": {"message": "not this", "parameters": {"intent": "inspect"}},
            "log_level": "debug",
        },
    )
    assert r.status_code == 200
    assert captured["agent_message"] == '{"intent": "inspect"}'


def test_markdown_case_loader_includes_result_field(tmp_path: Path) -> None:
    from agent_framework_evaluator.case_markdown import MarkdownCaseLoader

    case_file = tmp_path / "case.md"
    case_file.write_text(
        "---\n"
        "title: Parameters case\n"
        "result_field: parameters\n"
        "---\n"
        "Prompt text\n"
        "---\n"
        "Criteria text\n",
        encoding="utf-8",
    )
    cases = MarkdownCaseLoader(tmp_path, "*.md").get_test_cases()
    assert len(cases) == 1
    assert cases[0]["result_field"] == "parameters"


def test_api_evaluator_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_EVAL_DEFAULT_ENV_PATH", "/abs/env")
    monkeypatch.setenv("AGENT_EVAL_DEFAULT_AGENT", "agent-x")
    monkeypatch.setenv("AGENT_EVAL_DEFAULT_INITIALIZER", "init.py")
    client = TestClient(create_app())
    r = client.get("/api/evaluator-defaults")
    assert r.status_code == 200
    data = r.json()
    assert data["env_path"] == "/abs/env"
    assert data["agent"] == "agent-x"
    assert data["initializer"] == "init.py"


def test_api_initializers_and_template(tmp_path) -> None:
    env_f = tmp_path / ".env"
    init_d = tmp_path / "init_here"
    init_d.mkdir()
    (init_d / "seed.py").write_text(
        'PROMPT_TEMPLATE = "hello seed"\nEVALUATOR_CRITERIA = "check output"\n'
        'DEFAULT_AGENT = "seed-agent"\n',
        encoding="utf-8",
    )
    sub = init_d / "pkg"
    sub.mkdir()
    (sub / "nested.py").write_text(
        'PROMPT_TEMPLATE = "nested"\nDEFAULT_AGENT = "nested-agent"\n',
        encoding="utf-8",
    )
    (init_d / "agent_only.py").write_text('DEFAULT_AGENT = "solo-agent"\n', encoding="utf-8")
    env_f.write_text(f"AGENT_EVAL_INITIALIZER_DIR={init_d.name}\n", encoding="utf-8")

    client = TestClient(create_app())
    r = client.get("/api/initializers", params={"env_path": str(env_f)})
    assert r.status_code == 200
    data = r.json()
    assert data["env_exists"] is True
    assert "seed.py" in data["initializers"]
    assert "pkg/nested.py" in data["initializers"]

    t = client.get(
        "/api/initializer-template",
        params={"env_path": str(env_f), "initializer": "seed.py"},
    )
    assert t.status_code == 200
    tj = t.json()
    assert tj["template"] == "hello seed"
    assert "evaluator_criteria" in tj
    assert tj["evaluator_criteria"] == "check output"
    assert tj.get("agent") == "seed-agent"

    ao = client.get(
        "/api/initializer-template",
        params={"env_path": str(env_f), "initializer": "agent_only.py"},
    )
    assert ao.status_code == 200
    aoj = ao.json()
    assert aoj["agent"] == "solo-agent"
    assert aoj.get("template") == ""
    assert aoj.get("evaluator_criteria") == ""

    tn = client.get(
        "/api/initializer-template",
        params={"env_path": str(env_f), "initializer": "pkg/nested.py"},
    )
    assert tn.status_code == 200
    tnj = tn.json()
    assert tnj["template"] == "nested"
    assert tnj.get("evaluator_criteria") == ""
    assert tnj.get("agent") == "nested-agent"


def test_cli_run_supports_prompt_file(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_run_once(self, **kwargs):
        captured.update(kwargs)
        return {"status": "completed", "message": "ok"}

    monkeypatch.setattr(SessionRunner, "run_once", fake_run_once)
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("hello", encoding="utf-8")
    code = main(["run", "--agent", "root", "--prompt-file", str(prompt_path)])
    assert code == 0
    assert captured.get("prompt") == "hello"
