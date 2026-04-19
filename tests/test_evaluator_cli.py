from __future__ import annotations

import json
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


def test_auto_reply_mode_behavior() -> None:
    from agent_framework_evaluator.auto_user_reply import (
        EVALUATOR_AUTO_CLARIFICATION_REPLY,
        reply_text_for_outbox_item,
    )

    pid = "abc"

    # standard mode: all outbox items forwarded to client (return None)
    assert reply_text_for_outbox_item({"kind": "confirmation", "prompt": "ok?", "prompt_id": pid}) is None
    assert reply_text_for_outbox_item({"kind": "permission", "prompt_id": pid, "request": {}}) is None
    assert reply_text_for_outbox_item({"kind": "prompt", "prompt": "clarify?", "prompt_id": pid}) is None
    assert reply_text_for_outbox_item({"kind": "question", "prompt": "which?", "prompt_id": pid}) is None

    # no_callbacks mode: everything auto-answered
    assert (
        reply_text_for_outbox_item(
            {"kind": "confirmation", "prompt": "ok?", "prompt_id": pid},
            case_run_mode="no_callbacks",
        )
        == "y"
    )
    assert (
        reply_text_for_outbox_item(
            {"kind": "permission", "prompt_id": pid, "request": {}},
            case_run_mode="no_callbacks",
        )
        == "allow"
    )
    assert (
        reply_text_for_outbox_item(
            {"kind": "prompt", "prompt": "clarify?", "prompt_id": pid},
            case_run_mode="no_callbacks",
        )
        == EVALUATOR_AUTO_CLARIFICATION_REPLY
    )
    assert (
        reply_text_for_outbox_item(
            {"kind": "question", "prompt": "which?", "prompt_id": pid},
            case_run_mode="no_callbacks",
        )
        == EVALUATOR_AUTO_CLARIFICATION_REPLY
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
    from agent_framework_evaluator.session_manager import session_manager

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
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    rec = session_manager.get(sid)
    assert rec is not None
    rec.last_run_result = {"status": "completed", "message": "ok"}
    r = client.post(
        "/api/evaluate-result",
        json={
            "session_id": sid,
            "evaluator_prompt": "Check tone and accuracy.",
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


def test_api_evaluate_result_requires_run_result() -> None:
    """Returns 400 when the session has no last_run_result."""
    client = TestClient(create_app())
    # Unknown session
    r = client.post(
        "/api/evaluate-result",
        json={"session_id": "no-such-id", "evaluator_prompt": "x"},
    )
    assert r.status_code == 400
    # Known session but no run result yet
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    r = client.post(
        "/api/evaluate-result",
        json={"session_id": sid, "evaluator_prompt": "x"},
    )
    assert r.status_code == 400


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
    rec.last_run_result = {"status": "completed", "message": "agent-full"}

    r = client.post(
        "/api/evaluate-result",
        json={
            "session_id": sid,
            "evaluator_prompt": "criteria-full",
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
    from agent_framework_evaluator.session_manager import session_manager

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
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    rec = session_manager.get(sid)
    assert rec is not None
    rec.last_run_result = {"message": "not this", "parameters": {"intent": "inspect"}}
    r = client.post(
        "/api/evaluate-case",
        json={
            "session_id": sid,
            "initializer": "seed.py",
            "case_index": 0,
            "log_level": "debug",
        },
    )
    assert r.status_code == 200
    assert captured["agent_message"] == '{"intent": "inspect"}'


def test_api_evaluate_case_requires_run_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns 400 when no last_run_result on session or no session."""
    def fake_load_raw_test_cases(*_: object) -> list[dict[str, object]]:
        return [{"evaluation_criteria": "x", "prompt": "p", "result_field": "message"}]

    monkeypatch.setattr("agent_framework_evaluator.app.load_raw_test_cases", fake_load_raw_test_cases)
    monkeypatch.setattr("agent_framework_evaluator.app.load_initializer_default_eval_model", lambda *_: None)
    client = TestClient(create_app())
    # No session at all
    r = client.post(
        "/api/evaluate-case",
        json={"session_id": "no-such", "initializer": "seed.py", "case_index": 0},
    )
    assert r.status_code == 400
    # Session exists but no run result yet
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    r = client.post(
        "/api/evaluate-case",
        json={"session_id": sid, "initializer": "seed.py", "case_index": 0},
    )
    assert r.status_code == 400


def test_api_evaluate_case_missing_result_field_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns 400 when result_field path does not exist in last_run_result."""
    from agent_framework_evaluator.session_manager import session_manager

    def fake_load_raw_test_cases(*_: object) -> list[dict[str, object]]:
        return [{"evaluation_criteria": "x", "prompt": "p", "result_field": "no_such_field"}]

    monkeypatch.setattr("agent_framework_evaluator.app.load_raw_test_cases", fake_load_raw_test_cases)
    monkeypatch.setattr("agent_framework_evaluator.app.load_initializer_default_eval_model", lambda *_: None)
    client = TestClient(create_app())
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    rec = session_manager.get(sid)
    assert rec is not None
    rec.last_run_result = {"status": "completed", "message": "ok"}
    r = client.post(
        "/api/evaluate-case",
        json={"session_id": sid, "initializer": "seed.py", "case_index": 0},
    )
    assert r.status_code == 400
    assert "no_such_field" in r.json()["detail"]


def test_api_evaluate_result_debug_trace_includes_result_field(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """evaluate-result debug log record includes result_field and last_run_result snapshot."""
    import logging

    from agent_framework_evaluator.session_manager import session_manager

    monkeypatch.setattr(
        "agent_framework_evaluator.app.run_evaluation",
        lambda **_: {"score": 7.0, "overall_verdict": "ok", "evaluation": []},
    )
    client = TestClient(create_app())
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    rec = session_manager.get(sid)
    assert rec is not None
    stored_result = {"status": "completed", "message": "test-msg", "parameters": {"k": "v"}}
    rec.last_run_result = stored_result

    with caplog.at_level(logging.DEBUG, logger="agent_framework_evaluator.evaluation"):
        r = client.post(
            "/api/evaluate-result",
            json={"session_id": sid, "evaluator_prompt": "crit", "result_field": "message", "log_level": "debug"},
        )
    assert r.status_code == 200
    entry_records = [
        record
        for record in caplog.records
        if getattr(record, "trace_kind", "") == "evaluator.evaluate_result.entry"
    ]
    assert len(entry_records) == 1
    payload = entry_records[0].trace_payload
    assert payload["result_field"] == "message"
    assert payload["last_run_result"] == stored_result


def test_ws_run_handler_persists_last_run_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """WebSocket 'run' handler stores the run result on the session record."""
    from agent_framework_evaluator.session_manager import session_manager

    fake_payload = {"status": "completed", "message": "hello"}

    def fake_run_once(self, **_: object) -> dict[str, object]:
        return fake_payload

    monkeypatch.setattr(SessionRunner, "run_once", fake_run_once)
    client = TestClient(create_app())
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    rec = session_manager.get(sid)
    assert rec is not None

    with client.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json(
            {
                "type": "run",
                "agent_id": "root",
                "prompt": "test prompt",
                "case_run_mode": "standard",
            }
        )
        # Consume messages until we see the result
        for _ in range(20):
            msg = ws.receive_json()
            if msg.get("type") == "result":
                break

    assert rec.last_run_result == fake_payload


def test_ws_run_handler_applies_no_callbacks_postfix(monkeypatch: pytest.MonkeyPatch) -> None:
    """WebSocket 'run' handler appends CASE_NO_CALLBACKS_POSTFIX when case_run_mode=no_callbacks."""
    from agent_framework_evaluator.evaluation import CASE_NO_CALLBACKS_POSTFIX
    from agent_framework_evaluator.session_manager import session_manager

    received_prompts: list[str] = []

    def fake_run_once(self: object, *, prompt: str, **_: object) -> dict[str, object]:
        received_prompts.append(prompt)
        return {"status": "completed", "message": "ok"}

    monkeypatch.setattr(SessionRunner, "run_once", fake_run_once)
    client = TestClient(create_app())
    sid = client.post("/api/sessions", json={}).json()["session_id"]

    with client.websocket_connect(f"/ws/{sid}") as ws:
        ws.send_json(
            {
                "type": "run",
                "agent_id": "root",
                "prompt": "original prompt",
                "case_run_mode": "no_callbacks",
            }
        )
        for _ in range(20):
            msg = ws.receive_json()
            if msg.get("type") == "result":
                break

    assert len(received_prompts) == 1
    assert CASE_NO_CALLBACKS_POSTFIX.strip() in received_prompts[0]


def test_markdown_case_loader_includes_result_field(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

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
    with caplog.at_level(logging.DEBUG, logger="agent_framework_evaluator.case_markdown"):
        cases = MarkdownCaseLoader(tmp_path, "*.md").get_test_cases()
    assert len(cases) == 1
    assert cases[0]["result_field"] == "parameters"
    records = [
        record
        for record in caplog.records
        if getattr(record, "trace_kind", "") == "evaluator.case_markdown.frontmatter_parsed"
    ]
    assert records
    assert records[0].trace_payload["frontmatter"]["result_field"] == "parameters"


def test_initializer_catalog_preserves_markdown_result_field(tmp_path: Path) -> None:
    from agent_framework_evaluator.initializer_catalog import load_raw_test_cases, load_test_cases

    env_f = tmp_path / ".env"
    env_f.write_text(f"AGENT_EVAL_INITIALIZER_DIR={tmp_path.as_posix()}\n", encoding="utf-8")
    init_f = tmp_path / "initializer.py"
    init_f.write_text(
        "from pathlib import Path\n"
        "from agent_framework_evaluator.case_markdown import MarkdownCaseLoader\n"
        "def get_test_cases():\n"
        "    return MarkdownCaseLoader(Path(__file__).parent, '*.case.md').get_test_cases()\n",
        encoding="utf-8",
    )
    case_f = tmp_path / "parameters.case.md"
    case_f.write_text(
        "---\n"
        "title: Parameters case\n"
        "result_field: parameters\n"
        "---\n"
        "Prompt text\n"
        "---\n"
        "Criteria text\n",
        encoding="utf-8",
    )

    raw_cases = load_raw_test_cases(env_f, "initializer.py")
    serialized_cases = load_test_cases(env_f, "initializer.py")

    assert raw_cases[0]["result_field"] == "parameters"
    assert serialized_cases[0]["result_field"] == "parameters"


def test_initializer_catalog_preserves_code_evaluators(tmp_path: Path) -> None:
    """Regression test for #34: code_evaluators must survive the load_raw_test_cases roundtrip."""
    from agent_framework_evaluator.initializer_catalog import load_raw_test_cases, load_test_cases

    env_f = tmp_path / ".env"
    env_f.write_text(f"AGENT_EVAL_INITIALIZER_DIR={tmp_path.as_posix()}\n", encoding="utf-8")

    def my_evaluator(prompt: str, agent_message: str) -> dict:
        return {"score": 10, "evaluation": [], "result": "ok"}

    init_f = tmp_path / "initializer.py"
    case_f = tmp_path / "case.case.md"
    case_f.write_text(
        "---\ntitle: Eval case\ncode_evaluator: my_evaluator\n---\nPrompt\n---\nCriteria\n",
        encoding="utf-8",
    )
    init_f.write_text(
        "from pathlib import Path\n"
        "from agent_framework_evaluator.case_markdown import MarkdownCaseLoader\n"
        "def my_evaluator(prompt, agent_message): return {'score': 10, 'evaluation': [], 'result': 'ok'}\n"
        "def get_test_cases():\n"
        "    return MarkdownCaseLoader(Path(__file__).parent, '*.case.md', {'my_evaluator': my_evaluator}).get_test_cases()\n",
        encoding="utf-8",
    )

    raw_cases = load_raw_test_cases(env_f, "initializer.py")
    assert len(raw_cases) == 1
    evaluators = raw_cases[0].get("code_evaluators", [])
    assert len(evaluators) == 1, "code_evaluators must survive the roundtrip through load_raw_test_cases"
    assert callable(evaluators[0])

    serialized = load_test_cases(env_f, "initializer.py")
    assert serialized[0]["has_code_evaluator"] is True


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


# ---------------------------------------------------------------------------
# CLI evaluate subcommand tests
# ---------------------------------------------------------------------------


def _make_case_md(tmp_path: Path, *, title: str = "T", result_field: str = "message") -> Path:
    """Write a minimal valid case .md to tmp_path."""
    p = tmp_path / f"{title}.md"
    p.write_text(
        f"---\ntitle: {title}\nresult_field: {result_field}\n---\nDo the thing.\n---\nCheck output.\n",
        encoding="utf-8",
    )
    return p


def _make_fake_run_once(message: str = "result-text"):
    """Return a fake SessionRunner.run_once that always returns {message: message}."""

    def fake(self, **kwargs):
        return {"status": "completed", "message": message}

    return fake


def _fake_run_evaluation(**kwargs):
    return {"score": 8.0, "overall_verdict": "Good.", "evaluation": []}


def test_cli_evaluate_has_evaluate_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["evaluate", "--case-file", "x.md"])
    assert args.command == "evaluate"
    assert args.case_file == "x.md"

    args2 = parser.parse_args(["evaluate", "--initializer", "init.py", "--case", "0"])
    assert args2.initializer == "init.py"
    assert args2.case == 0


def test_cli_evaluate_case_file_single(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--case-file runs a single .md file and prints JSON output."""
    case_path = _make_case_md(tmp_path, title="basic")
    monkeypatch.setattr(SessionRunner, "run_once", _make_fake_run_once("hello output"))
    monkeypatch.setattr("agent_framework_evaluator.evaluation.run_evaluation", _fake_run_evaluation)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")

    code = main(["evaluate", "--case-file", str(case_path)])
    assert code == 0


def test_cli_evaluate_case_file_writes_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_path = _make_case_md(tmp_path, title="out")
    out_path = tmp_path / "result.json"
    monkeypatch.setattr(SessionRunner, "run_once", _make_fake_run_once("output"))
    monkeypatch.setattr("agent_framework_evaluator.evaluation.run_evaluation", _fake_run_evaluation)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")

    code = main(["evaluate", "--case-file", str(case_path), "--output", str(out_path)])
    assert code == 0
    assert out_path.exists()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert "average_score" in data
    assert "run_result" in data


def test_cli_evaluate_initializer_single_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--initializer --case N runs one case by index."""
    env_f = tmp_path / ".env"
    env_f.write_text(f"AGENT_EVAL_INITIALIZER_DIR={tmp_path.as_posix()}\n", encoding="utf-8")
    init_f = tmp_path / "init.py"
    case_md = _make_case_md(tmp_path, title="c1")
    init_f.write_text(
        "from pathlib import Path\n"
        "from agent_framework_evaluator.case_markdown import MarkdownCaseLoader\n"
        f"CASES_GLOB = '{case_md.name}'\n"
        "def get_test_cases():\n"
        f"    return MarkdownCaseLoader(Path(__file__).parent, CASES_GLOB).get_test_cases()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(SessionRunner, "run_once", _make_fake_run_once("answer"))
    monkeypatch.setattr("agent_framework_evaluator.evaluation.run_evaluation", _fake_run_evaluation)

    code = main(["evaluate", "--env", str(env_f), "--initializer", "init.py", "--case", "0"])
    assert code == 0


def test_cli_evaluate_batch_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Batch run prints summary table and returns 0."""
    env_f = tmp_path / ".env"
    env_f.write_text(f"AGENT_EVAL_INITIALIZER_DIR={tmp_path.as_posix()}\n", encoding="utf-8")
    c1 = _make_case_md(tmp_path, title="alpha")
    c2 = _make_case_md(tmp_path, title="beta")
    init_f = tmp_path / "init.py"
    init_f.write_text(
        "from pathlib import Path\n"
        "from agent_framework_evaluator.case_markdown import MarkdownCaseLoader\n"
        "def get_test_cases():\n"
        f"    return MarkdownCaseLoader(Path(__file__).parent, '*.md').get_test_cases()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(SessionRunner, "run_once", _make_fake_run_once("result"))
    monkeypatch.setattr("agent_framework_evaluator.evaluation.run_evaluation", _fake_run_evaluation)

    code = main(["evaluate", "--env", str(env_f), "--initializer", "init.py"])
    assert code == 0
    out = capsys.readouterr().out
    assert "alpha" in out or "beta" in out


def test_cli_evaluate_batch_output_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_f = tmp_path / ".env"
    env_f.write_text(f"AGENT_EVAL_INITIALIZER_DIR={tmp_path.as_posix()}\n", encoding="utf-8")
    _make_case_md(tmp_path, title="single")
    init_f = tmp_path / "init.py"
    init_f.write_text(
        "from pathlib import Path\n"
        "from agent_framework_evaluator.case_markdown import MarkdownCaseLoader\n"
        "def get_test_cases():\n"
        "    return MarkdownCaseLoader(Path(__file__).parent, '*.md').get_test_cases()\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "batch.json"
    monkeypatch.setattr(SessionRunner, "run_once", _make_fake_run_once("x"))
    monkeypatch.setattr("agent_framework_evaluator.evaluation.run_evaluation", _fake_run_evaluation)

    code = main(["evaluate", "--env", str(env_f), "--initializer", "init.py", "--output", str(out_path)])
    assert code == 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["title"] == "single"


# ---------------------------------------------------------------------------
# File reference expansion in case markdown tests
# ---------------------------------------------------------------------------


def test_case_markdown_expands_file_refs(tmp_path: Path) -> None:
    """@filename tokens in case prompt are expanded using the case file's directory."""
    from agent_framework_evaluator.case_markdown import parse_case_markdown_file

    context_file = tmp_path / "deck.txt"
    context_file.write_text("slide content here", encoding="utf-8")

    case_md = tmp_path / "case01.md"
    case_md.write_text(
        "---\ntitle: test\n---\nAnalyze @deck.txt\n---\nShould summarize slides\n",
        encoding="utf-8",
    )

    result = parse_case_markdown_file(path=case_md, evaluator_registry={})
    assert result is not None
    assert "slide content here" in result["prompt"]
    assert "@deck.txt" not in result["prompt"]


def test_case_markdown_missing_ref_left_unchanged(tmp_path: Path) -> None:
    from agent_framework_evaluator.case_markdown import parse_case_markdown_file

    case_md = tmp_path / "case02.md"
    case_md.write_text(
        "---\ntitle: test\n---\nSee @ghost.txt\n---\ncriteria\n",
        encoding="utf-8",
    )

    result = parse_case_markdown_file(path=case_md, evaluator_registry={})
    assert result is not None
    assert "@ghost.txt" in result["prompt"]  # left unchanged


def test_case_markdown_custom_resolver(tmp_path: Path) -> None:
    from pathlib import Path as P

    from agent_framework.file_reference import FileReferenceResolver
    from agent_framework_evaluator.case_markdown import parse_case_markdown_file

    class UpperResolver:
        def resolve(self, path: P) -> str:
            return f"[{path.name.upper()}]"

    (tmp_path / "data.csv").write_text("a,b,c", encoding="utf-8")
    case_md = tmp_path / "case03.md"
    case_md.write_text(
        "---\ntitle: t\n---\nLoad @data.csv\n---\ncriteria\n",
        encoding="utf-8",
    )

    result = parse_case_markdown_file(path=case_md, evaluator_registry={}, resolver=UpperResolver())
    assert result is not None
    assert "[DATA.CSV]" in result["prompt"]


def test_markdown_case_loader_expands_refs(tmp_path: Path) -> None:
    from agent_framework_evaluator.case_markdown import MarkdownCaseLoader

    (tmp_path / "info.txt").write_text("important context", encoding="utf-8")
    (tmp_path / "case.md").write_text(
        "---\ntitle: t\n---\nSee @info.txt\n---\ncriteria\n",
        encoding="utf-8",
    )

    loader = MarkdownCaseLoader(tmp_path, "*.md")
    cases = loader.get_test_cases()
    assert len(cases) == 1
    assert "important context" in cases[0]["prompt"]
