"""Tests for CommandRegistry and render()."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_framework.command import CommandDefinition, CommandRegistry, render


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_cmd(
    directory: Path,
    name: str,
    *,
    description: str = "A test command",
    argument_hint: str = "",
    allowed_tools: list[str] | None = None,
    model: str | None = None,
    body: str = "Do something with $ARGUMENTS",
) -> Path:
    lines = ["---", f"description: {description}"]
    if argument_hint:
        lines.append(f"argument-hint: {argument_hint}")
    if allowed_tools:
        lines.append("allowed-tools:")
        for t in allowed_tools:
            lines.append(f"  - {t}")
    if model:
        lines.append(f"model: {model}")
    lines += ["---", body]
    md_path = directory / f"{name}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestCommandRegistryDiscovery:
    def test_discover_parses_frontmatter(self, tmp_path: Path):
        cmds_dir = tmp_path / "cmds"
        cmds_dir.mkdir()
        _write_cmd(cmds_dir, "hello", description="Say hello")
        registry = CommandRegistry(directories=(cmds_dir,))
        registry.discover()
        cmd = registry.get("hello")
        assert cmd.description == "Say hello"
        assert cmd.name == "hello"

    def test_discover_parses_all_fields(self, tmp_path: Path):
        cmds_dir = tmp_path / "cmds"
        cmds_dir.mkdir()
        _write_cmd(
            cmds_dir,
            "greet",
            description="Greet someone",
            argument_hint="<name>",
            allowed_tools=["Read", "Bash"],
            model="gpt-4o",
            body="Hello $1!",
        )
        registry = CommandRegistry(directories=(cmds_dir,))
        registry.discover()
        cmd = registry.get("greet")
        assert cmd.argument_hint == "<name>"
        assert cmd.allowed_tools == ("Read", "Bash")
        assert cmd.model == "gpt-4o"
        assert cmd.prompt_template == "Hello $1!"

    def test_discover_skips_missing_description(self, tmp_path: Path):
        cmds_dir = tmp_path / "cmds"
        cmds_dir.mkdir()
        bad = cmds_dir / "bad.md"
        bad.write_text("---\nargument-hint: foo\n---\nbody\n", encoding="utf-8")
        registry = CommandRegistry(directories=(cmds_dir,))
        registry.discover()
        assert registry.get_all() == ()

    def test_discover_skips_no_frontmatter(self, tmp_path: Path):
        cmds_dir = tmp_path / "cmds"
        cmds_dir.mkdir()
        (cmds_dir / "plain.md").write_text("Just a plain file.\n", encoding="utf-8")
        registry = CommandRegistry(directories=(cmds_dir,))
        registry.discover()
        assert registry.get_all() == ()

    def test_discover_skips_missing_directory(self):
        registry = CommandRegistry(directories=(Path("/nonexistent"),))
        registry.discover()
        assert registry.get_all() == ()

    def test_discover_first_dir_wins_on_duplicate(self, tmp_path: Path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        _write_cmd(dir1, "cmd", description="First version")
        _write_cmd(dir2, "cmd", description="Second version")
        registry = CommandRegistry(directories=(dir1, dir2))
        registry.discover()
        assert registry.get("cmd").description == "First version"

    def test_discover_multiple_commands(self, tmp_path: Path):
        cmds_dir = tmp_path / "cmds"
        cmds_dir.mkdir()
        for i in range(5):
            _write_cmd(cmds_dir, f"cmd{i}", description=f"Command {i}")
        registry = CommandRegistry(directories=(cmds_dir,))
        registry.discover()
        assert len(registry.get_all()) == 5


# ---------------------------------------------------------------------------
# get() / get_all()
# ---------------------------------------------------------------------------


class TestCommandRegistryGet:
    def test_get_returns_command(self, tmp_path: Path):
        cmds_dir = tmp_path / "cmds"
        cmds_dir.mkdir()
        _write_cmd(cmds_dir, "foo", description="Foo")
        registry = CommandRegistry(directories=(cmds_dir,))
        registry.discover()
        cmd = registry.get("foo")
        assert isinstance(cmd, CommandDefinition)

    def test_get_unknown_raises_key_error(self):
        registry = CommandRegistry(directories=())
        with pytest.raises(KeyError, match="Unknown command"):
            registry.get("missing")

    def test_get_all_empty_when_no_discovery(self):
        registry = CommandRegistry(directories=())
        assert registry.get_all() == ()


# ---------------------------------------------------------------------------
# render()
# ---------------------------------------------------------------------------


class TestRender:
    def _cmd(self, template: str) -> CommandDefinition:
        return CommandDefinition(name="test", description="test", prompt_template=template)

    def test_arguments_substitution(self):
        cmd = self._cmd("Do $ARGUMENTS now")
        assert render(cmd, "some args") == "Do some args now"

    def test_arguments_empty_string(self):
        cmd = self._cmd("Do $ARGUMENTS now")
        assert render(cmd, "") == "Do  now"

    def test_positional_substitution(self):
        cmd = self._cmd("Hello $1, you are $2!")
        assert render(cmd, "Alice 30") == "Hello Alice, you are 30!"

    def test_positional_out_of_range_expands_to_empty(self):
        cmd = self._cmd("$1 $2 $3")
        assert render(cmd, "only_one") == "only_one  "

    def test_mixed_arguments_and_positional(self):
        cmd = self._cmd("Args: $ARGUMENTS; First: $1")
        assert render(cmd, "hello world") == "Args: hello world; First: hello"

    def test_no_placeholders(self):
        cmd = self._cmd("Static prompt")
        assert render(cmd, "ignored") == "Static prompt"

    def test_all_nine_positional(self):
        cmd = self._cmd("$1 $2 $3 $4 $5 $6 $7 $8 $9")
        result = render(cmd, "a b c d e f g h i")
        assert result == "a b c d e f g h i"

    def test_source_path_stored(self, tmp_path: Path):
        cmds_dir = tmp_path / "cmds"
        cmds_dir.mkdir()
        _write_cmd(cmds_dir, "named", description="Named command")
        registry = CommandRegistry(directories=(cmds_dir,))
        registry.discover()
        cmd = registry.get("named")
        assert cmd.source_path.name == "named.md"


# ---------------------------------------------------------------------------
# reload
# ---------------------------------------------------------------------------


class TestCommandRegistryReload:
    def test_reload_picks_up_new_files(self, tmp_path: Path):
        cmds_dir = tmp_path / "cmds"
        cmds_dir.mkdir()
        registry = CommandRegistry(directories=(cmds_dir,))
        registry.discover()
        assert registry.get_all() == ()

        _write_cmd(cmds_dir, "new_cmd", description="New command")
        registry.reload()
        assert len(registry.get_all()) == 1

    def test_reload_clears_stale_entries(self, tmp_path: Path):
        cmds_dir = tmp_path / "cmds"
        cmds_dir.mkdir()
        _write_cmd(cmds_dir, "old_cmd", description="Old")
        registry = CommandRegistry(directories=(cmds_dir,))
        registry.discover()
        assert len(registry.get_all()) == 1

        (cmds_dir / "old_cmd.md").unlink()
        registry.reload()
        assert registry.get_all() == ()


# ---------------------------------------------------------------------------
# from_config
# ---------------------------------------------------------------------------


class TestCommandRegistryFromConfig:
    def test_from_config_with_directories(self, tmp_path: Path):
        d1 = tmp_path / "d1"
        d1.mkdir()

        class FakeCfg:
            commands_directories = (d1,)

        registry = CommandRegistry.from_config(FakeCfg())
        assert len(registry.directories) == 1

    def test_from_config_empty_directories(self):
        class FakeCfg:
            commands_directories = ()

        registry = CommandRegistry.from_config(FakeCfg())
        assert registry.directories == ()
