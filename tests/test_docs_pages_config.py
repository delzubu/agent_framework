from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_pages_config_enables_pretty_permalinks() -> None:
    config_path = ROOT / "docs" / "pages" / "_config.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["baseurl"] == "/agent_framework"
    assert config["permalink"] == "pretty"


def test_sdk_reference_links_to_sdk_section_root() -> None:
    content = (ROOT / "docs" / "pages" / "reference" / "sdk-reference.md").read_text(encoding="utf-8")

    assert "{{ '/sdk/' | relative_url }}" in content
    assert "sdk/{{ '/' | relative_url }}" not in content
