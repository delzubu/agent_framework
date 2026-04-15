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
    assert 1.0 <= float(data["score"]) <= 10.0
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
        'PROMPT_TEMPLATE = "hello seed"\nEVALUATOR_CRITERIA = "check output"\n',
        encoding="utf-8",
    )
    sub = init_d / "pkg"
    sub.mkdir()
    (sub / "nested.py").write_text('PROMPT_TEMPLATE = "nested"\n', encoding="utf-8")
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

    tn = client.get(
        "/api/initializer-template",
        params={"env_path": str(env_f), "initializer": "pkg/nested.py"},
    )
    assert tn.status_code == 200
    tnj = tn.json()
    assert tnj["template"] == "nested"
    assert tnj.get("evaluator_criteria") == ""


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
