import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent_framework.agent import (
    Agent,
    AgentBehavior,
    AgentEndHookDecision,
    AgentHookDecision,
    AgentParameter,
    AgentResult,
)
from agent_framework.agents.agent import CallbackRoutingPolicy
from agent_framework.config import load_host_config
from agent_framework.host import AgentHost
from agent_framework.llm_trace_logging import wire_llm_traces_to_runtime_tracer
from agent_framework.model import ModelContext, ModelResponse
from agent_framework.model import LlmUsage
from agent_framework.model_validation import ModelValidationContext
from agent_framework.tracing import CompositeRuntimeTracer, TraceEvent



class FakeModelDriver:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.on_request_trace = None
        self.on_response_trace = None

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        self.on_request_trace = on_request
        self.on_response_trace = on_response

    def decide(self, *, agent_id, provider_name, model_names, temperature, context: ModelContext):
        payload = self._payloads.pop(0)
        return ModelResponse(payload=payload, raw_text=str(payload))


class FakeUsageDriver(FakeModelDriver):
    def __init__(self, payloads, usages):
        super().__init__(payloads)
        self._usages = list(usages)

    def decide(self, *, agent_id, provider_name, model_names, temperature, context: ModelContext):
        response = super().decide(
            agent_id=agent_id,
            provider_name=provider_name,
            model_names=model_names,
            temperature=temperature,
            context=context,
        )
        response_usage = self._usages.pop(0)
        event = type(
            "RespTrace",
            (),
            {
                "agent_id": agent_id,
                "provider_name": provider_name,
                "model_name": model_names[0],
                "raw_text": response.raw_text,
                "parsed_payload": dict(response.payload),
                "usage": response_usage,
                "raw_usage": response_usage.to_dict(),
                "run_id": context.run_id,
            },
        )()
        if callable(self.on_response_trace):
            self.on_response_trace(event)
        return ModelResponse(
            payload=response.payload,
            raw_text=response.raw_text,
            usage=response_usage,
            raw_usage=response_usage.to_dict(),
        )


class _TraceRecorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def consume(self, event: TraceEvent) -> None:
        self.events.append(event)


def write_env(path: Path, root_agent: str = 'root') -> None:
    path.write_text(
        '\n'.join(
            [
                'OPENAI_API_KEY=test-key',
                'DEFAULT_PROVIDER=openai',
                'DEFAULT_MODEL=gpt-4o-mini',
                'AGENT_DIRECTORY=agents',
                'TOOLS_DIRECTORY=tools',
                'WORLD_DIRECTORY=world',
                f'ROOT_AGENT={root_agent}',
            ]
        ),
        encoding='utf-8',
    )


def test_load_host_config_reads_env(tmp_path: Path) -> None:
    env_path = tmp_path / '.env'
    write_env(env_path)
    config = load_host_config(env_path)
    assert config.openai_api_key == 'test-key'
    assert config.root_agent_id == 'root'
    assert config.agent_directory == (tmp_path / 'agents').resolve()


def test_agent_markdown_renders_declared_parameters(tmp_path: Path) -> None:
    agent_path = tmp_path / 'root.md'
    agent_path.write_text(
        """---
id: root
role: narrator
parameters:
  instruction:
    description: First instruction.
    required: true
---
You are the root narrator.
---
<agent_input><instruction>{{instruction}}</instruction></agent_input>
""",
        encoding='utf-8',
    )
    agent = Agent.from_markdown(agent_path, default_provider='openai', default_model=('gpt-4o-mini',))
    rendered = agent.render_user_prompt({'instruction': 'Explore the ruin.'})
    assert '<instruction>Explore the ruin.</instruction>' in rendered


def test_post_agent_hook_replace_is_default_and_append_is_opt_in() -> None:
    class ReplacingAppendingBehavior(AgentBehavior):
        def attach(self, agent) -> None:
            return None

        def after_run(self, agent, host, *, run, caller_id, result):
            return AgentEndHookDecision(
                continue_run=True,
                prompt_fragments=(
                    '<feedback>newest</feedback>',
                    '<round>2</round>',
                ),
                append_prompt_fragments=(
                    '<history>first</history>',
                ),
            )

    agent = Agent(
        agent_id='tester',
        role='tester',
        description='',
        system_prompt='sys',
        user_prompt_template='Hello',
        parameters=(),
        provider_name='openai',
        model_names=('gpt-4o-mini',),
        behaviors=(ReplacingAppendingBehavior(),),
    )
    run = agent._create_run({})
    run.prompt_fragments.extend(['<feedback>old</feedback>', '<round>1</round>', '<history>zero</history>'])
    result, continue_run = agent._run_post_agent_hooks(
        host=type('Host', (), {})(),
        run=run,
        caller_id=None,
        result=AgentResult(status='completed', message='ok', prompt=run.rendered_prompt),
    )
    assert continue_run is True
    assert result.message == 'ok'
    assert run.prompt_fragments == [
        '<feedback>newest</feedback>',
        '<round>2</round>',
        '<history>zero</history>',
        '<history>first</history>',
    ]


def test_agent_pre_hook_can_return_final_result(tmp_path: Path) -> None:
    env_path = tmp_path / '.env'
    write_env(env_path)
    agent = Agent(
        agent_id='hooked',
        role='tester',
        description='',
        system_prompt='sys',
        user_prompt_template='Hello {{name}}',
        parameters=(AgentParameter(name='name', description='name'),),
        provider_name='openai',
        model_names=('gpt-4o-mini',),
    )
    host = AgentHost(
        config=load_host_config(env_path),
        model_driver=FakeModelDriver([{'kind': 'final_message', 'message': 'done'}]),
    )

    def stop_agent_callback(event):
        return AgentHookDecision(
            final_result=AgentResult(status='completed', message='done|wrapped', prompt=event.invocation.rendered_prompt)
        )

    agent.on_pre_agent += stop_agent_callback
    result = agent.run(host=host, parameters={'name': 'Ada'}, caller_id='host')
    assert result.message == 'done|wrapped'


def test_host_runs_root_agent(tmp_path: Path) -> None:
    agents_dir = tmp_path / 'agents'
    tools_dir = tmp_path / 'tools'
    world_dir = tmp_path / 'world'
    agents_dir.mkdir()
    tools_dir.mkdir()
    world_dir.mkdir()
    (agents_dir / 'root.md').write_text(
        """---
id: root
role: narrator
parameters:
  instruction:
    description: First instruction.
    required: true
---
You are the root narrator.
---
<agent_input><instruction>{{instruction}}</instruction></agent_input>
""",
        encoding='utf-8',
    )
    env_path = tmp_path / '.env'
    write_env(env_path)
    host = AgentHost.from_env(
        env_path,
        model_driver=FakeModelDriver([{'kind': 'final_message', 'message': 'ok'}]),
        input_reader=lambda _: '',
        output_writer=lambda _: None,
    )
    result = host.run_root(initial_instruction='test instruction')
    assert result.message == 'ok'


