from __future__ import annotations

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
PAGES_ROOT = ROOT / "docs" / "pages"
RELATIVE_URL_PATTERN = re.compile(r"\{\{\s*'([^']+)'\s*\|\s*relative_url\s*\}\}")


def test_pages_config_enables_pretty_permalinks() -> None:
    config_path = PAGES_ROOT / "_config.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["baseurl"] == "/agent_framework"
    assert config["permalink"] == "pretty"
    assert "build" in config.get("include", [])
    assert "sdk" in config.get("exclude", [])


def test_gitignore_does_not_hide_docs_build_section() -> None:
    content = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/build/" in content
    assert "\nbuild/\n" not in content


def test_sdk_reference_links_to_sdk_section_root() -> None:
    content = (PAGES_ROOT / "reference" / "sdk-reference.md").read_text(encoding="utf-8")

    assert "{{ '/sdk/' | relative_url }}" in content
    assert "sdk/{{ '/' | relative_url }}" not in content


def test_docs_relative_url_targets_resolve_to_existing_routes() -> None:
    routes = _collect_routes()
    markdown_files = [
        path
        for path in PAGES_ROOT.rglob("*.md")
        if not any(part.startswith("_") for part in path.relative_to(PAGES_ROOT).parts)
    ]

    missing: list[str] = []
    for path in markdown_files:
        content = path.read_text(encoding="utf-8")
        for raw_target in RELATIVE_URL_PATTERN.findall(content):
            if raw_target.startswith("http://") or raw_target.startswith("https://"):
                continue
            normalized = _normalize_route(raw_target)
            if normalized in {"/", "/sdk/"}:
                continue
            if normalized not in routes:
                missing.append(f"{path.relative_to(ROOT)} -> {normalized}")

    assert not missing, "Broken docs routes:\n" + "\n".join(sorted(missing))


def test_usage_accounting_docs_are_present() -> None:
    guide = (ROOT / "docs" / "guides" / "using-agent-framework.md").read_text(encoding="utf-8")
    dev_ref = (PAGES_ROOT / "reference" / "developer-documentation.md").read_text(encoding="utf-8")

    assert "runtime.agent_finished.usage_self" in guide
    assert "runtime.session_finished.usage_session_totals" in guide
    assert "output_cached_tokens" in guide
    assert "LLM Usage Accounting" in dev_ref
    assert "usage_inclusive" in dev_ref


def _collect_routes() -> set[str]:
    routes = {"/"}
    for path in PAGES_ROOT.rglob("*.md"):
        rel = path.relative_to(PAGES_ROOT)
        if any(part.startswith("_") for part in rel.parts):
            continue
        parts = list(rel.parts)
        if parts[-1] == "index.md":
            route_parts = parts[:-1]
        else:
            route_parts = parts[:-1] + [path.stem]
        route = "/" + "/".join(route_parts)
        routes.add(_normalize_route(route))
    return routes


def _normalize_route(value: str) -> str:
    stripped = value.strip()
    if not stripped.startswith("/"):
        stripped = "/" + stripped
    normalized = re.sub(r"/+", "/", stripped)
    if normalized != "/":
        normalized = normalized.rstrip("/") + "/"
    return normalized
