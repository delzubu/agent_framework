import json
from dataclasses import FrozenInstanceError
from pathlib import Path

from agent_framework.agent import Agent, AgentBehavior, AgentEndHookDecision, AgentHookDecision, AgentParameter, AgentResult
from agent_framework.agents.agent_run import AgentRun
from agent_framework.config import load_host_config
from agent_framework.host import AgentHost
from agent_framework.model import ModelContext, ModelResponse


def test_agent_run_has_skill_tool_names() -> None:
    run = AgentRun(run_id="x", rendered_prompt="p", seed_parameters={}, parameter_values={})
    assert run.skill_tool_names == []
    run.skill_tool_names.append("read_skill_resource")
    assert run.skill_tool_names == ["read_skill_resource"]


class FakeModelDriver:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.on_request_trace = None
        self.on_response_trace = None

    def set_trace_callbacks(self, *, on_request=None, on_response=None):
        self.on_request_trace = on_request
        self.on_response_trace = on_response

    def decide(self, *, agent_id, provider_name, model_name, temperature, context: ModelContext):
        payload = self._payloads.pop(0)
        return ModelResponse(payload=payload, raw_text=str(payload))


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
    agent = Agent.from_markdown(agent_path, default_provider='openai', default_model='gpt-4o-mini')
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
        model_name='gpt-4o-mini',
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
        model_name='gpt-4o-mini',
    )
    host = AgentHost(
        config=load_host_config(env_path),
        model_driver=FakeModelDriver([{'kind': 'final_message', 'message': 'done'}]),
        input_reader=lambda _: '',
        output_writer=lambda _: None,
    )

    def stop_agent_callback(event):
        return AgentHookDecision(
            final_result=AgentResult(status='completed', message='done|wrapped', prompt=event.invocation.rendered_prompt)
        )

    agent.onPreAgent += stop_agent_callback
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
