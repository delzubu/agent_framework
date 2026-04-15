from __future__ import annotations

import uuid
from pathlib import Path

from starlette.testclient import TestClient

from agent_framework_evaluator.app import create_app
from agent_framework_evaluator.cli import build_parser, main
from agent_framework_evaluator.runtime.session_runner import SessionRunner


def test_evaluator_cli_has_web_and_run_subcommands() -> None:
    parser = build_parser()
    args = parser.parse_args(["web", "--env", ".env"])
    assert args.command == "web"
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
