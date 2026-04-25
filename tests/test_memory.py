"""Tests for the scoped memory subsystem."""

from __future__ import annotations

import json
from pathlib import Path

from agent_framework import Agent, AgentHost, AgentParameter, HostConfig, ModelResponse
from agent_framework.config import DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES, load_host_config
from agent_framework.memory import (
    CatalogMemoryQueryProvider,
    ConfiguredMemoryScopeResolver,
    InMemoryMemoryBackend,
    MemoryEntry,
    MemoryQueryHit,
    MemoryRef,
    MemoryScope,
    XmlMemoryProjector,
    build_memory_uri,
)


class FakeModelDriver:
    def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        raise AssertionError("FakeModelDriver.decide should not be called in these tests")


class CapturingModelDriver:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.contexts: list[tuple[str | None, object]] = []

    def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        self.contexts.append((agent_id, context))
        payload = self._payloads.pop(0)
        return ModelResponse(payload=payload, raw_text=json.dumps(payload))


def test_in_memory_backend_put_get_list_and_query() -> None:
    backend = InMemoryMemoryBackend()
    scope = MemoryScope(kind="session", key="abc123")
    ref = MemoryRef(
        uri=build_memory_uri(scope, "deck/full"),
        scope=scope,
        mime_type="application/json",
        title="Deck JSON",
        summary="Full deck payload",
        size_bytes=12,
    )
    entry = MemoryEntry(ref=ref, content_json={"slides": [1, 2]})
    backend.put(entry)

    fetched = backend.get(ref.uri)
    assert fetched.content_json == {"slides": [1, 2]}
    assert backend.list((scope,)) == (ref,)

    hits = CatalogMemoryQueryProvider(backend).query("deck", (scope,))
    assert len(hits) == 1
    assert hits[0].ref.uri == ref.uri


def test_xml_memory_projector_renders_catalog_and_entry() -> None:
    scope = MemoryScope(kind="session", key="abc123")
    ref = MemoryRef(
        uri=build_memory_uri(scope, "deck/full"),
        scope=scope,
        mime_type="application/json",
        title="Deck JSON",
        summary="Full deck payload",
        size_bytes=12,
    )
    entry = MemoryEntry(ref=ref, content_json={"slides": [1]})
    projector = XmlMemoryProjector()

    assert projector.render_catalog(()) == ""

    catalog = projector.render_catalog((MemoryQueryHit(ref=ref),))
    content = projector.render_entries((entry,))
    assert "<available_memory>" in catalog
    assert ref.uri in catalog
    assert "<memory " in content
    assert '"slides"' in content


def test_host_create_registers_memory_tools() -> None:
    host = AgentHost.create(model_driver=FakeModelDriver(), config=HostConfig())
    names = set(host.tool_registry.list_names())
    assert {"memory_get", "memory_list", "memory_query", "memory_put", "memory_update"} <= names


def test_host_memory_factories_are_extension_points() -> None:
    class CustomBackend(InMemoryMemoryBackend):
        pass

    class CustomHost(AgentHost):
        def create_memory_backend(self):
            return CustomBackend()

    host = CustomHost.create(model_driver=FakeModelDriver(), config=HostConfig())
    assert isinstance(host.get_memory_backend(), CustomBackend)
    assert isinstance(host.get_memory_query_provider(), CatalogMemoryQueryProvider)
    assert isinstance(host.get_memory_scope_resolver(), ConfiguredMemoryScopeResolver)


def test_default_visible_memory_scopes_are_session_only() -> None:
    host = AgentHost.create(model_driver=FakeModelDriver(), config=HostConfig())

    assert host.get_visible_memory_scopes(agent_id="reviewer", run_id="run-1") == (
        MemoryScope(kind="session", key=host.session_id),
    )


def test_host_uses_configured_extra_memory_scopes() -> None:
    host = AgentHost.create(
        model_driver=FakeModelDriver(),
        config=HostConfig(
            memory_global_scopes=("default",),
            memory_group_scopes=("deck-reviewers",),
            memory_use_case_scopes=("slide-review",),
            memory_enable_agent_scope=True,
        ),
    )

    assert host.get_visible_memory_scopes(agent_id="reviewer", run_id="run-1") == (
        MemoryScope(kind="session", key=host.session_id),
        MemoryScope(kind="global", key="default"),
        MemoryScope(kind="group", key="deck-reviewers"),
        MemoryScope(kind="use_case", key="slide-review"),
        MemoryScope(kind="agent", key="reviewer"),
    )