def test_host_runtime_usage_totals_include_subagent_usage(tmp_path: Path) -> None:
    host, _ = _make_subagent_host(
        tmp_path,
        parent_id="orchestrator",
        child_id="parser",
        decisions=[
            {"kind": "call_subagent", "subagent_id": "parser", "parameters": {}},
            {"kind": "final_message", "message": "child done"},
            {"kind": "final_message", "message": "parent done"},
        ],
    )
    recorder = _TraceRecorder()
    usage_driver = FakeUsageDriver(
        payloads=[
            {"kind": "call_subagent", "subagent_id": "parser", "parameters": {}},
            {"kind": "final_message", "message": "child done"},
            {"kind": "final_message", "message": "parent done"},
        ],
        usages=[
            LlmUsage(input_tokens=10, input_cached_tokens=2, output_tokens=5, total_tokens=15),
            LlmUsage(input_tokens=7, input_cached_tokens=1, output_tokens=3, total_tokens=10),
            LlmUsage(input_tokens=4, input_cached_tokens=0, output_tokens=2, total_tokens=6),
        ],
    )
    host.model_driver = usage_driver
    host.runtime_tracer = CompositeRuntimeTracer(subscribers=[recorder])
    host._llm_traces_wired = False
    wire_llm_traces_to_runtime_tracer(host)
    host.run_root(initial_instruction="go")

    parent_run_id = next(run_id for run_id, reg in host._run_registry.items() if reg.agent_id == "orchestrator")
    child_run_id = next(run_id for run_id, reg in host._run_registry.items() if reg.agent_id == "parser")

    parent_usage = host.finish_runtime_usage(run_id=parent_run_id)
    child_usage = host.finish_runtime_usage(run_id=child_run_id)
    session_totals = host.session_usage_totals()

    assert child_usage["usage_self"]["total_tokens"] == 10
    assert child_usage["usage_inclusive"]["total_tokens"] == 10
    assert parent_usage["usage_self"]["total_tokens"] == 21
    assert parent_usage["usage_inclusive"]["total_tokens"] == 31
    assert session_totals["total_tokens"] == 31
    audit_finished = [e for e in recorder.events if e.kind == "runtime.audit.agent_call_finished"]
    assert len(audit_finished) == 2
    assert all(isinstance(e.payload.get("usage_self"), dict) for e in audit_finished)
    assert all(isinstance(e.payload.get("usage_inclusive"), dict) for e in audit_finished)
    child_audit = next(e for e in audit_finished if e.payload["run_id"] == child_run_id)
    parent_audit = next(e for e in audit_finished if e.payload["run_id"] == parent_run_id)
    assert child_audit.payload["usage_self"]["total_tokens"] == 10
    assert child_audit.payload["usage_inclusive"]["total_tokens"] == 10
    assert parent_audit.payload["usage_self"]["total_tokens"] == 21
    assert parent_audit.payload["usage_inclusive"]["total_tokens"] == 31


def test_programmatic_single_subagent_workflow_matches_native_trace_contract(tmp_path: Path) -> None:
    from agent_framework.agents.workflow import ProgrammaticWorkflow, WorkflowCallSubagentStep, WorkflowReturnStep

    class ProgrammaticBehavior(AgentBehavior):
        def __init__(self) -> None:
            self.last_run = None

        def attach(self, agent: Agent) -> None:
            return None

        def before_run(self, agent, host, *, run, caller_id):
            self.last_run = run
            workflow = ProgrammaticWorkflow(
                entry_step="delegate",
                steps={
                    "delegate": WorkflowCallSubagentStep(
                        step_id="delegate",
                        subagent_id="parser",
                        parameters={"topic": "go"},
                        next_step="finish",
                    ),
                    "finish": WorkflowReturnStep(
                        step_id="finish",
                        value=lambda state: AgentResult(
                            status="completed",
                            message=state.require_step_result("delegate").message,
                        ),
                    ),
                },
            )
            result = agent.execute_programmatic_workflow(
                host=host,
                run=run,
                caller_id=caller_id,
                workflow=workflow,
            )
            return AgentHookDecision(final_result=result)

    host, _ = _make_subagent_host(
        tmp_path,
        parent_id="orchestrator",
        child_id="parser",
        decisions=[{"kind": "final_message", "message": "child done"}],
    )
    recorder = _TraceRecorder()
    host.runtime_tracer = CompositeRuntimeTracer(subscribers=[recorder])
    parent = host.get_agent("orchestrator")
    behavior = ProgrammaticBehavior()
    parent.behaviors = (behavior,)

    result = host.run_root(initial_instruction="go")

    assert result.message == "child done"
    parent_run_id = next(run_id for run_id, reg in host._run_registry.items() if reg.agent_id == "orchestrator")
    named = [
        e.payload["event"]["type"]
        for e in recorder.events
        if e.kind == "runtime.audit.named_event" and e.context.run_id == parent_run_id
    ]
    assert "subagent_call" in named
    assert "subagent_result" in named
    assert behavior.last_run is not None
    assert "before_subagent:parser" in behavior.last_run.history
    assert "after_subagent:parser" in behavior.last_run.history
    assert any("<subagent_call id=\"parser\">" in item for item in behavior.last_run.transcript_entries)
    assert any("<subagent_result id=\"parser\">" in item for item in behavior.last_run.prompt_fragments)


def test_programmatic_parallel_workflow_matches_native_batch_trace_contract(tmp_path: Path) -> None:
    from agent_framework.agents import SubagentCallSpec
    from agent_framework.agents.workflow import ProgrammaticWorkflow, WorkflowCallSubagentsStep, WorkflowReturnStep

    class ProgrammaticBatchBehavior(AgentBehavior):
        def __init__(self) -> None:
            self.last_run = None

        def attach(self, agent: Agent) -> None:
            return None

        def before_run(self, agent, host, *, run, caller_id):
            self.last_run = run
            workflow = ProgrammaticWorkflow(
                entry_step="delegate",
                steps={
                    "delegate": WorkflowCallSubagentsStep(
                        step_id="delegate",
                        calls=(
                            SubagentCallSpec(subagent_id="parser", parameters={"topic": "a"}, output_key="first"),
                            SubagentCallSpec(subagent_id="parser", parameters={"topic": "b"}, output_key="second"),
                        ),
                        mode="parallel",
                        next_step="finish",
                    ),
                    "finish": WorkflowReturnStep(
                        step_id="finish",
                        value=lambda state: AgentResult(
                            status="completed",
                            message=str(len(state.require_step_result("delegate"))),
                        ),
                    ),
                },
            )
            result = agent.execute_programmatic_workflow(
                host=host,
                run=run,
                caller_id=caller_id,
                workflow=workflow,
            )
            return AgentHookDecision(final_result=result)

    host, _ = _make_subagent_host(
        tmp_path,
        parent_id="orchestrator",
        child_id="parser",
        decisions=[
            {"kind": "final_message", "message": "child one"},
            {"kind": "final_message", "message": "child two"},
        ],
    )
    recorder = _TraceRecorder()
    host.runtime_tracer = CompositeRuntimeTracer(subscribers=[recorder])
    parent = host.get_agent("orchestrator")
    behavior = ProgrammaticBatchBehavior()
    parent.behaviors = (behavior,)

    result = host.run_root(initial_instruction="go")

    assert result.message == "2"
    parent_run_id = next(run_id for run_id, reg in host._run_registry.items() if reg.agent_id == "orchestrator")
    named_events = [
        e.payload["event"]
        for e in recorder.events
        if e.kind == "runtime.audit.named_event" and e.context.run_id == parent_run_id
    ]
    assert any(evt["type"] == "subagent_batch_started" for evt in named_events)
    assert any(evt["type"] == "subagent_batch_finished" for evt in named_events)
    assert behavior.last_run is not None
    assert any("<subagent_results>" in item for item in behavior.last_run.transcript_entries)
    assert any(msg["content"].startswith("<subagent_results>") for msg in behavior.last_run.conversation_messages)


def test_agent_decision_preserves_callback_to_caller_kind() -> None:
    from agent_framework.agents.agent_decision import AgentDecision

    decision = AgentDecision.from_model_response(
        ModelResponse(
            payload={
                "kind": "callback_to_caller",
                "intent": "information_request",
                "message": "Need caller help",
                "parameters": {},
            },
            raw_text="",
        )
    )
    assert decision.kind == "callback_to_caller"
    assert decision.callback_intent == "information_request"


def test_agent_decision_preserves_request_user_input_kind() -> None:
    from agent_framework.agents.agent_decision import AgentDecision

    decision = AgentDecision.from_model_response(
        ModelResponse(
            payload={
                "kind": "request_user_input",
                "intent": "information_request",
                "message": "Ask the user",
                "parameters": {},
            },
            raw_text="",
        )
    )
    assert decision.kind == "request_user_input"
    assert decision.callback_intent == "information_request"


def test_agent_decision_alias_request_parameter_maps_to_request_user_input() -> None:
    from agent_framework.agents.agent_decision import AgentDecision

    decision = AgentDecision.from_model_response(
        ModelResponse(
            payload={
                "kind": "request_parameter",
                "message": "Need one field",
                "parameters": {},
            },
            raw_text="",
        )
    )
    assert decision.kind == "request_user_input"
    assert decision.callback_intent == "information_request"


def test_agent_decision_preserves_request_resolution_kind() -> None:
    from agent_framework.agents.agent_decision import AgentDecision

    decision = AgentDecision.from_model_response(
        ModelResponse(
            payload={
                "kind": "request_resolution",
                "intent": "information_request",
                "message": "Resolve internally",
                "parameters": {},
            },
            raw_text="",
        )
    )
    assert decision.kind == "request_resolution"
    assert decision.callback_intent == "information_request"


