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


def test_read_skill_resource_resolves_relative_to_skill_dir(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillContent, ReadSkillResourceTool
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "guide.md").write_text("# Guide content", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: d\n---\n", encoding="utf-8")
    defn = SkillDefinition(name="my-skill", description="d", version=None, priority=0,
                           source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve())
    content = SkillContent(definition=defn, body="", inventory=())
    tool = ReadSkillResourceTool._make(content)
    result = tool.invoke({"path": "guide.md"}, host=None)  # type: ignore[arg-type]
    assert "Guide content" in result


def test_read_skill_resource_returns_error_for_missing_file(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillContent, ReadSkillResourceTool
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: d\n---\n", encoding="utf-8")
    defn = SkillDefinition(name="my-skill", description="d", version=None, priority=0,
                           source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve())
    content = SkillContent(definition=defn, body="", inventory=())
    tool = ReadSkillResourceTool._make(content)
    result = tool.invoke({"path": "nonexistent.md"}, host=None)  # type: ignore[arg-type]
    assert "not found" in result.lower()


def test_read_skill_resource_empty_path_returns_error(tmp_path: Path) -> None:
    from agent_framework.skill import SkillDefinition, SkillContent, ReadSkillResourceTool
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: d\n---\n", encoding="utf-8")
    defn = SkillDefinition(name="my-skill", description="d", version=None, priority=0,
                           source_path=(skill_dir / "SKILL.md").resolve(), skill_dir=skill_dir.resolve())
    content = SkillContent(definition=defn, body="", inventory=())
    tool = ReadSkillResourceTool._make(content)
    result = tool.invoke({"path": ""}, host=None)  # type: ignore[arg-type]
    assert "required" in result.lower() or "not found" in result.lower()


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
