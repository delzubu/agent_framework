from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
OVERLAYS = ROOT / "docs" / "sdk-overlays"

project = "agent_framework SDK"
author = "agent_framework contributors"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_css_files = ["sdk.css"]

html_theme_options = {
    "show_nav_level": 2,
    "navigation_depth": 4,
    "collapse_navigation": True,
    "show_toc_level": 2,
    "navbar_align": "left",
    "github_url": "https://github.com/delzubu/agent_framework",
}

toc_object_entries = False
autosummary_generate = False
autodoc_typehints = "description"
autodoc_member_order = "bysource"
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}
napoleon_google_docstring = True
napoleon_numpy_docstring = True


def overlay_path_for(what: str, name: str) -> Path | None:
    parts = name.split(".")
    if what == "module":
        return OVERLAYS.joinpath(*parts).with_suffix(".md")
    if what == "class" and len(parts) > 1:
        return OVERLAYS.joinpath(*parts[:-1], parts[-1]).with_suffix(".md")
    return None


def overlay_include(path: Path) -> list[str]:
    return [
        "",
        f".. include:: {path.as_posix()}",
        "   :parser: myst_parser.sphinx_",
        "",
    ]


def insert_overlay(lines: list[str], overlay_lines: list[str]) -> None:
    insert_at = len(lines)
    section_markers = (
        "Args:",
        "Arguments:",
        "Attributes:",
        "Keyword Args:",
        "Keyword Arguments:",
        "Parameters:",
        "Returns:",
        "Raises:",
        "Yields:",
    )
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped in section_markers or stripped.startswith((":ivar ", ":vartype ", ":param ", ":type ")):
            insert_at = index
            break
    lines[insert_at:insert_at] = overlay_lines


def inject_overlay(app, what, name, obj, options, lines):
    path = overlay_path_for(what, name)
    if path is None or not path.exists():
        return
    insert_overlay(lines, overlay_include(path))


def setup(app):
    app.connect("autodoc-process-docstring", inject_overlay, priority=0)