def test_memory_scope_resolver_is_an_extension_point() -> None:
    class FixedScopeResolver:
        def visible_scopes(self, *, session_id: str, agent_id: str, run_id: str):
            return (
                MemoryScope(kind="session", key=session_id),
                MemoryScope(kind="group", key=f"{agent_id}:{run_id}"),
            )

    class CustomHost(AgentHost):
        def create_memory_scope_resolver(self):
            return FixedScopeResolver()

    host = CustomHost.create(model_driver=FakeModelDriver(), config=HostConfig())

    assert host.get_visible_memory_scopes(agent_id="reviewer", run_id="run-1") == (
        MemoryScope(kind="session", key=host.session_id),
        MemoryScope(kind="group", key="reviewer:run-1"),
    )


def test_agents_receive_memory_read_tools_by_default() -> None:
    host = AgentHost.create(model_driver=FakeModelDriver(), config=HostConfig())
    agent = Agent(
        agent_id="reviewer",
        role="reviewer",
        description="",
        system_prompt="sys",
        user_prompt_template="go",
        parameters=(),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
    )
    context = agent.build_context(host=host, run=agent._create_run({}))
    tool_names = {tool.tool_id for tool in context.tools}
    assert {"memory_get", "memory_list", "memory_query"} <= tool_names
    assert "memory_put" not in tool_names
    assert "memory_update" not in tool_names


def test_memory_tools_list_get_and_query() -> None:
    host = AgentHost.create(
        model_driver=FakeModelDriver(),
        config=HostConfig(memory_global_scopes=("shared",)),
    )
    ref = host.store_memory(
        path="deck/full",
        content={"slides": [{"title": "Overview"}]},
        mime_type="application/json",
        title="Deck JSON",
        summary="Session deck",
    )
    global_ref = host.store_memory(
        path="deck/style-guide",
        content="Prefer concise slide titles.",
        mime_type="text/markdown",
        scope=MemoryScope(kind="global", key="shared"),
        title="Deck Style Guide",
        summary="Global review rules",
    )

    list_result = host.get_tool("memory_list").invoke({}, host)
    list_payload = json.loads(list_result)
    assert {item["uri"] for item in list_payload} == {ref.uri, global_ref.uri}

    filtered_list = host.get_tool("memory_list").invoke(
        {"scope_kind": "global", "scope_key": "shared"},
        host,
    )
    filtered_list_payload = json.loads(filtered_list)
    assert filtered_list_payload == [
        {
            "uri": global_ref.uri,
            "scope": "global:shared",
            "mime_type": "text/markdown",
            "title": "Deck Style Guide",
            "summary": "Global review rules",
        }
    ]

    get_result = host.get_tool("memory_get").invoke({"uri": ref.uri}, host)
    assert "<memory " in get_result
    assert ref.uri in get_result

    query_result = host.get_tool("memory_query").invoke({"query": "deck"}, host)
    query_payload = json.loads(query_result)
    assert {item["uri"] for item in query_payload} == {ref.uri, global_ref.uri}

    filtered_query = host.get_tool("memory_query").invoke(
        {"query": "deck", "scope_kind": "global", "scope_key": "shared"},
        host,
    )
    filtered_query_payload = json.loads(filtered_query)
    assert [item["uri"] for item in filtered_query_payload] == [global_ref.uri]


def test_memory_put_and_update_tools_are_available_but_not_default() -> None:
    host = AgentHost.create(model_driver=FakeModelDriver(), config=HostConfig())

    put_result = host.get_tool("memory_put").invoke(
        {
            "path": "notes/topic-a",
            "content": {"answer": 42},
            "title": "Topic A",
            "summary": "Structured note",
        },
        host,
    )
    put_payload = json.loads(put_result)
    assert put_payload["uri"].startswith("mem://session/")
    assert put_payload["version"] == "1"

    update_result = host.get_tool("memory_update").invoke(
        {
            "uri": put_payload["uri"],
            "content": {"answer": 43},
            "summary": "Updated structured note",
        },
        host,
    )
    update_payload = json.loads(update_result)
    assert update_payload["uri"] == put_payload["uri"]
    assert update_payload["version"] == "2"
    assert host.get_memory(put_payload["uri"]).content_json == {"answer": 43}


def test_memory_operations_are_written_to_audit_trace(tmp_path: Path) -> None:
    host = AgentHost.create(model_driver=FakeModelDriver(), config=HostConfig())
    tracer = host.enable_audit_trace(output_dir=tmp_path / "logs")
    from agent_framework.tracing import CompositeRuntimeTracer

    if not isinstance(host.runtime_tracer, CompositeRuntimeTracer):
        raise AssertionError("Expected CompositeRuntimeTracer after enabling audit trace")

    ref = host.create_memory(
        path="notes/topic-a",
        content={"answer": 42},
        title="Topic A",
        summary="Structured note",
    )
    host.update_memory(uri=ref.uri, content={"answer": 43}, summary="Updated structured note")

    text = tracer.output_path.read_text(encoding="utf-8")
    assert '"type": "memory_operation"' in text
    assert '"operation": "memory_put"' in text
    assert '"operation": "memory_update"' in text
    assert ref.uri in text