def test_request_user_input_bypasses_caller_resolution() -> None:
    from agent_framework.agents.agent_decision import AgentDecision

    class _Host:
        def __init__(self) -> None:
            self.resolve_calls = 0
            self.input_calls = 0

        def open_context(self, *, caller_id, callee_id, kind):
            class _Ctx:
                status = "open"
            return _Ctx()

        def resolve_callback(self, **kwargs):
            self.resolve_calls += 1
            return "caller-answer"

        def request_user_input(self, prompt, **kwargs):
            self.input_calls += 1
            return "user-answer"

    agent = Agent(
        agent_id="child",
        role="tester",
        description="",
        system_prompt="sys",
        user_prompt_template="Hello",
        parameters=(),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
    )
    host = _Host()
    run = agent._create_run({}, run_id="run-1", parent_run_id="root-run")
    decision = AgentDecision(
        kind="request_user_input",
        callback_intent="information_request",
        message="Ask the user directly",
    )

    result = agent.handle_callback(host=host, run=run, decision=decision, caller_id="parent")
    assert result is None
    assert host.resolve_calls == 0
    assert host.input_calls == 1


def test_callback_to_caller_passthrough_policy_redirects_to_host() -> None:
    from agent_framework.agents.agent_decision import AgentDecision

    class _Host:
        def __init__(self) -> None:
            self.resolve_calls = 0
            self.input_calls = 0

        def open_context(self, *, caller_id, callee_id, kind):
            class _Ctx:
                status = "open"
            return _Ctx()

        def resolve_callback(self, **kwargs):
            self.resolve_calls += 1
            return "caller-answer"

        def request_user_input(self, prompt, **kwargs):
            self.input_calls += 1
            return "user-answer"

    agent = Agent(
        agent_id="workflow_step",
        role="tester",
        description="",
        system_prompt="sys",
        user_prompt_template="Hello",
        parameters=(),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
        callback_routing_policy=CallbackRoutingPolicy(
            passthrough_child_callbacks=True,
            max_bubble_hops=None,
            fallback_target="user",
        ),
    )
    host = _Host()
    run = agent._create_run({}, run_id="run-1", parent_run_id="root-run")
    decision = AgentDecision(
        kind="callback_to_caller",
        callback_intent="information_request",
        message="Forward this upward",
        parameters={},
    )

    result = agent.handle_callback(host=host, run=run, decision=decision, caller_id="controller")
    assert result is None
    assert host.resolve_calls == 0
    assert host.input_calls == 1


def test_callback_to_caller_hop_limit_redirects_to_host() -> None:
    from agent_framework.agents.agent_decision import AgentDecision

    class _Host:
        def __init__(self) -> None:
            self.resolve_calls = 0
            self.input_calls = 0

        def open_context(self, *, caller_id, callee_id, kind):
            class _Ctx:
                status = "open"
            return _Ctx()

        def resolve_callback(self, **kwargs):
            self.resolve_calls += 1
            return "caller-answer"

        def request_user_input(self, prompt, **kwargs):
            self.input_calls += 1
            return "user-answer"

    agent = Agent(
        agent_id="controller",
        role="tester",
        description="",
        system_prompt="sys",
        user_prompt_template="Hello",
        parameters=(),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
    )
    host = _Host()
    run = agent._create_run({}, run_id="run-1", parent_run_id="root-run")
    decision = AgentDecision(
        kind="callback_to_caller",
        callback_intent="information_request",
        message="Hop-limited escalation",
        parameters={"bubble_hops": 1, "max_bubble_hops": 1, "fallback_target": "user"},
    )

    result = agent.handle_callback(host=host, run=run, decision=decision, caller_id="hosting_parent")
    assert result is None
    assert host.resolve_calls == 0
    assert host.input_calls == 1


