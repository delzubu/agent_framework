"""Tests for the scoped memory subsystem."""

from __future__ import annotations

import json
from pathlib import Path

from agent_framework import Agent, AgentHost, AgentParameter, HostConfig
from agent_framework.config import load_host_config
from agent_framework.memory import (
    CatalogMemoryQueryProvider,
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
    assert {"memory_get", "memory_list", "memory_query"} <= names


def test_memory_tools_list_get_and_query() -> None:
    host = AgentHost.create(model_driver=FakeModelDriver(), config=HostConfig())
    ref = host.store_memory(
        path="deck/full",
        content={"slides": [{"title": "Overview"}]},
        mime_type="application/json",
        title="Deck JSON",
        summary="Session deck",
    )

    list_result = host.get_tool("memory_list").invoke({}, host)
    list_payload = json.loads(list_result)
    assert list_payload[0]["uri"] == ref.uri

    get_result = host.get_tool("memory_get").invoke({"uri": ref.uri}, host)
    assert "<memory " in get_result
    assert ref.uri in get_result

    query_result = host.get_tool("memory_query").invoke({"query": "deck"}, host)
    query_payload = json.loads(query_result)
    assert query_payload[0]["uri"] == ref.uri


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
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_host_config(env_path)
    assert cfg.memory_enabled is False
    assert cfg.memory_builtin_tools_enabled is False
    assert cfg.memory_auto_store_threshold_bytes == 4096
    assert cfg.memory_default_projection_mode == "catalog_only"