def test_agent_build_context_includes_memory_catalog_and_resolved_entry() -> None:
    host = AgentHost.create(model_driver=FakeModelDriver(), config=HostConfig())
    ref = host.store_memory(
        path="deck/full",
        content={"slides": [{"title": "Overview"}]},
        mime_type="application/json",
        title="Deck JSON",
        summary="Session deck",
    )
    agent = Agent(
        agent_id="reviewer",
        role="reviewer",
        description="",
        system_prompt="sys",
        user_prompt_template="<deck_ref>{{deck_ref}}</deck_ref>",
        parameters=(AgentParameter(name="deck_ref", description="Deck memory ref"),),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
    )
    run = agent._create_run({"deck_ref": ref.uri})
    agent.refresh_parameter_state(run)
    context = agent.build_context(host=host, run=run)

    joined = "\n".join(str(message.get("content", "")) for message in context.messages)
    assert "<available_memory>" in joined
    assert ref.uri in joined
    assert "<memory " in joined
    assert run.visible_memory_scopes == (MemoryScope(kind="session", key=host.session_id),)
    assert run.resolved_memory_refs == (ref,)


def test_load_host_config_reads_memory_settings(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=test-key",
                "DEFAULT_PROVIDER=openai",
                "DEFAULT_MODEL=gpt-4o-mini",
                "AGENT_DIRECTORY=agents",
                "TOOLS_DIRECTORY=tools",
                "WORLD_DIRECTORY=world",
                "ROOT_AGENT=root",
                "MEMORY_ENABLED=false",
                "MEMORY_BUILTIN_TOOLS_ENABLED=false",
                "MEMORY_AUTO_STORE_THRESHOLD_BYTES=4096",
                "MEMORY_DEFAULT_PROJECTION_MODE=catalog_only",
                "MEMORY_BACKEND=memory",
                "MEMORY_QUERY_PROVIDER=catalog",
                "MEMORY_PROJECTOR=xml",
                "MEMORY_GLOBAL_SCOPES=default,shared",
                "MEMORY_GROUP_SCOPES=deck-reviewers",
                "MEMORY_USE_CASE_SCOPES=slide-review",
                "MEMORY_ENABLE_AGENT_SCOPE=true",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_host_config(env_path)
    assert cfg.memory_enabled is False
    assert cfg.memory_builtin_tools_enabled is False
    assert cfg.memory_auto_store_threshold_bytes == 4096
    assert cfg.memory_default_projection_mode == "catalog_only"
    assert cfg.memory_global_scopes == ("default", "shared")
    assert cfg.memory_group_scopes == ("deck-reviewers",)
    assert cfg.memory_use_case_scopes == ("slide-review",)
    assert cfg.memory_enable_agent_scope is True


def test_memory_threshold_default_is_sensible_in_code() -> None:
    cfg = HostConfig()
    assert cfg.memory_auto_store_threshold_bytes == DEFAULT_MEMORY_AUTO_STORE_THRESHOLD_BYTES
    assert cfg.memory_auto_store_threshold_bytes == 32768


def test_agent_run_auto_stores_large_seed_parameter_and_rerenders_prompt() -> None:
    driver = CapturingModelDriver([{"kind": "final_message", "message": "done"}])
    host = AgentHost.create(
        model_driver=driver,
        config=HostConfig(memory_auto_store_threshold_bytes=32),
    )
    agent = Agent(
        agent_id="reviewer",
        role="reviewer",
        description="",
        system_prompt="sys",
        user_prompt_template="<deck>{{deck_json}}</deck>",
        parameters=(AgentParameter(name="deck_json", description="Deck JSON"),),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
    )
    payload = {"slides": [{"title": "Overview", "body": "X" * 128}]}

    result = agent.run(host=host, parameters={"deck_json": payload}, caller_id="host")
    assert result.message == "done"

    _, context = driver.contexts[0]
    assert "mem://session/" in context.user_prompt
    joined = "\n".join(str(message.get("content", "")) for message in context.messages)
    assert "<memory " in joined
    assert '"slides"' in joined
    assert "X" * 64 in joined


def test_large_prompt_text_is_not_auto_stored_to_memory() -> None:
    driver = CapturingModelDriver([{"kind": "final_message", "message": "done"}])
    host = AgentHost.create(
        model_driver=driver,
        config=HostConfig(memory_auto_store_threshold_bytes=32),
    )
    agent = Agent(
        agent_id="reviewer",
        role="reviewer",
        description="",
        system_prompt="sys",
        user_prompt_template="go",
        parameters=(),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
    )
    prompt_fragment = "<notes>" + ("Z" * 256) + "</notes>"

    result = agent.run(
        host=host,
        parameters={},
        caller_id="host",
        prompt_fragments=(prompt_fragment,),
        rendered_prompt_override="review this",
    )
    assert result.message == "done"

    _, context = driver.contexts[0]
    assert "mem://session/" not in context.user_prompt
    joined = "\n".join(str(message.get("content", "")) for message in context.messages)
    assert "<memory " not in joined


def test_root_prompt_file_blocks_are_lifted_to_memory(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=test-key",
                "DEFAULT_PROVIDER=openai",
                "DEFAULT_MODEL=gpt-4o-mini",
                "AGENT_DIRECTORY=agents",
                "TOOLS_DIRECTORY=tools",
                "WORLD_DIRECTORY=world",
                "ROOT_AGENT=reviewer",
                "MEMORY_AUTO_STORE_THRESHOLD_BYTES=32",
            ]
        ),
        encoding="utf-8",
    )
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "reviewer.md").write_text(
        "---\n"
        "id: reviewer\n"
        "role: reviewer\n"
        "---\n"
        "sys\n"
        "---\n"
        "go\n",
        encoding="utf-8",
    )
    deck_path = tmp_path / "full-extract.json"
    deck_path.write_text(
        json.dumps({"slides": [{"title": "Overview", "body": "Z" * 128}]}, indent=2),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    driver = CapturingModelDriver([{"kind": "final_message", "message": "done"}])
    host = AgentHost.from_env(env_path, model_driver=driver)

    result = host.run_root(initial_instruction="Review this deck: @full-extract.json")
    assert result.message == "done"

    _, context = driver.contexts[0]
    assert '<memory id="mem://session/' in context.user_prompt
    assert "<file name=" not in context.user_prompt

    joined = "\n".join(str(message.get("content", "")) for message in context.messages)
    assert "<memory " in joined
    assert '<file name="full-extract.json">' in joined
    assert "Z" * 64 in joined


def test_prompt_text_ignores_malformed_memory_like_tokens() -> None:
    driver = CapturingModelDriver([{"kind": "final_message", "message": "done"}])
    host = AgentHost.create(model_driver=driver, config=HostConfig())
    agent = Agent(
        agent_id="reviewer",
        role="reviewer",
        description="",
        system_prompt="sys",
        user_prompt_template="go",
        parameters=(),
        provider_name="openai",
        model_names=("gpt-4o-mini",),
    )

    result = agent.run(
        host=host,
        parameters={},
        caller_id="host",
        rendered_prompt_override="Notes: `mem://`+projection), should stay literal.",
    )
    assert result.message == "done"

    _, context = driver.contexts[0]
    joined = "\n".join(str(message.get("content", "")) for message in context.messages)
    assert "<memory " not in joined


def test_subagent_call_auto_stores_large_parameter_before_child_run(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=test-key",
                "DEFAULT_PROVIDER=openai",
                "DEFAULT_MODEL=gpt-4o-mini",
                "AGENT_DIRECTORY=agents",
                "TOOLS_DIRECTORY=tools",
                "WORLD_DIRECTORY=world",
                "ROOT_AGENT=orchestrator",
                "MEMORY_AUTO_STORE_THRESHOLD_BYTES=32",
            ]
        ),
        encoding="utf-8",
    )
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "orchestrator.md").write_text(
        "---\n"
        "id: orchestrator\n"
        "role: orchestrator\n"
        "subagents:\n"
        "  - child\n"
        "---\n"
        "sys\n"
        "---\n"
        "go\n",
        encoding="utf-8",
    )
    (agents_dir / "child.md").write_text(
        "---\n"
        "id: child\n"
        "role: child\n"
        "parameters:\n"
        "  deck_json:\n"
        "    description: deck payload\n"
        "    required: true\n"
        "---\n"
        "sys\n"
        "---\n"
        "<deck>{{deck_json}}</deck>\n",
        encoding="utf-8",
    )
    large_payload = {"slides": [{"title": "Overview", "body": "Y" * 128}]}
    driver = CapturingModelDriver(
        [
            {
                "kind": "call_subagent",
                "subagent_id": "child",
                "parameters": {"deck_json": large_payload},
            },
            {"kind": "final_message", "message": "child done"},
            {"kind": "final_message", "message": "done"},
        ]
    )
    host = AgentHost.from_env(env_path, model_driver=driver)

    result = host.run_root(initial_instruction="review this")
    assert result.message == "done"

    child_context = next(context for agent_id, context in driver.contexts if agent_id == "child")
    assert "mem://session/" in child_context.user_prompt
    joined = "\n".join(str(message.get("content", "")) for message in child_context.messages)
    assert "<memory " in joined
    assert '"slides"' in joined

    orchestrator_second = [context for agent_id, context in driver.contexts if agent_id == "orchestrator"][1]
    parent_joined = "\n".join(str(message.get("content", "")) for message in orchestrator_second.messages)
    assert "Subagent call child:" in parent_joined
    assert "mem://session/" in parent_joined