def test_skill_definition_is_frozen(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition
    defn = SkillDefinition(
        name="my-skill",
        description="A test skill",
        version=None,
        priority=0,
        source_path=tmp_path / "SKILL.md",
        skill_dir=tmp_path,
    )
    try:
        defn.name = "other"  # type: ignore[misc]
        raise AssertionError("Expected frozen instance error")
    except (AttributeError, FrozenInstanceError):
        pass


def test_skill_content_holds_body_and_inventory(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillResource, SkillContent
    defn = SkillDefinition(
        name="my-skill", description="desc", version=None, priority=0,
        source_path=tmp_path / "SKILL.md", skill_dir=tmp_path,
    )
    resource = SkillResource(relative_path="references/guide.md", full_path=tmp_path / "references" / "guide.md")
    content = SkillContent(definition=defn, body="# Instructions", inventory=(resource,))
    assert content.body == "# Instructions"
    assert len(content.inventory) == 1
    assert content.inventory[0].relative_path == "references/guide.md"


def _write_skill(skill_dir: Path, name: str, description: str, priority: int = 0) -> None:
    """Helper: create a minimal SKILL.md in a skill subdirectory."""
    d = skill_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\npriority: {priority}\n---\n# Body\n",
        encoding="utf-8",
    )


def test_skill_registry_discovers_skills(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    _write_skill(tmp_path, "my-skill", "A test skill")
    registry = SkillRegistry(directories=(tmp_path,))
    registry.discover()
    defn = registry.get("my-skill")
    assert defn.name == "my-skill"
    assert defn.description == "A test skill"


def test_skill_registry_filter_empty_returns_all(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    _write_skill(tmp_path, "skill-a", "A")
    _write_skill(tmp_path, "skill-b", "B")
    registry = SkillRegistry(directories=(tmp_path,))
    registry.discover()
    result = registry.filter(())
    assert {d.name for d in result} == {"skill-a", "skill-b"}


def test_skill_registry_filter_restricted(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    _write_skill(tmp_path, "skill-a", "A")
    _write_skill(tmp_path, "skill-b", "B")
    registry = SkillRegistry(directories=(tmp_path,))
    registry.discover()
    result = registry.filter(("skill-a",))
    assert len(result) == 1
    assert result[0].name == "skill-a"


def test_skill_registry_deduplication_first_dir_wins(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    high = tmp_path / "high"
    low = tmp_path / "low"
    _write_skill(high, "shared", "from high priority")
    _write_skill(low, "shared", "from low priority")
    registry = SkillRegistry(directories=(high, low))  # high is index 0 = highest
    registry.discover()
    assert registry.get("shared").description == "from high priority"


def test_skill_registry_invalid_frontmatter_skipped(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    bad = tmp_path / "bad-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\n# missing name and description\n---\n# Body\n", encoding="utf-8")
    _write_skill(tmp_path, "good-skill", "Good")
    registry = SkillRegistry(directories=(tmp_path,))
    registry.discover()
    assert "good-skill" in [d.name for d in registry.get_all()]
    try:
        registry.get("bad-skill")
        raise AssertionError("Expected KeyError")
    except KeyError:
        pass


def test_skill_loader_reads_body(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillLoader
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: desc\n---\n# Instructions\nDo something useful.",
        encoding="utf-8",
    )
    defn = SkillDefinition(
        name="my-skill", description="desc", version=None, priority=0,
        source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve(),
    )
    content = SkillLoader().load(defn)
    assert "# Instructions" in content.body
    assert "Do something useful" in content.body
    assert "---" not in content.body  # frontmatter stripped


def test_skill_loader_builds_inventory_from_directory(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillLoader
    skill_dir = tmp_path / "my-skill"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "guide.md").write_text("# Guide", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: desc\n---\n# Body", encoding="utf-8"
    )
    defn = SkillDefinition(
        name="my-skill", description="desc", version=None, priority=0,
        source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve(),
    )
    content = SkillLoader().load(defn)
    paths = [r.relative_path for r in content.inventory]
    assert "references/guide.md" in paths


def test_skill_loader_detects_body_backtick_references(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillLoader
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "selling.md").write_text("# Selling guide", encoding="utf-8")
    body = "---\nname: my-skill\ndescription: desc\n---\nRead `selling.md` for details."
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    defn = SkillDefinition(
        name="my-skill", description="desc", version=None, priority=0,
        source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve(),
    )
    content = SkillLoader().load(defn)
    paths = [r.relative_path for r in content.inventory]
    assert "selling.md" in paths


def test_skill_loader_inventory_excludes_skill_md_itself(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillLoader
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: desc\n---\n# Body", encoding="utf-8"
    )
    defn = SkillDefinition(
        name="my-skill", description="desc", version=None, priority=0,
        source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve(),
    )
    content = SkillLoader().load(defn)
    paths = [r.relative_path for r in content.inventory]
    assert "SKILL.md" not in paths


def test_host_config_skills_directory_single(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"OPENAI_API_KEY=key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        f"AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        f"ROOT_AGENT=root\nSKILLS_DIRECTORY=skills\n",
        encoding="utf-8",
    )
    config = load_host_config(env_path)
    assert skills_dir.resolve() in config.skills_directories


def test_host_config_skills_directories_multi(tmp_path: Path) -> None:
    (tmp_path / "skills-a").mkdir()
    (tmp_path / "skills-b").mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"OPENAI_API_KEY=key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        f"AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        f"ROOT_AGENT=root\nSKILLS_DIRECTORIES=skills-a,skills-b\n",
        encoding="utf-8",
    )
    config = load_host_config(env_path)
    names = [p.name for p in config.skills_directories]
    assert "skills-a" in names
    assert "skills-b" in names
    assert names.index("skills-a") < names.index("skills-b")  # order preserved


def test_host_config_auto_detects_skills_dir(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    env_path = tmp_path / ".env"
    write_env(env_path)  # no SKILLS_DIRECTORY set
    config = load_host_config(env_path)
    assert any(p.name == "skills" for p in config.skills_directories)


def test_host_config_no_skills_dir_empty_tuple(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    write_env(env_path)  # no SKILLS_DIRECTORY, no skills/ dir
    config = load_host_config(env_path)
    assert config.skills_directories == ()


def test_agent_host_get_skill_registry_lazy_init(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "A test skill")
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"OPENAI_API_KEY=key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        f"AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        f"ROOT_AGENT=root\nSKILLS_DIRECTORY=skills\n",
        encoding="utf-8",
    )
    host = AgentHost.from_env(env_path, model_driver=FakeModelDriver([]),
                               input_reader=lambda _: "", output_writer=lambda _: None)
    assert host.skill_registry is None  # not initialized yet
    registry = host.get_skill_registry()
    assert isinstance(registry, SkillRegistry)
    assert host.skill_registry is registry  # cached


def test_agent_host_get_skill_registry_returns_same_instance(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    write_env(env_path)
    host = AgentHost.from_env(env_path, model_driver=FakeModelDriver([]),
                               input_reader=lambda _: "", output_writer=lambda _: None)
    r1 = host.get_skill_registry()
    r2 = host.get_skill_registry()
    assert r1 is r2


def test_skill_start_event_fields(tmp_path: Path) -> None:
    from agent_framework.agents.skill_start_event import SkillStartEvent
    from agent_framework.agents.agent_invocation import AgentInvocation
    invocation = AgentInvocation(agent_id="a", run_id="r", rendered_prompt="p", caller_id=None, parameters={})
    event = SkillStartEvent(invocation=invocation, skill_name="my-skill", parameters={})
    assert event.skill_name == "my-skill"
    assert event.parameters == {}


def test_skill_end_event_fields(tmp_path: Path) -> None:
    from agent_framework.agents.skill_end_event import SkillEndEvent
    from agent_framework.agents.agent_invocation import AgentInvocation
    from agent_framework.skill import SkillDefinition, SkillContent
    invocation = AgentInvocation(agent_id="a", run_id="r", rendered_prompt="p", caller_id=None, parameters={})
    defn = SkillDefinition(name="s", description="d", version=None, priority=0,
                           source_path=tmp_path / "SKILL.md", skill_dir=tmp_path)
    content = SkillContent(definition=defn, body="body", inventory=())
    event = SkillEndEvent(invocation=invocation, skill_name="s", parameters={}, content=content)
    assert event.content.body == "body"


def test_parse_json_object_model_output_valid() -> None:
    from agent_framework.model import parse_json_object_model_output

    payload, norm = parse_json_object_model_output(
        '{"kind": "final_message", "message": "ok"}', provider_label="Test"
    )
    assert payload == {"kind": "final_message", "message": "ok"}
    assert '"kind"' in norm


def test_parse_json_object_model_output_rejects_invalid_json() -> None:
    from agent_framework.errors import ModelDriverError
    from agent_framework.model import parse_json_object_model_output

    with pytest.raises(ModelDriverError, match="not valid JSON"):
        parse_json_object_model_output("not json {", provider_label="Test")


def test_parse_json_object_model_output_rejects_non_object() -> None:
    from agent_framework.errors import ModelDriverError
    from agent_framework.model import parse_json_object_model_output

    with pytest.raises(ModelDriverError, match="JSON object"):
        parse_json_object_model_output("[1,2]", provider_label="Test")


def test_host_rewrites_multiple_json_documents_error(tmp_path: Path) -> None:
    from agent_framework.errors import ModelDriverError

    env_path = tmp_path / ".env"
    write_env(env_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "root.md").write_text(
        """---
id: root
role: tester
---
You are a tester.
---
Say hi
""",
        encoding="utf-8",
    )

    class InvalidJsonDriver:
        def decide(self, *, agent_id, provider_name, model_names, temperature, context):
            raise ModelDriverError(
                "DIAL structured response is not valid JSON: Extra data: line 45 column 1 (char 7447).",
                upstream_body='{"kind":"call_subagents"}{"kind":"call_subagent"}',
            )

    host = AgentHost.from_env(env_path, model_driver=InvalidJsonDriver())

    with pytest.raises(ModelDriverError, match="more than one JSON value") as exc_info:
        host.run_agent("root", initial_instruction="hello")

    assert "exactly one top-level JSON object per turn" in str(exc_info.value)


def test_host_accepts_runtime_model_response_validator(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    write_env(env_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "root.md").write_text(
        """---
id: root
role: tester
---
You are a tester.
---
Say hi
""",
        encoding="utf-8",
    )

    class CustomResponseValidator:
        def validate_response(self, response: ModelResponse, *, context: ModelValidationContext) -> None:
            del response, context
            raise ValueError("custom runtime validator blocked this response")

    host = AgentHost.from_env(
        env_path,
        model_driver=FakeModelDriver([{"kind": "final_message", "message": "ok"}]),
    )
    host.register_model_response_validator(CustomResponseValidator())

    with pytest.raises(ValueError, match="custom runtime validator blocked this response"):
        host.run_agent("root", initial_instruction="hello")


def test_agent_decision_extracts_skill_name() -> None:
    from agent_framework.agents.agent_decision import AgentDecision
    from agent_framework.model import ModelResponse
    response = ModelResponse(
        payload={"kind": "invoke_skill", "skill_name": "my-skill"},
        raw_text='{"kind": "invoke_skill", "skill_name": "my-skill"}',
    )
    decision = AgentDecision.from_model_response(response)
    assert decision.kind == "invoke_skill"
    assert decision.skill_name == "my-skill"


def test_agent_decision_skill_name_defaults_to_none() -> None:
    from agent_framework.agents.agent_decision import AgentDecision
    from agent_framework.model import ModelResponse
    response = ModelResponse(
        payload={"kind": "final_message", "message": "done"},
        raw_text="done",
    )
    decision = AgentDecision.from_model_response(response)
    assert decision.skill_name is None


def test_agent_decision_rejects_missing_kind() -> None:
    from agent_framework.agents.agent_decision import AgentDecision
    from agent_framework.model import ModelResponse

    response = ModelResponse(payload={"message": "no kind"}, raw_text="{}")
    with pytest.raises(ValueError, match="missing top-level"):
        AgentDecision.from_model_response(response)


def test_agent_decision_rejects_both_subagent_and_tool() -> None:
    from agent_framework.agents.agent_decision import AgentDecision
    from agent_framework.model import ModelResponse

    response = ModelResponse(
        payload={
            "kind": "call_tool",
            "tool_name": "Read",
            "subagent_id": "helper",
            "message": "",
        },
        raw_text="{}",
    )
    with pytest.raises(ValueError, match="both subagent_id and tool_name"):
        AgentDecision.from_model_response(response)


def test_agent_decision_rejects_unknown_kind() -> None:
    from agent_framework.agents.agent_decision import AgentDecision
    from agent_framework.model import ModelResponse

    response = ModelResponse(
        payload={"kind": "gather_context", "message": "x"},
        raw_text="{}",
    )
    with pytest.raises(ValueError, match="gather_context"):
        AgentDecision.from_model_response(response)


def test_agent_decision_rejects_empty_kind() -> None:
    from agent_framework.agents.agent_decision import AgentDecision
    from agent_framework.model import ModelResponse

    response = ModelResponse(payload={"kind": "", "message": "x"}, raw_text="{}")
    with pytest.raises(ValueError, match="unsupported"):
        AgentDecision.from_model_response(response)


def test_build_skills_catalog_returns_catalog_text() -> None:
    """build_skills_catalog returns formatted text when skills are provided."""
    from agent_framework.model import build_skills_catalog, CapabilityDefinition
    skills = (CapabilityDefinition(capability_id="my-skill", description="Does something"),)
    result = build_skills_catalog(skills)
    assert "<available_skills>" in result
    assert "my-skill" in result


def test_build_skills_catalog_returns_empty_for_no_skills() -> None:
    """build_skills_catalog returns empty string when no skills."""
    from agent_framework.model import build_skills_catalog
    result = build_skills_catalog(())
    assert result == ""


def test_skills_catalog_not_in_system_prompt(tmp_path: Path) -> None:
    """System prompt must NOT contain skills catalog (the JSON skill list)."""
    from agent_framework.model import assemble_system_prompt, CapabilityDefinition, ModelContext
    skills = (CapabilityDefinition(capability_id="my-skill", description="Does something"),)
    context = ModelContext(
        system_prompt="base",
        user_prompt="hello",
        messages=(),
        response_mode="decision",
        tools=(),
        subagents=(),
        skills=skills,
        run_id="r1",
    )
    prompt = assemble_system_prompt(context)
    # The skills catalog JSON (with skill id) must NOT appear in the system prompt
    assert '"name": "my-skill"' not in prompt


def test_skills_catalog_injected_as_conversation_message(tmp_path: Path) -> None:
    """Skills catalog appears as user message at messages[2]."""
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "Does useful things")

    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        "AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        f"ROOT_AGENT=root\nSKILLS_DIRECTORY={skills_dir.name}\n",
        encoding="utf-8",
    )
    host = AgentHost.from_env(env_path, model_driver=FakeModelDriver([]),
                               input_reader=lambda _: "", output_writer=lambda _: None)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_names=("gpt-4o-mini",),
        allowed_skills=("my-skill",),
    )
    run = agent._create_run({})
    context = agent.build_context(host=host, run=run)
    assert len(context.messages) >= 3
    catalog_message = context.messages[2]
    assert catalog_message["role"] == "user"
    assert "<available_skills>" in catalog_message["content"]
    assert "my-skill" in catalog_message["content"]


def test_build_context_merges_runtime_into_system_message(tmp_path: Path) -> None:
    """First system message includes shared runtime instructions (json_object mode)."""
    env_path = tmp_path / ".env"
    write_env(env_path)
    host = AgentHost.from_env(env_path, model_driver=FakeModelDriver([]),
                               input_reader=lambda _: "", output_writer=lambda _: None)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_names=("gpt-4o-mini",),
    )
    run = agent._create_run({})
    context = agent.build_context(host=host, run=run)
    assert "Structured Action Format" in context.system_prompt
    assert context.messages[0]["role"] == "system"
    assert "Structured Action Format" in context.messages[0]["content"]


def test_no_skills_catalog_message_when_no_skills(tmp_path: Path) -> None:
    """Without skills, messages has only system + user (2 entries)."""
    env_path = tmp_path / ".env"
    write_env(env_path)
    host = AgentHost.from_env(env_path, model_driver=FakeModelDriver([]),
                               input_reader=lambda _: "", output_writer=lambda _: None)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_names=("gpt-4o-mini",),
    )
    run = agent._create_run({})
    context = agent.build_context(host=host, run=run)
    assert len(context.messages) == 2
    assert context.messages[0]["role"] == "system"
    assert context.messages[1]["role"] == "user"


def test_capability_metadata_does_not_include_skills_section() -> None:
    """_capability_metadata no longer returns skills_section key."""
    from agent_framework.drivers import OpenAiModelDriver
    from agent_framework.model import CapabilityDefinition
    skills = (CapabilityDefinition(capability_id="my-skill", description="Does things"),)
    metadata = OpenAiModelDriver._capability_metadata(tools=(), subagents=(), skills=skills)
    assert "skills_section" not in metadata


def test_capability_metadata_has_no_skills_section_key_when_empty() -> None:
    """_capability_metadata no longer returns skills_section key even when no skills."""
    from agent_framework.drivers import OpenAiModelDriver
    metadata = OpenAiModelDriver._capability_metadata(tools=(), subagents=(), skills=())
    assert "skills_section" not in metadata


def test_agent_build_context_populates_skills_from_registry(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "Does useful things")

    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        "AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        "ROOT_AGENT=root\nSKILLS_DIRECTORY=skills\n",
        encoding="utf-8",
    )
    host = AgentHost.from_env(env_path, model_driver=FakeModelDriver([]),
                               input_reader=lambda _: "", output_writer=lambda _: None)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_names=("gpt-4o-mini",),
        allowed_skills=(),  # empty = all skills
    )
    run = agent._create_run({})
    context = agent.build_context(host=host, run=run)
    skill_ids = [s.capability_id for s in context.skills]
    assert "my-skill" in skill_ids


