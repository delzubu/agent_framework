from __future__ import annotations

from pathlib import Path

from agent_framework_skills import installer


def _write_skill_tree(root: Path) -> None:
    skill_dir = root / "use-agent-framework"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: use-agent-framework\n---\n", encoding="utf-8")


def test_list_targets_treats_first_directory_as_installation_marker(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".cursor").mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(tmp_path)

    targets = {target.label: target for target in installer.list_targets()}

    cursor = targets["Cursor"]
    assert cursor.exists is True
    assert cursor.path == (home / ".cursor" / "skills").resolve()


def test_install_creates_missing_skills_subdirectory_when_marker_exists(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".cursor").mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(tmp_path)

    skills_root = tmp_path / "bundled-skills"
    _write_skill_tree(skills_root)
    monkeypatch.setattr(installer, "SKILLS_DIR", skills_root)

    results = installer.install()

    expected = (home / ".cursor" / "skills" / "use-agent-framework").resolve()
    assert (str(expected), "installed") in results
    assert expected.is_dir()
    assert (expected / "SKILL.md").exists()
