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
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
        allowed_skills=("my-skill",),
    )
    run = agent._create_run({})
    context = agent.build_context(host=host, run=run)
    assert len(context.messages) >= 3
    catalog_message = context.messages[2]
    assert catalog_message["role"] == "user"
    assert "<available_skills>" in catalog_message["content"]
    assert "my-skill" in catalog_message["content"]


def test_no_skills_catalog_message_when_no_skills(tmp_path: Path) -> None:
    """Without skills, messages has only system + user (2 entries)."""
    env_path = tmp_path / ".env"
    write_env(env_path)
    host = AgentHost.from_env(env_path, model_driver=FakeModelDriver([]),
                               input_reader=lambda _: "", output_writer=lambda _: None)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
    )
    run = agent._create_run({})
    context = agent.build_context(host=host, run=run)
    assert len(context.messages) == 2
    assert context.messages[0]["role"] == "system"
    assert context.messages[1]["role"] == "user"


def test_capability_metadata_does_not_include_skills_section() -> None:
    """_capability_metadata no longer returns skills_section key."""
    from agent_framework.model import OpenAiModelDriver, CapabilityDefinition
    skills = (CapabilityDefinition(capability_id="my-skill", description="Does things"),)
    metadata = OpenAiModelDriver._capability_metadata(tools=(), subagents=(), skills=skills)
    assert "skills_section" not in metadata


def test_capability_metadata_has_no_skills_section_key_when_empty() -> None:
    """_capability_metadata no longer returns skills_section key even when no skills."""
    from agent_framework.model import OpenAiModelDriver
    metadata = OpenAiModelDriver._capability_metadata(tools=(), subagents=(), skills=())
    assert "skills_section" not in metadata


def test_agent_build_context_populates_skills_from_registry(tmp_path: Path) -> None:
    from agent_framework.skill import SkillRegistry
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
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
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
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
    )
    assert isinstance(agent.onPreSkill, SequentialHook)
    assert isinstance(agent.onPostSkill, SequentialHook)


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
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
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
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
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


def test_read_skill_resource_tool_cleaned_up_after_run(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "A skill")
    env_path = tmp_path / ".env"
    _write_env_with_skills(env_path, skills_dir)
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
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
    assert "read_skill_resource" not in host.tool_registry


def test_skill_hooks_fire_on_invocation(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my-skill", "A skill")
    env_path = tmp_path / ".env"
    _write_env_with_skills(env_path, skills_dir)

    fired = []
    agent = Agent(
        agent_id="tester", role="tester", description="",
        system_prompt="sys", user_prompt_template="Hello",
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
        allowed_skills=(),
    )
    agent.onPreSkill += lambda event: fired.append(("pre", event.skill_name))
    agent.onPostSkill += lambda event: fired.append(("post", event.skill_name))

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
    from agent_framework.agent import SkillStartEvent, SkillEndEvent  # noqa: F401
    from agent_framework.agents import SkillStartEvent, SkillEndEvent  # noqa: F401


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
    result = build_skills_catalog(skills, max_tokens=50)
    # With max_tokens=50, not all 10 skills should appear
    assert result.count('"name"') < 10


def test_build_skills_catalog_keeps_highest_priority_first() -> None:
    """Skills with higher priority survive truncation."""
    from agent_framework.model import build_skills_catalog, CapabilityDefinition
    skills = (
        CapabilityDefinition(capability_id="low-skill",  description="y" * 200, priority=1),
        CapabilityDefinition(capability_id="mid-skill",  description="y" * 200, priority=5),
        CapabilityDefinition(capability_id="high-skill", description="y" * 200, priority=10),
    )
    # Budget tight enough to keep only ~1 skill (each skill text is ~200+ chars / 4 = ~50+ tokens)
    result = build_skills_catalog(skills, max_tokens=80)
    assert "high-skill" in result
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
        parameters=(), provider_name="openai", model_name="gpt-4o-mini",
        allowed_skills=("low-skill", "mid-skill", "high-skill"),
    )
    run = agent._create_run({})
    context = agent.build_context(host=host, run=run)
    # The catalog message should exist and contain only the high-priority skill
    catalog_messages = [m for m in context.messages if "<available_skills>" in m.get("content", "")]
    assert len(catalog_messages) == 1
    catalog_content = catalog_messages[0]["content"]
    assert "high-skill" in catalog_content
    assert "low-skill" not in catalog_content
