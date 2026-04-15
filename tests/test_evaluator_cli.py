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
