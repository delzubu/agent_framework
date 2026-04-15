from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from agent_framework_evaluator.app import create_app
from agent_framework_evaluator.evaluation import (
    extract_initial_prompts,
    format_eval_input,
    parse_eval_response,
)
from agent_framework_evaluator.initializer_catalog import load_initializer_default_evaluator_criteria


def test_extract_initial_prompts_openai_plain() -> None:
    payload = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]
    out = extract_initial_prompts(payload)
    assert out["system_prompt"] == "SYS"
    assert out["user_prompt"] == "USR"


def test_extract_initial_prompts_openai_with_format() -> None:
    payload = {"input": [{"role": "system", "content": "A"}, {"role": "user", "content": "B"}]}
    out = extract_initial_prompts(payload)
    assert out["system_prompt"] == "A"
    assert out["user_prompt"] == "B"


def test_extract_initial_prompts_dial() -> None:
    payload = {"messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]}
    out = extract_initial_prompts(payload)
    assert out["system_prompt"] == "S"
    assert out["user_prompt"] == "U"


def test_format_eval_input_contains_all_tags() -> None:
    s = format_eval_input("sys", "usr", "crit", "result")
    assert "<system_prompt>" in s and "sys" in s
    assert "<user_prompt>" in s and "usr" in s
    assert "<evaluation_criteria>" in s and "crit" in s
    assert "<agent_result>" in s and "result" in s


def test_parse_eval_response_maps_keys_and_clamps() -> None:
    out = parse_eval_response(
        {
            "score": 11,
            "evaluation": [
                {"criteria": "one", "passed": True, "reason": ""},
                {"criteria": "two", "passed": True, "reason": "ok"},
                {"criteria": "three", "passed": False, "reason": "gap"},
            ],
            "result": "Overall ok",
        }
    )
    assert out["score"] == 10.0
    assert out["overall_verdict"] == "Overall ok"
    ev = out["evaluation"]
    assert len(ev) == 3
    assert ev[0]["criteria"] == "one" and ev[0]["passed"] is True
    assert ev[2]["passed"] is False and ev[2]["reason"] == "gap"


def test_parse_eval_response_legacy_hits_misses() -> None:
    out = parse_eval_response(
        {
            "score": 5,
            "hits": ["a"],
            "misses": ["b"],
            "verdict": "Legacy",
        }
    )
    assert out["overall_verdict"] == "Legacy"
    assert len(out["evaluation"]) == 2
    assert out["evaluation"][0] == {"criteria": "a", "passed": True, "reason": ""}
    assert out["evaluation"][1] == {"criteria": "b", "passed": False, "reason": ""}


def test_load_initializer_evaluator_criteria(tmp_path: Path) -> None:
    init_d = tmp_path / "init"
    init_d.mkdir()
    (init_d / "mod.py").write_text('EVALUATOR_CRITERIA = "judge this"\n', encoding="utf-8")
    env_f = tmp_path / ".env"
    env_f.write_text(f"AGENT_EVAL_INITIALIZER_DIR={init_d.name}\n", encoding="utf-8")
    text = load_initializer_default_evaluator_criteria(env_f.resolve(), "mod.py")
    assert text == "judge this"


def test_load_initializer_criteria_only_module(tmp_path: Path) -> None:
    init_d = tmp_path / "init2"
    init_d.mkdir()
    (init_d / "only_crit.py").write_text('EVALUATOR_CRITERIA = "only"\n', encoding="utf-8")
    env_f = tmp_path / ".env"
    env_f.write_text(f"AGENT_EVAL_INITIALIZER_DIR={init_d.name}\n", encoding="utf-8")
    client = TestClient(create_app())
    r = client.get(
        "/api/initializer-template",
        params={"env_path": str(env_f.resolve()), "initializer": "only_crit.py"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["template"] == ""
    assert data["evaluator_criteria"] == "only"
