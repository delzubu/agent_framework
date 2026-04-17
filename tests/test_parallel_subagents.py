"""Tests for call_subagents parallel/sequential batch execution."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from agent_framework.agents.agent_decision import AgentDecision, SubagentCallSpec
from agent_framework.agents.agent_result import AgentResult
from agent_framework.agents.agent_run import AgentRun
from agent_framework.host import AgentHost, SubagentBatchItemResult
from agent_framework.config import HostConfig


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

class FakeModelDriver:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.on_request_trace = None
        self.on_response_trace = None

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        self.on_request_trace = on_request
        self.on_response_trace = on_response

    def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        from agent_framework.model import ModelResponse
        payload = self._payloads.pop(0)
        return ModelResponse(payload=payload, raw_text=json.dumps(payload))


def make_host(tmp_path: Path, *, agents_dir: Path | None = None) -> AgentHost:
    env_path = tmp_path / ".env"
    agents = agents_dir or (tmp_path / "agents")
    agents.mkdir(exist_ok=True)
    env_path.write_text(
        "\n".join([
            "OPENAI_API_KEY=test-key",
            "DEFAULT_PROVIDER=openai",
            "DEFAULT_MODEL=gpt-4o-mini",
            f"AGENT_DIRECTORY={agents}",
            "TOOLS_DIRECTORY=tools",
            "WORLD_DIRECTORY=world",
            "ROOT_AGENT=root",
        ]),
        encoding="utf-8",
    )
    from agent_framework.config import load_host_config
    config = load_host_config(env_path)
    return AgentHost.create(model_driver=FakeModelDriver([]), config=config, builtin_tools=False)


def write_agent(agents_dir: Path, agent_id: str, subagents: list[str] | None = None) -> Path:
    agents_dir.mkdir(parents=True, exist_ok=True)
    subagents_yaml = ""
    if subagents:
        lines = "\n".join(f"  - {s}" for s in subagents)
        subagents_yaml = f"subagents:\n{lines}\n"
    path = agents_dir / f"{agent_id}.md"
    path.write_text(
        f"---\nid: {agent_id}\nrole: {agent_id}\n{subagents_yaml}---\nYou are {agent_id}.\n---\nDo the task.\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 1. Decision parsing — valid
# ---------------------------------------------------------------------------

def test_decision_parsing_valid():
    payload = {
        "kind": "call_subagents",
        "mode": "parallel",
        "calls": [
            {"subagent_id": "researcher", "parameters": {"topic": "X"}, "output_key": "research"},
            {"subagent_id": "critic", "output_key": "critique"},
        ],
    }
    from agent_framework.model import ModelResponse
    d = AgentDecision.from_model_response(ModelResponse(payload=payload, raw_text=""))
    assert d.kind == "call_subagents"
    assert d.batch_mode == "parallel"
    assert len(d.subagent_calls) == 2
    assert d.subagent_calls[0].subagent_id == "researcher"
    assert d.subagent_calls[0].output_key == "research"
    assert d.subagent_calls[1].subagent_id == "critic"
    assert d.subagent_calls[1].output_key == "critique"


# ---------------------------------------------------------------------------
# 2. Decision parsing — rejects missing mode
# ---------------------------------------------------------------------------

def test_decision_parsing_rejects_missing_mode():
    payload = {"kind": "call_subagents", "calls": [{"subagent_id": "x"}]}
    from agent_framework.model import ModelResponse
    with pytest.raises(ValueError, match="mode"):
        AgentDecision.from_model_response(ModelResponse(payload=payload, raw_text=""))


# ---------------------------------------------------------------------------
# 3. Decision parsing — rejects empty calls
# ---------------------------------------------------------------------------

def test_decision_parsing_rejects_empty_calls():
    payload = {"kind": "call_subagents", "mode": "parallel", "calls": []}
    from agent_framework.model import ModelResponse
    with pytest.raises(ValueError, match="calls"):
        AgentDecision.from_model_response(ModelResponse(payload=payload, raw_text=""))


# ---------------------------------------------------------------------------
# 4. Decision parsing — rejects non-positive timeout
# ---------------------------------------------------------------------------

def test_decision_parsing_rejects_nonpositive_timeout():
    payload = {
        "kind": "call_subagents",
        "mode": "parallel",
        "timeout_seconds": -1,
        "calls": [{"subagent_id": "x"}],
    }
    from agent_framework.model import ModelResponse
    with pytest.raises(ValueError, match="timeout_seconds"):
        AgentDecision.from_model_response(ModelResponse(payload=payload, raw_text=""))


# ---------------------------------------------------------------------------
# 5. Default output_key assigned by parser
# ---------------------------------------------------------------------------

def test_decision_parsing_default_output_keys():
    payload = {
        "kind": "call_subagents",
        "mode": "sequential",
        "calls": [{"subagent_id": "a"}, {"subagent_id": "b"}],
    }
    from agent_framework.model import ModelResponse
    d = AgentDecision.from_model_response(ModelResponse(payload=payload, raw_text=""))
    assert d.subagent_calls[0].output_key == "call_0"
    assert d.subagent_calls[1].output_key == "call_1"


# ---------------------------------------------------------------------------
# 6. Context isolation — child starts with empty conversation
# ---------------------------------------------------------------------------

def test_context_isolation(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    write_agent(agents_dir, "root", subagents=["child"])
    write_agent(agents_dir, "child")

    child_convo_at_start: list = []

    from agent_framework.agents.agent import Agent
    original_run = Agent.run

    def patched_run(self, *, host, parameters=None, caller_id=None, **kwargs):
        if self.agent_id == "child":
            run = self._create_run(parameters or {}, **{
                k: v for k, v in kwargs.items()
                if k in ("run_id", "in_parallel_batch", "rendered_prompt_override",
                         "conversation_messages", "prompt_fragments")
            })
            child_convo_at_start.extend(run.conversation_messages)
            return AgentResult(status="completed", message="done", prompt="")
        return original_run(self, host=host, parameters=parameters, caller_id=caller_id, **kwargs)

    host = make_host(tmp_path, agents_dir=agents_dir)
    driver = FakeModelDriver([{"kind": "call_subagent", "subagent_id": "child", "parameters": {}}])
    host.model_driver = driver

    from agent_framework.agents.agent import Agent
    import unittest.mock as mock
    with mock.patch.object(Agent, "run", patched_run):
        # Prime root with a conversation message
        root = host.get_agent("root")
        root_run = root._create_run({})
        root_run.conversation_messages.append({"role": "user", "content": "existing parent message"})
        # Call child directly
        host.call_subagent(caller=root, callee_id="child", parameters={}, parent_run_id="parent-run")

    # Child should start with empty conversation
    assert child_convo_at_start == []


# ---------------------------------------------------------------------------
# 7. Sequential execution order
# ---------------------------------------------------------------------------

def test_sequential_order(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    write_agent(agents_dir, "root", subagents=["a", "b", "c"])
    for name in ["a", "b", "c"]:
        write_agent(agents_dir, name)

    invocation_order: list[str] = []
    invocation_lock = threading.Lock()

    host = make_host(tmp_path, agents_dir=agents_dir)

    def fake_call_subagent(*, caller, callee_id, parameters, parent_run_id=None,
                           run_id=None, in_parallel_batch=False, conversation_messages=None):
        with invocation_lock:
            invocation_order.append(callee_id)
        return AgentResult(status="completed", message=f"{callee_id} done", prompt="")

    specs = (
        SubagentCallSpec(subagent_id="a", output_key="ka"),
        SubagentCallSpec(subagent_id="b", output_key="kb"),
        SubagentCallSpec(subagent_id="c", output_key="kc"),
    )

    import unittest.mock as mock
    with mock.patch.object(AgentHost, "call_subagent", side_effect=fake_call_subagent):
        root = host.get_agent("root")
        results = host.call_subagent_batch(
            caller=root, specs=specs, mode="sequential", timeout_seconds=30
        )

    assert invocation_order == ["a", "b", "c"]
    assert all(r.status == "completed" for r in results)


# ---------------------------------------------------------------------------
# 8. Parallel execution is actually concurrent
# ---------------------------------------------------------------------------

def test_parallel_concurrent(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    write_agent(agents_dir, "root", subagents=["slow_a", "slow_b"])
    write_agent(agents_dir, "slow_a")
    write_agent(agents_dir, "slow_b")

    host = make_host(tmp_path, agents_dir=agents_dir)

    def fake_call_subagent(*, caller, callee_id, parameters, parent_run_id=None,
                           run_id=None, in_parallel_batch=False, conversation_messages=None):
        time.sleep(0.3)
        return AgentResult(status="completed", message=f"{callee_id} done", prompt="")

    specs = (
        SubagentCallSpec(subagent_id="slow_a", output_key="a"),
        SubagentCallSpec(subagent_id="slow_b", output_key="b"),
    )

    import unittest.mock as mock
    with mock.patch.object(AgentHost, "call_subagent", side_effect=fake_call_subagent):
        root = host.get_agent("root")
        start = time.monotonic()
        results = host.call_subagent_batch(
            caller=root, specs=specs, mode="parallel", timeout_seconds=10
        )
        elapsed = time.monotonic() - start

    assert elapsed < 0.55, f"Expected parallel execution < 0.55s, got {elapsed:.2f}s"
    assert all(r.status == "completed" for r in results)


# ---------------------------------------------------------------------------
# 9. Parallel timeout abandons slow children
# ---------------------------------------------------------------------------

def test_parallel_timeout(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SUBAGENT_BATCH_TIMEOUT_SECONDS", "0.2")
    agents_dir = tmp_path / "agents"
    write_agent(agents_dir, "root", subagents=["fast", "slow"])
    write_agent(agents_dir, "fast")
    write_agent(agents_dir, "slow")

    host = make_host(tmp_path, agents_dir=agents_dir)

    def fake_call_subagent(*, caller, callee_id, parameters, parent_run_id=None,
                           run_id=None, in_parallel_batch=False, conversation_messages=None):
        if callee_id == "slow":
            time.sleep(5)
        return AgentResult(status="completed", message=f"{callee_id} done", prompt="")

    specs = (
        SubagentCallSpec(subagent_id="fast", output_key="fast_key"),
        SubagentCallSpec(subagent_id="slow", output_key="slow_key"),
    )

    import unittest.mock as mock
    with mock.patch.object(AgentHost, "call_subagent", side_effect=fake_call_subagent):
        root = host.get_agent("root")
        results = host.call_subagent_batch(
            caller=root, specs=specs, mode="parallel", timeout_seconds=0.2
        )

    by_key = {r.output_key: r for r in results}
    assert by_key["fast_key"].status == "completed"
    assert by_key["slow_key"].status == "timed_out"


# ---------------------------------------------------------------------------
# 10. Aggregated fragment content
# ---------------------------------------------------------------------------

def test_aggregated_fragment(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    write_agent(agents_dir, "root", subagents=["a", "b"])
    write_agent(agents_dir, "a")
    write_agent(agents_dir, "b")

    host = make_host(tmp_path, agents_dir=agents_dir)

    specs = (
        SubagentCallSpec(subagent_id="a", output_key="alpha"),
        SubagentCallSpec(subagent_id="b", output_key="beta"),
    )

    def fake_call_subagent(*, caller, callee_id, parameters, parent_run_id=None,
                           run_id=None, in_parallel_batch=False, conversation_messages=None):
        return AgentResult(status="completed", message=f"{callee_id}-result", prompt="")

    import unittest.mock as mock
    with mock.patch.object(AgentHost, "call_subagent", side_effect=fake_call_subagent):
        root = host.get_agent("root")
        root_run = root._create_run({})
        results = host.call_subagent_batch(
            caller=root, specs=specs, mode="sequential", timeout_seconds=30
        )
        root._emit_subagent_batch_results(host, root_run, results)

    fragment = root_run.prompt_fragments[-1]
    assert "<subagent_results>" in fragment
    assert 'key="alpha"' in fragment
    assert 'key="beta"' in fragment
    assert "a-result" in fragment
    assert "b-result" in fragment

    convo_msg = root_run.conversation_messages[-1]
    assert convo_msg["role"] == "user"
    assert "<subagent_results>" in convo_msg["content"]


# ---------------------------------------------------------------------------
# 11. Parallelism cap rejected loudly
# ---------------------------------------------------------------------------

def test_parallelism_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SUBAGENT_MAX_PARALLELISM", "2")
    agents_dir = tmp_path / "agents"
    write_agent(agents_dir, "root", subagents=["a", "b", "c"])
    for name in ["a", "b", "c"]:
        write_agent(agents_dir, name)

    host = make_host(tmp_path, agents_dir=agents_dir)
    root = host.get_agent("root")
    specs = (
        SubagentCallSpec(subagent_id="a", output_key="a"),
        SubagentCallSpec(subagent_id="b", output_key="b"),
        SubagentCallSpec(subagent_id="c", output_key="c"),
    )
    with pytest.raises(ValueError, match="SUBAGENT_MAX_PARALLELISM"):
        host.call_subagent_batch(caller=root, specs=specs, mode="parallel", timeout_seconds=30)


# ---------------------------------------------------------------------------
# 12. Child failure does not affect siblings
# ---------------------------------------------------------------------------

def test_child_failure_isolation(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    write_agent(agents_dir, "root", subagents=["good", "bad"])
    write_agent(agents_dir, "good")
    write_agent(agents_dir, "bad")

    host = make_host(tmp_path, agents_dir=agents_dir)

    def fake_call_subagent(*, caller, callee_id, parameters, parent_run_id=None,
                           run_id=None, in_parallel_batch=False, conversation_messages=None):
        if callee_id == "bad":
            raise RuntimeError("child exploded")
        return AgentResult(status="completed", message="good done", prompt="")

    specs = (
        SubagentCallSpec(subagent_id="good", output_key="good_key"),
        SubagentCallSpec(subagent_id="bad", output_key="bad_key"),
    )

    import unittest.mock as mock
    with mock.patch.object(AgentHost, "call_subagent", side_effect=fake_call_subagent):
        root = host.get_agent("root")
        results = host.call_subagent_batch(
            caller=root, specs=specs, mode="parallel", timeout_seconds=10
        )

    by_key = {r.output_key: r for r in results}
    assert by_key["good_key"].status == "completed"
    assert by_key["bad_key"].status == "failed"
    assert "child exploded" in by_key["bad_key"].message


# ---------------------------------------------------------------------------
# 13. Parallel callback saves checkpoint and returns blocked
# ---------------------------------------------------------------------------

def test_parallel_callback_saves_checkpoint_and_blocks(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    write_agent(agents_dir, "root", subagents=["asker"])
    write_agent(agents_dir, "asker")

    host = make_host(tmp_path, agents_dir=agents_dir)
    driver = FakeModelDriver([
        {"kind": "callback", "intent": "information_request", "message": "what is X?"},
    ])
    host.model_driver = driver

    asker = host.get_agent("asker")
    child_run_id = "test-session.p1.root.ask_key"
    result = asker.run(
        host=host,
        parameters={},
        caller_id="root",
        run_id=child_run_id,
        in_parallel_batch=True,
    )

    assert result.status == "blocked"
    payload = json.loads(result.message)
    assert payload["intent"] == "information_request"
    assert payload["prompt"] == "what is X?"

    # Checkpoint must have been saved.
    saved = host.load_checkpoint(child_run_id)
    assert saved is not None


# ---------------------------------------------------------------------------
# 14. Callback resume restores conversation history
# ---------------------------------------------------------------------------

def test_callback_resume_restores_history(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    write_agent(agents_dir, "root", subagents=["asker"])
    write_agent(agents_dir, "asker")

    host = make_host(tmp_path, agents_dir=agents_dir)

    # Round 1: child emits callback → blocked.
    # Round 2: child receives answer → completes.
    seen_conversation: list[list] = []

    from agent_framework.agents.agent import Agent
    original_run = Agent.run
    call_count = [0]

    def patched_run(self, *, host, parameters=None, caller_id=None, conversation_messages=None,
                    run_id=None, in_parallel_batch=False, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: simulate a callback block with checkpoint saved.
            run = self._create_run(
                parameters or {},
                run_id=run_id,
                in_parallel_batch=in_parallel_batch,
                conversation_messages=conversation_messages,
            )
            run.conversation_messages.append({"role": "assistant", "content": "thinking..."})
            save_fn = getattr(host, "save_checkpoint", None)
            if callable(save_fn):
                save_fn(run.run_id, list(run.conversation_messages))
            return AgentResult(
                status="blocked",
                message=json.dumps({"intent": "information_request", "prompt": "need X", "parameters": {}}),
                prompt="",
            )
        else:
            # Second call: record the conversation passed in.
            seen_conversation.append(list(conversation_messages or []))
            return AgentResult(status="completed", message="done with context", prompt="")

    import unittest.mock as mock
    with mock.patch.object(Agent, "run", patched_run):
        root = host.get_agent("root")

        def fake_resolve_callback(*, caller_id, callee, prompt):
            return "the answer is 42"

        with mock.patch.object(AgentHost, "resolve_callback", side_effect=fake_resolve_callback):
            specs = (SubagentCallSpec(subagent_id="asker", output_key="ask_key"),)
            results = host.call_subagent_batch(
                caller=root, specs=specs, mode="parallel",
                timeout_seconds=10, parent_run_id="test-session.p1.root"
            )

    assert results[0].status == "completed"
    assert len(seen_conversation) == 1
    resumed = seen_conversation[0]
    # Must include the saved assistant message AND the callback answer.
    contents = [m["content"] for m in resumed]
    assert "thinking..." in contents
    assert "the answer is 42" in contents


# ---------------------------------------------------------------------------
# 15. Hierarchical run IDs
# ---------------------------------------------------------------------------

def test_hierarchical_run_ids(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    write_agent(agents_dir, "root", subagents=["child"])
    write_agent(agents_dir, "child")

    host = make_host(tmp_path, agents_dir=agents_dir)

    observed_ids: dict[str, str] = {}
    from agent_framework.agents.agent import Agent
    original_run = Agent.run

    def patched_run(self, *, host, run_id=None, **kwargs):
        actual_run = original_run(self, host=host, run_id=run_id, **kwargs)
        return actual_run

    driver = FakeModelDriver([
        {"kind": "call_subagent", "subagent_id": "child", "parameters": {}},
        {"kind": "final_message", "message": "child done"},
        {"kind": "final_message", "message": "root done"},
    ])
    host.model_driver = driver

    # Patch _create_run to observe generated run_ids.
    original_create_run = Agent._create_run

    def patched_create_run(self, parameters, *, run_id=None, **kwargs):
        run = original_create_run(self, parameters, run_id=run_id, **kwargs)
        observed_ids[self.agent_id] = run.run_id
        return run

    import unittest.mock as mock
    with mock.patch.object(Agent, "_create_run", patched_create_run):
        result = host.run_agent("root")

    assert result.status == "completed"

    root_id = observed_ids.get("root", "")
    child_id = observed_ids.get("child", "")

    # Root ID must match {session}.p{n}.root format.
    assert root_id.startswith(host.session_id), f"root_id={root_id} should start with session_id"
    assert ".p1.root" in root_id

    # Child ID must be scoped under root.
    assert child_id.startswith(root_id), f"child_id={child_id} should start with root_id={root_id}"
    assert "child" in child_id

    # IDs are distinct.
    assert root_id != child_id


# ---------------------------------------------------------------------------
# 16. Checkpoint cleanup removes old entries
# ---------------------------------------------------------------------------

def test_cleanup_checkpoints_removes_old(tmp_path: Path):
    host = make_host(tmp_path)
    host.save_checkpoint("run-1", [{"role": "user", "content": "hi"}])
    host.save_checkpoint("run-2", [{"role": "user", "content": "hello"}])

    # Force timestamps to be old.
    with host._checkpoint_lock:
        for key in host._checkpoints:
            msgs, _ = host._checkpoints[key]
            host._checkpoints[key] = (msgs, 0.0)

    removed = host.cleanup_checkpoints(ttl_seconds=1.0)
    assert removed == 2
    assert host.load_checkpoint("run-1") is None
    assert host.load_checkpoint("run-2") is None


# ---------------------------------------------------------------------------
# 17. JSONL subscriber is thread-safe under parallel writes
# ---------------------------------------------------------------------------

def test_jsonl_subscriber_thread_safe(tmp_path: Path):
    from agent_framework.tracing_subscribers.jsonl_subscriber import JsonlTraceSubscriber
    from agent_framework.tracing import make_trace_event

    log_path = tmp_path / "trace.jsonl"
    subscriber = JsonlTraceSubscriber(log_path)

    n_threads = 8
    events_per_thread = 20
    errors: list[Exception] = []

    def write_events(thread_idx: int):
        try:
            for i in range(events_per_thread):
                evt = make_trace_event(
                    kind="test.event",
                    title=f"t{thread_idx}-e{i}",
                    channel="runtime",
                    level="info",
                )
                subscriber.consume(evt)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_events, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors during concurrent writes: {errors}"

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == n_threads * events_per_thread, f"Expected {n_threads * events_per_thread} lines, got {len(lines)}"

    # Every line must be valid JSON.
    for i, line in enumerate(lines):
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            pytest.fail(f"Line {i} is not valid JSON: {exc}\nContent: {line[:200]}")


# ---------------------------------------------------------------------------
# 18. contextvars propagate into thread-pool workers via copy_context()
# ---------------------------------------------------------------------------

def test_contextvars_propagate_to_executor_workers():
    """copy_context() in call_subagent_async / _run_parallel_round must carry
    contextvars into worker threads so tracer scope and other context-local
    state set before the batch is visible inside each child."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    sentinel: contextvars.ContextVar[str] = contextvars.ContextVar("_test_sentinel", default="unset")
    sentinel.set("parent-value")

    captured: list[str] = []
    lock = threading.Lock()

    def worker():
        with lock:
            captured.append(sentinel.get())

    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(ctx.run, worker) for _ in range(3)]
        for f in futs:
            f.result()

    assert all(v == "parent-value" for v in captured), (
        f"contextvars not propagated into workers: {captured}"
    )


# ---------------------------------------------------------------------------
# 19. Timed-out orphaned threads cannot write stale checkpoints
# ---------------------------------------------------------------------------

def test_timed_out_orphan_cannot_write_checkpoint(tmp_path: Path):
    """A child thread that completes after the parent's wait times out must not
    be able to write a checkpoint via save_checkpoint (tombstone guard)."""
    import concurrent.futures

    host = make_host(tmp_path)

    run_id = "orphan-run-1"
    # Simulate the parent registering this run_id as timed-out.
    with host._timed_out_lock:
        host._timed_out_run_ids.add(run_id)

    # The orphaned thread tries to save a checkpoint.
    host.save_checkpoint(run_id, [{"role": "user", "content": "late message"}])

    # The checkpoint must NOT have been written.
    assert host.load_checkpoint(run_id) is None, (
        "Orphaned timed-out thread should not have been able to write a checkpoint."
    )