def test_agent_has_pre_and_post_skill_hooks() -> None:
    from agent_framework.agents.sequential_hook import SequentialHook
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_names=("gpt-4o-mini",),
    )
    assert isinstance(agent.on_pre_skill, SequentialHook)
    assert isinstance(agent.on_post_skill, SequentialHook)


def _write_env_with_skills(env_path: Path, skills_dir: Path) -> None:
    env_path.write_text(
        "OPENAI_API_KEY=test-key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        f"AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        f"ROOT_AGENT=root\nSKILLS_DIRECTORY={skills_dir.name}\n",
        encoding="utf-8",
    )


def test_agent_invokes_skill_and_injects_content_into_conversation(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "Does useful things")
    (skills_dir / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Does useful things\n---\n# Do this thing\nFollow these steps.",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    _write_env_with_skills(env_path, skills_dir)

    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_names=("gpt-4o-mini",),
        allowed_skills=(),
    )
    host = AgentHost.from_env(
        env_path,
        model_driver=FakeModelDriver([
            {"kind": "invoke_skill", "skill_name": "my-skill"},
            {"kind": "final_message", "message": "done"},
        ]),
        input_reader=lambda _: "",
        output_writer=lambda _: None,
    )
    result = agent.run(host=host, parameters={}, caller_id="host")
    assert result.status == "completed"
    assert result.message == "done"


def test_agent_unknown_skill_feeds_error_back_and_continues(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    write_env(env_path)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_names=("gpt-4o-mini",),
    )
    host = AgentHost.from_env(
        env_path,
        model_driver=FakeModelDriver([
            {"kind": "invoke_skill", "skill_name": "nonexistent-skill"},
            {"kind": "final_message", "message": "recovered"},
        ]),
        input_reader=lambda _: "",
        output_writer=lambda _: None,
    )
    result = agent.run(host=host, parameters={}, caller_id="host")
    assert result.message == "recovered"


def test_skill_hooks_fire_on_invocation(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "A skill")
    env_path = tmp_path / ".env"
    _write_env_with_skills(env_path, skills_dir)

    fired = []
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_names=("gpt-4o-mini",),
        allowed_skills=(),
    )
    agent.on_pre_skill += lambda event: fired.append(("pre", event.skill_name))
    agent.on_post_skill += lambda event: fired.append(("post", event.skill_name))

    host = AgentHost.from_env(
        env_path,
        model_driver=FakeModelDriver([
            {"kind": "invoke_skill", "skill_name": "my-skill"},
            {"kind": "final_message", "message": "done"},
        ]),
        input_reader=lambda _: "",
        output_writer=lambda _: None,
    )
    agent.run(host=host, parameters={}, caller_id="host")
    assert ("pre", "my-skill") in fired
    assert ("post", "my-skill") in fired


