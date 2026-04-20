from __future__ import annotations

import ast
import shutil
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SDK_SOURCE = ROOT / "docs" / "sdk-source"
API_DIR = SDK_SOURCE / "api"
OVERLAYS = ROOT / "docs" / "sdk-overlays"
PACKAGES = ("agent_framework", "agent_framework_evaluator", "agent_framework_skills")


@dataclass
class ClassInfo:
    name: str
    methods: list[str] = field(default_factory=list)


@dataclass
class ModuleInfo:
    name: str
    path: Path
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)


def public(name: str) -> bool:
    return not name.startswith("_")


def module_name_for(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def parse_module(path: Path) -> ModuleInfo:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = ModuleInfo(name=module_name_for(path), path=path)

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and public(node.name):
            methods = [
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and public(item.name)
            ]
            module.classes.append(ClassInfo(name=node.name, methods=methods))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and public(node.name):
            module.functions.append(node.name)

    return module


def discover_modules() -> list[ModuleInfo]:
    modules: list[ModuleInfo] = []
    for package in PACKAGES:
        for path in sorted((SRC / package).rglob("*.py")):
            if path.name == "__main__.py":
                continue
            modules.append(parse_module(path))
    return modules


def title(text: str, char: str = "=") -> str:
    return f"{text}\n{char * len(text)}\n\n"


def overlay_path_for_module(module_name: str) -> Path:
    return OVERLAYS.joinpath(*module_name.split(".")).with_suffix(".md")


def overlay_path_for_class(module_name: str, class_name: str) -> Path:
    return OVERLAYS.joinpath(*module_name.split("."), class_name).with_suffix(".md")


def include_overlay(path: Path) -> str:
    if not path.exists():
        return ""
    rel = path.relative_to(ROOT).as_posix()
    return f".. include:: ../../{rel}\n   :parser: myst_parser.sphinx_\n\n"


def module_rst_path(module_name: str) -> Path:
    parts = module_name.split(".")
    if len(parts) == 1:
        return API_DIR / parts[0] / "index.rst"
    return API_DIR.joinpath(*parts).with_suffix(".rst")


def class_rst_path(module_name: str, class_name: str) -> Path:
    return module_rst_path(module_name).with_suffix("") / f"{class_name}.rst"


def rst_ref(from_path: Path, to_path: Path) -> str:
    return to_path.relative_to(from_path.parent).with_suffix("").as_posix()


def write_module_page(module: ModuleInfo) -> None:
    path = module_rst_path(module.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [title(module.name), include_overlay(overlay_path_for_module(module.name))]

    if module.classes:
        lines.extend([
            "Classes\n-------\n\n",
            ".. toctree::\n   :maxdepth: 1\n\n",
        ])
        for cls in module.classes:
            lines.append(f"   {rst_ref(path, class_rst_path(module.name, cls.name))}\n")
        lines.append("\n")

    lines.extend([
        "Module API\n----------\n\n",
        f".. automodule:: {module.name}\n",
        "   :members:\n",
        "   :exclude-members: " + ", ".join(cls.name for cls in module.classes) + "\n" if module.classes else "",
        "   :show-inheritance:\n\n",
    ])
    path.write_text("".join(lines), encoding="utf-8")


def write_class_page(module: ModuleInfo, cls: ClassInfo) -> None:
    path = class_rst_path(module.name, cls.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fqcn = f"{module.name}.{cls.name}"
    lines = [
        title(cls.name),
        f"Module: :mod:`{module.name}`\n\n",
        include_overlay(overlay_path_for_class(module.name, cls.name)),
        f".. autoclass:: {fqcn}\n",
        "   :members:\n",
        "   :show-inheritance:\n",
        "   :inherited-members:\n\n",
    ]
    path.write_text("".join(lines), encoding="utf-8")


def write_package_index(package: str, modules: list[ModuleInfo]) -> None:
    path = API_DIR / package / "index.rst"
    path.parent.mkdir(parents=True, exist_ok=True)
    package_modules = [module for module in modules if module.name == package or module.name.startswith(package + ".")]
    lines = [
        title(package),
        f".. automodule:: {package}\n",
        "   :members:\n\n",
        ".. toctree::\n",
        "   :maxdepth: 3\n\n",
    ]
    for module in package_modules:
        module_path = module_rst_path(module.name)
        if module_path == path:
            continue
        lines.append(f"   {rst_ref(path, module_path)}\n")
    path.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    if API_DIR.exists():
        shutil.rmtree(API_DIR)

    modules = discover_modules()
    for package in PACKAGES:
        write_package_index(package, modules)
    for module in modules:
        write_module_page(module)
        for cls in module.classes:
            write_class_page(module, cls)


if __name__ == "__main__":
    main()
