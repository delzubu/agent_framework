"""Tests for log_reader — JSONL → AuditEvent parsing."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_workflow_compiler.log_reader import read_events, events_for_run, planning_run_ids
from agent_workflow_compiler.models import AuditEvent

_LOG_PATH = Path(__file__).parent.parent.parent.parent / "agent-adventure" / "logs" / "agent-host-20260502-071519.jsonl"
_FIXTURE_AVAILABLE = _LOG_PATH.exists()


# ---------------------------------------------------------------------------
# read_events
# ---------------------------------------------------------------------------

def test_read_events_parses_all_lines(tmp_path):
    log = tmp_path / "test.jsonl"
    log.write_text(
        '\n'.join([
            json.dumps({"event_id": "e1", "kind": "a.b", "timestamp": "t1",
                        "context": {"run_id": "r1"}, "payload": {"x": 1}}),
            json.dumps({"event_id": "e2", "kind": "c.d", "timestamp": "t2",
                        "context": None, "payload": None}),
        ]),
        encoding="utf-8",
    )
    events = read_events(log)
    assert len(events) == 2
    assert events[0].kind == "a.b"
    assert events[0].payload == {"x": 1}
    assert events[1].context == {}
    assert events[1].payload == {}


def test_read_events_skips_blank_lines(tmp_path):
    log = tmp_path / "test.jsonl"
    log.write_text(
        json.dumps({"kind": "k", "timestamp": "", "context": {}, "payload": {}})
        + "\n\n\n",
        encoding="utf-8",
    )
    events = read_events(log)
    assert len(events) == 1


def test_read_events_raises_on_invalid_json(tmp_path):
    log = tmp_path / "bad.jsonl"
    log.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        read_events(log)


# ---------------------------------------------------------------------------
# events_for_run
# ---------------------------------------------------------------------------

def test_events_for_run_filters_by_prefix(tmp_path):
    events = [
        AuditEvent("e1", "k", "t", {"run_id": "abc.p1.root"}, {}),
        AuditEvent("e2", "k", "t", {"run_id": "abc.p1.root.child"}, {}),
        AuditEvent("e3", "k", "t", {"run_id": "xyz.p1.other"}, {}),
        AuditEvent("e4", "k", "t", {"run_id": None}, {}),
    ]
    result = events_for_run(events, "abc.p1.root")
    assert len(result) == 2
    assert result[0].event_id == "e1"
    assert result[1].event_id == "e2"


# ---------------------------------------------------------------------------
# planning_run_ids
# ---------------------------------------------------------------------------

def test_planning_run_ids_returns_runs_with_plan_updated(tmp_path):
    events = [
        AuditEvent("e1", "runtime.audit.agent_call_started", "t",
                   {"run_id": "run-A"}, {}),
        AuditEvent("e2", "runtime.audit.named_event", "t",
                   {"run_id": "run-A"},
                   {"event": {"type": "plan_updated", "is_initial": True, "plan": []}}),
        AuditEvent("e3", "runtime.audit.agent_call_started", "t",
                   {"run_id": "run-B"}, {}),
        # run-B has no plan_updated
    ]
    result = planning_run_ids(events)
    assert result == ["run-A"]


def test_planning_run_ids_preserves_order(tmp_path):
    events = [
        AuditEvent("e1", "runtime.audit.named_event", "t", {"run_id": "run-2"},
                   {"event": {"type": "plan_updated"}}),
        AuditEvent("e2", "runtime.agent_started", "t", {"run_id": "run-1"}, {}),
        AuditEvent("e3", "runtime.audit.named_event", "t", {"run_id": "run-1"},
                   {"event": {"type": "plan_updated"}}),
    ]
    result = planning_run_ids(events)
    assert result[0] == "run-2"
    assert result[1] == "run-1"


# ---------------------------------------------------------------------------
# Fixture log smoke test (only if the file exists)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _FIXTURE_AVAILABLE, reason="Fixture log not available.")
def test_fixture_log_has_one_planning_call():
    events = read_events(_LOG_PATH)
    run_ids = planning_run_ids(events)
    assert len(run_ids) == 1
    assert "player_controller" in run_ids[0]


@pytest.mark.skipif(not _FIXTURE_AVAILABLE, reason="Fixture log not available.")
def test_fixture_log_event_count():
    events = read_events(_LOG_PATH)
    assert len(events) > 0
    assert all(isinstance(e, AuditEvent) for e in events)