def test_audit_tracer_records_skill_invocation(tmp_path: Path) -> None:
    from agent_framework.audit_trace import InMemoryAuditTracer
    tracer = InMemoryAuditTracer(output_dir=tmp_path)
    tracer.start_agent_call(
        run_id="r1", caller_id=None, agent_name="tester",
        system_prompt="sys", system_prompt_sources=(),
        user_prompt="hello", user_prompt_sources=(),
    )
    tracer.record_skill_invocation(
        run_id="r1",
        skill_name="my-skill",
        parameters={"key": "val"},
        inventory=["references/guide.md"],
    )
    record = tracer.active_records["r1"]
    assert len(record.skill_invocations) == 1
    assert record.skill_invocations[0].skill_name == "my-skill"
    assert "references/guide.md" in record.skill_invocations[0].inventory


def test_skill_invocation_record_serializes(tmp_path: Path) -> None:
    from agent_framework.audit_trace import InMemoryAuditTracer
    tracer = InMemoryAuditTracer(output_dir=tmp_path)
    tracer.start_agent_call(
        run_id="r1", caller_id=None, agent_name="tester",
        system_prompt="sys", system_prompt_sources=(),
        user_prompt="hello", user_prompt_sources=(),
    )
    tracer.record_skill_invocation(run_id="r1", skill_name="s", parameters={}, inventory=[])
    tracer.finish_agent_call(run_id="r1")
    import json
    jsonl_files = list(tmp_path.glob("*.jsonl"))
    assert jsonl_files, "Expected JSONL output file"
    line = jsonl_files[0].read_text(encoding="utf-8").strip()
    data = json.loads(line)
    assert "skill_invocations" in data
    assert data["skill_invocations"][0]["skill_name"] == "s"


def test_skill_events_importable_from_top_level_agent_module() -> None:
    from agent_framework.agent import SkillStartEvent as AgentSkillStartEvent, SkillEndEvent as AgentSkillEndEvent
    from agent_framework.agents import SkillStartEvent as PackageSkillStartEvent, SkillEndEvent as PackageSkillEndEvent

    assert AgentSkillStartEvent is PackageSkillStartEvent
    assert AgentSkillEndEvent is PackageSkillEndEvent


# ---------------------------------------------------------------------------
# Task 2: token budget cap for skills catalog
# ---------------------------------------------------------------------------

def test_host_config_skills_catalog_max_tokens_from_env(tmp_path: Path) -> None:
    """SKILLS_CATALOG_MAX_TOKENS env var is read into config."""
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=test\nSKILLS_CATALOG_MAX_TOKENS=500\n")
    config = load_host_config(env)
    assert config.skills_catalog_max_tokens == 500


def test_host_config_skills_catalog_max_tokens_default(tmp_path: Path) -> None:
    """Default skills_catalog_max_tokens is 2000."""
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=test\n")
    config = load_host_config(env)
    assert config.skills_catalog_max_tokens == 2000


def test_build_skills_catalog_truncates_over_budget() -> None:
    """Skills exceeding budget are dropped (lowest-priority first)."""
    from agent_framework.model import build_skills_catalog, CapabilityDefinition
    # Build many skills with long descriptions that exceed a tight budget
    skills = tuple(
        CapabilityDefinition(
            capability_id=f"skill-{i}",
            description="x" * 300,
            priority=i,
        )
        for i in range(10)
    )
    max_tokens = 50
    result = build_skills_catalog(skills, max_tokens=max_tokens)
    # With max_tokens=50, not all 10 skills should appear
    assert result.count('"name"') < 10
    # Verify either within budget or we hit the at-least-1 minimum
    skill_count = result.count('"name"')
    assert len(result) // 4 <= max_tokens or skill_count == 1


def test_build_skills_catalog_keeps_highest_priority_first() -> None:
    """Skills with higher priority survive truncation."""
    from agent_framework.model import build_skills_catalog, CapabilityDefinition
    skills = (
        CapabilityDefinition(capability_id="low-skill",  description="y" * 200, priority=1),
        CapabilityDefinition(capability_id="mid-skill",  description="y" * 200, priority=5),
        CapabilityDefinition(capability_id="high-skill", description="y" * 200, priority=10),
    )
    # Budget so tight that even one skill exceeds it — verifies the at-least-1 guarantee keeps the highest-priority skill.
    result = build_skills_catalog(skills, max_tokens=80)
    assert "high-skill" in result
    assert "mid-skill" not in result
    assert "low-skill" not in result


def test_build_skills_catalog_passes_max_tokens_from_host_config(tmp_path: Path) -> None:
    """build_context() reads max_tokens from host.config.skills_catalog_max_tokens.

    With 3 skills and a tight budget that only fits 1, only the highest-priority
    skill should appear in the catalog message.
    """
    from agent_framework.agents.agent import Agent

    skills_dir = tmp_path / "skills"
    # Write 3 skills: each description is ~200 chars (~50 tokens each)
    for name, priority in [("low-skill", 1), ("mid-skill", 5), ("high-skill", 10)]:
        d = skills_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {'A' * 200}\npriority: {priority}\n---\n# Body\n",
            encoding="utf-8",
        )

    env_path = tmp_path / ".env"
    # Budget of 80 tokens: fits roughly 1 skill (each skill ~50 tokens for description alone)
    env_path.write_text(
        "OPENAI_API_KEY=key\nDEFAULT_PROVIDER=openai\nDEFAULT_MODEL=gpt-4o-mini\n"
        "AGENT_DIRECTORY=agents\nTOOLS_DIRECTORY=tools\nWORLD_DIRECTORY=world\n"
        f"ROOT_AGENT=root\nSKILLS_DIRECTORY={skills_dir.name}\n"
        "SKILLS_CATALOG_MAX_TOKENS=80\n",
        encoding="utf-8",
    )
    host = AgentHost.from_env(env_path, model_driver=FakeModelDriver([]),
                               input_reader=lambda _: "", output_writer=lambda _: None)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_names=("gpt-4o-mini",),
        allowed_skills=("low-skill", "mid-skill", "high-skill"),
    )
    run = agent._create_run({})
    context = agent.build_context(host=host, run=run)
    # The catalog message should exist and contain only the high-priority skill
    catalog_messages = [
        m
        for m in context.messages
        if "## Skills" in m.get("content", "") and "<available_skills>" in m.get("content", "")
    ]
    assert len(catalog_messages) == 1
    catalog_content = catalog_messages[0]["content"]
    assert "high-skill" in catalog_content
    assert "low-skill" not in catalog_content


# ---------------------------------------------------------------------------
# Task 3: Replace ReadSkillResourceTool with base directory injection
# ---------------------------------------------------------------------------

def test_skill_fragment_includes_base_directory(tmp_path: Path) -> None:
    """Skill invocation injects 'Base directory:' path in the conversation message."""
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "Does useful things")
    (skills_dir / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Does useful things\n---\n# Do this thing\nFollow these steps.",
        encoding="utf-8",
    )
    # Add a resource file so the inventory is non-empty and <skill_files> is injected
    (skills_dir / "my-skill" / "reference.md").write_text(
        "# Reference\nExtra context for the skill.",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    _write_env_with_skills(env_path, skills_dir)

    skill_dir_path = str((skills_dir / "my-skill").resolve())
    conversation_snapshot: list[list[dict]] = []

    class CapturingModelDriver:
        def __init__(self, payloads):
            self._payloads = list(payloads)
            self.on_request_trace = None
            self.on_response_trace = None

        def set_trace_callbacks(self, *, on_request=None, on_response=None):
            self.on_request_trace = on_request
            self.on_response_trace = on_response

        def decide(self, *, agent_id, provider_name, model_names, temperature, context):
            conversation_snapshot.append(list(context.messages))
            payload = self._payloads.pop(0)
            return ModelResponse(payload=payload, raw_text=str(payload))

    host = AgentHost.from_env(
        env_path,
        model_driver=CapturingModelDriver([
            {"kind": "invoke_skill", "skill_name": "my-skill"},
            {"kind": "final_message", "message": "done"},
        ]),
        input_reader=lambda _: "",
        output_writer=lambda _: None,
    )
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_names=("gpt-4o-mini",),
        allowed_skills=(),
    )
    agent.run(host=host, parameters={}, caller_id="host")

    # The second model call should include the skill fragment with Base directory:
    assert len(conversation_snapshot) == 2, "Expected 2 model calls"
    second_call_messages = conversation_snapshot[1]
    skill_messages = [m for m in second_call_messages if "skill_invocation_result" in m.get("content", "")]
    assert skill_messages, "Expected a skill_invocation_result message in the second model call"
    fragment = skill_messages[0]["content"]
    assert "Base directory:" in fragment, f"Expected 'Base directory:' in fragment, got:\n{fragment}"
    assert skill_dir_path in fragment or str(skills_dir / "my-skill") in fragment, \
        f"Expected skill directory path in fragment, got:\n{fragment}"
    assert "read_skill_resource" not in fragment, \
        f"Expected no 'read_skill_resource' in fragment, got:\n{fragment}"
    assert "<skill_file_inventory>" not in fragment, \
        f"Expected no '<skill_file_inventory>' in fragment, got:\n{fragment}"
    assert "<skill_files>" in fragment, \
        f"Expected '<skill_files>' in fragment, got:\n{fragment}"


def test_no_skill_tool_registered_on_invocation(tmp_path: Path) -> None:
    """No read_skill_resource tool registered after skill invocation."""
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "A skill")
    env_path = tmp_path / ".env"
    _write_env_with_skills(env_path, skills_dir)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_names=("gpt-4o-mini",),
        allowed_skills=(),
    )
    host = AgentHost.from_env(
        env_path,
        model_driver=FakeModelDriver([
            {"kind": "invoke_skill", "skill_name": "my-skill"},
            {"kind": "final_message", "message": "done"},
        ]),
        input_reader=lambda _: "",
        output_writer=lambda _: None,
    )
    agent.run(host=host, parameters={}, caller_id="host")
    assert "read_skill_resource" not in host.tool_registry.list_names()


# ---------------------------------------------------------------------------
# AgentResult.parameters propagation
# ---------------------------------------------------------------------------

def test_agent_result_carries_parameters_from_final_message_decision(tmp_path: Path) -> None:
    """handle_final_message must propagate decision.parameters into AgentResult.parameters."""
    env_path = tmp_path / ".env"
    write_env(env_path)
    agent = Agent(
        agent_id="parser",
        role="parser",
        description="",
        system_prompt="sys",
        user_prompt_template="parse",
        parameters=(),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
    )
    host = AgentHost(
        config=load_host_config(env_path),
        model_driver=FakeModelDriver([
            {
                "kind": "final_message",
                "message": "parsed 2 intents",
                "parameters": {"intents": ["move", "attack"], "confidence": 0.9},
            }
        ]),
    )
    result = agent.run(host=host, parameters={}, caller_id="host")
    assert result.message == "parsed 2 intents"
    assert result.parameters == {"intents": ["move", "attack"], "confidence": 0.9}


def test_agent_result_parameters_none_when_decision_has_no_parameters(tmp_path: Path) -> None:
    """AgentResult.parameters stays None when decision carries no parameters."""
    env_path = tmp_path / ".env"
    write_env(env_path)
    agent = Agent(
        agent_id="simple",
        role="simple",
        description="",
        system_prompt="sys",
        user_prompt_template="go",
        parameters=(),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
    )
    host = AgentHost(
        config=load_host_config(env_path),
        model_driver=FakeModelDriver([{"kind": "final_message", "message": "done"}]),
    )
    result = agent.run(host=host, parameters={}, caller_id="host")
    assert result.parameters is None


def test_subagent_result_payload_merges_message_and_parameters() -> None:
    """_subagent_result_payload produces a JSON envelope when parameters are present."""
    from agent_framework.agents.agent import _subagent_result_payload

    payload = _subagent_result_payload("I captured 3 intents", {"intents": ["a", "b", "c"]})
    parsed = json.loads(payload)
    assert parsed["message"] == "I captured 3 intents"
    assert parsed["intents"] == ["a", "b", "c"]


def test_subagent_result_payload_plain_text_when_no_parameters() -> None:
    """_subagent_result_payload returns plain message when no parameters."""
    from agent_framework.agents.agent import _subagent_result_payload

    assert _subagent_result_payload("simple reply", None) == "simple reply"
    assert _subagent_result_payload("simple reply", {}) == "simple reply"


def test_subagent_result_payload_append_mode() -> None:
    """append mode: message kept verbatim, parameters appended as fenced JSON block."""
    from agent_framework.agents.agent import _subagent_result_payload

    result = _subagent_result_payload("captured 3 intents", {"intents": ["a"]}, "append")
    assert result.startswith("captured 3 intents\n```\n")
    assert result.endswith("\n```")
    fenced = result.split("```\n", 1)[1].rstrip("\n`")
    assert json.loads(fenced) == {"intents": ["a"]}


def test_subagent_result_payload_ignore_mode() -> None:
    """ignore mode: message returned unchanged, parameters discarded."""
    from agent_framework.agents.agent import _subagent_result_payload

    assert _subagent_result_payload("summary only", {"secret": 42}, "ignore") == "summary only"


def test_agent_loads_parameters_injection_from_frontmatter(tmp_path: Path) -> None:
    """parameters_injection frontmatter field is parsed and stored on Agent."""
    agent_path = tmp_path / "parser.md"
    agent_path.write_text(
        "---\nid: parser\nrole: parser\ndescription: p\nparameters_injection: append\n---\nsys\n---\ngo\n",
        encoding="utf-8",
    )
    agent = Agent.from_markdown(agent_path, default_provider="openai", default_model=("gpt-4o-mini",))
    assert agent.parameters_injection == "append"


def test_agent_parameters_injection_defaults_to_override(tmp_path: Path) -> None:
    """parameters_injection defaults to 'override' when absent from frontmatter."""
    agent_path = tmp_path / "simple.md"
    agent_path.write_text(
        "---\nid: simple\nrole: r\ndescription: d\n---\nsys\n---\ngo\n",
        encoding="utf-8",
    )
    agent = Agent.from_markdown(agent_path, default_provider="openai", default_model=("gpt-4o-mini",))
    assert agent.parameters_injection == "override"


def test_agent_invalid_parameters_injection_raises(tmp_path: Path) -> None:
    """An unrecognised parameters_injection value raises AgentMarkdownError."""
    from agent_framework.agents.helpers import AgentMarkdownError

    agent_path = tmp_path / "bad.md"
    agent_path.write_text(
        "---\nid: bad\nrole: r\ndescription: d\nparameters_injection: merge\n---\nsys\n---\ngo\n",
        encoding="utf-8",
    )
    with pytest.raises(AgentMarkdownError, match="parameters_injection"):
        Agent.from_markdown(agent_path, default_provider="openai", default_model=("gpt-4o-mini",))


def test_call_subagent_append_mode_keeps_message_and_appends_params(tmp_path: Path) -> None:
    """When child uses append mode, parent sees prose summary + fenced JSON block."""
    host, driver = _make_subagent_host(
        tmp_path,
        parent_id="orchestrator",
        child_id="parser",
        decisions=[
            {"kind": "call_subagent", "subagent_id": "parser", "parameters": {}},
            {
                "kind": "final_message",
                "message": "captured 2 intents",
                "parameters": {"intents": ["move", "attack"]},
            },
            {"kind": "final_message", "message": "done"},
        ],
    )
    # Override the parser agent's parameters_injection after loading
    parser = host.agent_registry.get("parser")
    object.__setattr__(parser, "parameters_injection", "append")

    host.run_root(initial_instruction="go")

    orchestrator_contexts = [c for c in driver.contexts if len(list(c.messages)) > 2]
    last_user_msg = next(
        (m["content"] for m in reversed(list(orchestrator_contexts[0].messages))
         if "parser" in m.get("content", "")),
        None,
    )
    assert last_user_msg is not None
    content = last_user_msg.split("Subagent result parser: ", 1)[1]
    assert content.startswith("captured 2 intents\n```\n")
    params = json.loads(content.split("```\n", 1)[1].rstrip("\n`"))
    assert params == {"intents": ["move", "attack"]}


def test_call_subagent_ignore_mode_drops_parameters(tmp_path: Path) -> None:
    """When child uses ignore mode, parent sees only the prose message."""
    host, driver = _make_subagent_host(
        tmp_path,
        parent_id="orchestrator",
        child_id="parser",
        decisions=[
            {"kind": "call_subagent", "subagent_id": "parser", "parameters": {}},
            {
                "kind": "final_message",
                "message": "summary only",
                "parameters": {"hidden": True},
            },
            {"kind": "final_message", "message": "done"},
        ],
    )
    parser = host.agent_registry.get("parser")
    object.__setattr__(parser, "parameters_injection", "ignore")

    host.run_root(initial_instruction="go")

    orchestrator_contexts = [c for c in driver.contexts if len(list(c.messages)) > 2]
    last_user_msg = next(
        (m["content"] for m in reversed(list(orchestrator_contexts[0].messages))
         if "parser" in m.get("content", "")),
        None,
    )
    assert last_user_msg is not None
    content = last_user_msg.split("Subagent result parser: ", 1)[1]
    assert content == "summary only"


def test_subagent_call_exception_is_reported_without_secondary_type_error() -> None:
    from agent_framework.agents.agent_decision import AgentDecision

    class RaisingHost:
        config = type("Config", (), {"skills_catalog_max_tokens": 2000, "memory_builtin_tools_enabled": True})()

        def resolve_model_tool_definitions(self, tool_names, *, agent_id=None, run_id=None):
            return ()

        def get_agent(self, agent_id, *, base_dir=None):
            raise AssertionError("get_agent should not be used in this test")

        def get_tool(self, name):
            raise AssertionError("get_tool should not be used in this test")

        def call_subagent(self, *, caller, callee_id, parameters, parent_run_id=None, **kwargs):
            raise RuntimeError("boom")

    agent = Agent(
        agent_id="orchestrator",
        role="orchestrator",
        description="",
        system_prompt="sys",
        user_prompt_template="go",
        parameters=(),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
        allowed_child_agents=("child",),
    )
    host = RaisingHost()
    run = agent._create_run({})

    decision = AgentDecision.from_model_response(
        ModelResponse(
            payload={
                "kind": "call_subagent",
                "subagent_id": "child",
                "parameters": {"topic": "x"},
            },
            raw_text="",
        )
    )
    outcome = agent.handle_subagent_call(host=host, run=run, decision=decision, caller_id="host")

    assert outcome is None
    assert any(
        item["content"] == "Subagent error child: RuntimeError: boom"
        for item in run.conversation_messages
    )
    assert any(
        "<subagent_error id=\"child\">RuntimeError: boom</subagent_error>" in item
        for item in run.prompt_fragments
    )


def test_call_subagents_with_memory_normalization_builds_specs_without_name_error() -> None:
    from agent_framework.agents.agent_decision import AgentDecision

    captured: dict[str, object] = {}

    class BatchHost:
        config = type("Config", (), {"skills_catalog_max_tokens": 2000, "memory_builtin_tools_enabled": True})()

        def resolve_model_tool_definitions(self, tool_names, *, agent_id=None, run_id=None):
            return ()

        def get_agent(self, agent_id, *, base_dir=None):
            raise AssertionError("get_agent should not be used in this test")

        def get_tool(self, name):
            raise AssertionError("get_tool should not be used in this test")

        def normalize_memory_parameters(
            self,
            *,
            agent_id,
            run_id,
            parameters,
            child_agent_id=None,
        ):
            captured["normalize_call"] = {
                "agent_id": agent_id,
                "run_id": run_id,
                "parameters": dict(parameters),
                "child_agent_id": child_agent_id,
            }
            return {"normalized": True, **parameters}

        def call_subagent_batch(
            self,
            *,
            caller,
            specs,
            mode,
            timeout_seconds,
            parent_run_id=None,
        ):
            captured["specs"] = specs
            captured["mode"] = mode
            captured["timeout_seconds"] = timeout_seconds
            captured["parent_run_id"] = parent_run_id
            return []

    agent = Agent(
        agent_id="orchestrator",
        role="orchestrator",
        description="",
        system_prompt="sys",
        user_prompt_template="go",
        parameters=(),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
        allowed_child_agents=("child",),
    )
    host = BatchHost()
    run = agent._create_run({})

    decision = AgentDecision.from_model_response(
        ModelResponse(
            payload={
                "kind": "call_subagents",
                "mode": "parallel",
                "calls": [
                    {
                        "subagent_id": "child",
                        "output_key": "child_out",
                        "parameters": {"topic": "x"},
                    }
                ],
            },
            raw_text="",
        )
    )

    outcome = agent.handle_subagent_calls(host=host, run=run, decision=decision, caller_id="host")

    assert outcome is None
    specs = captured["specs"]
    assert len(specs) == 1
    assert specs[0].subagent_id == "child"
    assert specs[0].output_key == "child_out"
    assert specs[0].parameters == {"normalized": True, "topic": "x"}


def _make_subagent_host(tmp_path: Path, parent_id: str, child_id: str, decisions: list) -> tuple:
    """Build a host with two agents (parent calls child) using FakeModelDriver.

    Returns (host, capturing_driver) where the driver records all decide() contexts.
    """
    env_path = tmp_path / ".env"
    write_env(env_path, root_agent=parent_id)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)

    (agents_dir / f"{parent_id}.md").write_text(
        f"---\nid: {parent_id}\nrole: orchestrator\ndescription: top\n"
        f"subagents:\n  - {child_id}\n---\nsys\n---\nrun\n",
        encoding="utf-8",
    )
    (agents_dir / f"{child_id}.md").write_text(
        f"---\nid: {child_id}\nrole: worker\ndescription: worker\n---\nsys\n---\ndo it\n",
        encoding="utf-8",
    )

    class CapturingDriver(FakeModelDriver):
        def __init__(self, payloads):
            super().__init__(payloads)
            self.contexts: list[ModelContext] = []

        def decide(self, *, agent_id, provider_name, model_names, temperature, context):
            self.contexts.append(context)
            return super().decide(
                agent_id=agent_id, provider_name=provider_name,
                model_names=model_names, temperature=temperature, context=context,
            )

    driver = CapturingDriver(decisions)
    host = AgentHost.from_env(
        env_path,
        model_driver=driver,
        input_reader=lambda _: "",
        output_writer=lambda _: None,
    )
    return host, driver


def test_call_subagent_injects_json_envelope_when_parameters_present(tmp_path: Path) -> None:
    """Parent conversation must contain the JSON envelope when the child returns parameters."""
    host, driver = _make_subagent_host(
        tmp_path,
        parent_id="orchestrator",
        child_id="parser",
        decisions=[
            # orchestrator turn 1: call subagent
            {"kind": "call_subagent", "subagent_id": "parser", "parameters": {}},
            # parser: final_message WITH parameters
            {
                "kind": "final_message",
                "message": "parsed 2 intents",
                "parameters": {"intents": ["move", "attack"]},
            },
            # orchestrator turn 2 (after subagent result injected): finish
            {"kind": "final_message", "message": "orchestration done"},
        ],
    )
    host.run_root(initial_instruction="go")

    # The second orchestrator decide() call contains the injected subagent result.
    orchestrator_contexts = [c for c in driver.contexts if len(list(c.messages)) > 2]
    assert orchestrator_contexts, "expected at least one orchestrator context with subagent result"
    last_user_msg = next(
        (m["content"] for m in reversed(list(orchestrator_contexts[0].messages))
         if m.get("role") == "user" and "parser" in m.get("content", "")),
        None,
    )
    assert last_user_msg is not None, "subagent result message not found in orchestrator context"
    envelope = json.loads(last_user_msg.split("Subagent result parser: ", 1)[1])
    assert envelope["message"] == "parsed 2 intents"
    assert envelope["intents"] == ["move", "attack"]


def test_call_subagent_injects_plain_text_when_no_parameters(tmp_path: Path) -> None:
    """Parent conversation must get plain text when child returns no parameters."""
    host, driver = _make_subagent_host(
        tmp_path,
        parent_id="orchestrator",
        child_id="helper",
        decisions=[
            {"kind": "call_subagent", "subagent_id": "helper", "parameters": {}},
            {"kind": "final_message", "message": "helper done"},
            {"kind": "final_message", "message": "orchestration done"},
        ],
    )
    host.run_root(initial_instruction="go")

    orchestrator_contexts = [c for c in driver.contexts if len(list(c.messages)) > 2]
    assert orchestrator_contexts
    last_user_msg = next(
        (m["content"] for m in reversed(list(orchestrator_contexts[0].messages))
         if m.get("role") == "user" and "helper" in m.get("content", "")),
        None,
    )
    assert last_user_msg is not None
    content_after_prefix = last_user_msg.split("Subagent result helper: ", 1)[1]
    # No parameters: must be plain text, not a JSON envelope.
    assert content_after_prefix == "helper done"
