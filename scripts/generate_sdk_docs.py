from __future__ import annotations

import ast
import argparse
import posixpath
import shutil
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PAGES = ROOT / "docs" / "pages"
SDK_DIR = PAGES / "sdk"
SDK_NAV = PAGES / "_includes" / "sdk-navigation.html"
SDK_OVERLAYS = ROOT / "docs" / "sdk-overlays"
PACKAGES = ("agent_framework", "agent_framework_evaluator", "agent_framework_skills")


@dataclass
class FunctionDoc:
    name: str
    signature: str
    docstring: str
    is_async: bool = False


@dataclass
class ClassDoc:
    name: str
    bases: list[str]
    docstring: str
    methods: list[FunctionDoc] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)


@dataclass
class ModuleDoc:
    name: str
    source_path: Path
    page_path: Path
    docstring: str
    functions: list[FunctionDoc]
    classes: list[ClassDoc]


def public(name: str) -> bool:
    return not name.startswith("_")


def unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "..."


def format_arg(arg: ast.arg, default: ast.expr | None = None) -> str:
    text = arg.arg
    if arg.annotation is not None:
        text += f": {unparse(arg.annotation)}"
    if default is not None:
        text += f" = {unparse(default)}"
    return text


def signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    parts: list[str] = []

    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    posonly_count = len(args.posonlyargs)

    for index, (arg, default) in enumerate(zip(positional, defaults)):
        parts.append(format_arg(arg, default))
        if posonly_count and index == posonly_count - 1:
            parts.append("/")

    if args.vararg is not None:
        vararg = "*" + format_arg(args.vararg)
        parts.append(vararg)
    elif args.kwonlyargs:
        parts.append("*")

    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(format_arg(arg, default))

    if args.kwarg is not None:
        parts.append("**" + format_arg(args.kwarg))

    result = f"{node.name}({', '.join(parts)})"
    if node.returns is not None:
        result += f" -> {unparse(node.returns)}"
    return result


def function_doc(node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionDoc:
    return FunctionDoc(
        name=node.name,
        signature=signature(node),
        docstring=ast.get_docstring(node) or "",
        is_async=isinstance(node, ast.AsyncFunctionDef),
    )


def class_doc(node: ast.ClassDef) -> ClassDoc:
    methods: list[FunctionDoc] = []
    attributes: list[str] = []

    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and public(item.name):
            methods.append(function_doc(item))
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and public(item.target.id):
            attributes.append(item.target.id)
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and public(target.id):
                    attributes.append(target.id)

    return ClassDoc(
        name=node.name,
        bases=[unparse(base) for base in node.bases],
        docstring=ast.get_docstring(node) or "",
        methods=methods,
        attributes=sorted(set(attributes)),
    )


def module_name_for(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def module_page_path(module_name: str) -> Path:
    parts = module_name.split(".")
    if len(parts) == 1:
        return SDK_DIR / parts[0] / "index.md"
    return SDK_DIR.joinpath(*parts).with_suffix(".md")


def class_page_path(module_name: str, class_name: str) -> Path:
    module_page = module_page_path(module_name)
    return module_page.with_suffix("") / f"{class_name}.md"


def html_for_page(page_path: Path) -> str:
    rel = page_path.relative_to(PAGES).with_suffix(".html")
    return "/" + rel.as_posix()


def relative_html(from_page: Path, to_page: Path) -> str:
    from_dir = from_page.relative_to(PAGES).parent.as_posix() or "."
    to_html = to_page.relative_to(PAGES).with_suffix(".html").as_posix()
    return posixpath.relpath(to_html, start=from_dir)


def anchor(text: str) -> str:
    result = []
    for char in text.lower():
        if char.isalnum():
            result.append(char)
        elif char in {" ", "_", "-"}:
            result.append("-")
    return "".join(result).strip("-")


def parse_module(path: Path) -> ModuleDoc:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions: list[FunctionDoc] = []
    classes: list[ClassDoc] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and public(node.name):
            functions.append(function_doc(node))
        elif isinstance(node, ast.ClassDef) and public(node.name):
            classes.append(class_doc(node))

    module_name = module_name_for(path)
    return ModuleDoc(
        name=module_name,
        source_path=path,
        page_path=module_page_path(module_name),
        docstring=ast.get_docstring(tree) or "",
        functions=functions,
        classes=classes,
    )


def remove_existing_module_pages(module_name: str) -> None:
    page = module_page_path(module_name)
    page.unlink(missing_ok=True)
    class_dir = page.with_suffix("")
    if class_dir.exists():
        shutil.rmtree(class_dir)


def front_matter(title: str) -> str:
    return f"---\ntitle: {title}\nlayout: default\nsdk_page: true\n---\n\n"


def overlay_path_for_module(module_name: str) -> Path:
    return SDK_OVERLAYS.joinpath(*module_name.split(".")).with_suffix(".md")


def overlay_path_for_class(module_name: str, class_name: str) -> Path:
    return SDK_OVERLAYS.joinpath(*module_name.split("."), class_name).with_suffix(".md")


def overlay_block(path: Path) -> list[str]:
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return []
    return [
        "<!-- BEGIN sdk-overlay -->",
        "",
        content,
        "",
        "<!-- END sdk-overlay -->",
        "",
    ]


def write_module_page(module: ModuleDoc) -> None:
    module.page_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        front_matter(module.name),
        f"# `{module.name}`",
        "",
    ]
    lines.extend(overlay_block(overlay_path_for_module(module.name)))
    lines.extend([
        "## API Summary",
        "",
        module.docstring or "No module docstring is available yet.",
        "",
        "## Source",
        "",
        f"`{module.source_path.relative_to(ROOT).as_posix()}`",
        "",
    ])

    if module.classes:
        lines.extend(["## Classes", ""])
        for cls in module.classes:
            lines.append(f"- [`{cls.name}`]({relative_html(module.page_path, class_page_path(module.name, cls.name))})")
        lines.append("")

    if module.functions:
        lines.extend(["## Functions", ""])
        for func in module.functions:
            lines.extend([
                f"### `{func.name}`",
                "",
                "```python",
                ("async def " if func.is_async else "def ") + func.signature,
                "```",
                "",
                func.docstring or "No function docstring is available yet.",
                "",
            ])

    module.page_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_class_page(module: ModuleDoc, cls: ClassDoc) -> None:
    page_path = class_page_path(module.name, cls.name)
    page_path.parent.mkdir(parents=True, exist_ok=True)
    bases = f"({', '.join(cls.bases)})" if cls.bases else ""
    lines = [
        front_matter(cls.name),
        f"# `{cls.name}`",
        "",
        f"Module: [`{module.name}`]({relative_html(page_path, module.page_path)})",
        "",
    ]
    lines.extend(overlay_block(overlay_path_for_class(module.name, cls.name)))
    lines.extend([
        "## API Summary",
        "",
        "```python",
        f"class {cls.name}{bases}",
        "```",
        "",
        cls.docstring or "No class docstring is available yet.",
        "",
    ])

    if cls.attributes:
        lines.extend(["## Attributes", ""])
        for attr in cls.attributes:
            lines.append(f"- `{attr}`")
        lines.append("")

    if cls.methods:
        lines.extend(["## Methods", ""])
        for method in cls.methods:
            lines.extend([
                f"### `{method.name}`",
                "",
                "```python",
                ("async def " if method.is_async else "def ") + method.signature,
                "```",
                "",
                method.docstring or "No method docstring is available yet.",
                "",
            ])

    page_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_sdk_index(modules: list[ModuleDoc]) -> None:
    SDK_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        front_matter("Generated SDK Reference"),
        "# Generated SDK Reference",
        "",
        "This section is generated from Python source docstrings during the GitHub Pages build.",
        "",
        "## Packages",
        "",
    ]

    for package in PACKAGES:
        page = SDK_DIR / package / "index.md"
        lines.append(f"- [`{package}`]({html_for_page(page).split('/sdk/', 1)[1]})")
    lines.append("")
    lines.extend(["## Modules", ""])
    for module in modules:
        lines.append(f"- [`{module.name}`]({html_for_page(module.page_path).split('/sdk/', 1)[1]})")

    (SDK_DIR / "index.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_sdk_nav(modules: list[ModuleDoc]) -> None:
    by_package: dict[str, list[ModuleDoc]] = {package: [] for package in PACKAGES}
    for module in modules:
        by_package[module.name.split(".", 1)[0]].append(module)

    lines = [
        '<nav class="tree-nav" data-tree-nav>',
        '  <a class="tree-home" href="{{ \'/SDK-Reference.html\' | relative_url }}">SDK Reference</a>',
        '  <a href="{{ \'/sdk/index.html\' | relative_url }}">Generated SDK Overview</a>',
    ]

    for package, package_modules in by_package.items():
        lines.extend([f"  <details>", f"    <summary>{package}</summary>"])
        for module in sorted(package_modules, key=lambda item: item.name):
            module_href = html_for_page(module.page_path)
            lines.extend([
                "    <details>",
                f"      <summary>{module.name}</summary>",
                f"      <a href=\"{{{{ '{module_href}' | relative_url }}}}\">Module</a>",
            ])
            if module.classes:
                lines.extend(["      <details>", "        <summary>Classes</summary>"])
                for cls in module.classes:
                    class_href = html_for_page(class_page_path(module.name, cls.name))
                    lines.append(f"        <a href=\"{{{{ '{class_href}' | relative_url }}}}\">{cls.name}</a>")
                    if cls.methods:
                        lines.extend(["        <details>", f"          <summary>{cls.name} methods</summary>"])
                        for method in cls.methods:
                            lines.append(
                                f"          <a href=\"{{{{ '{class_href}#{anchor(method.name)}' | relative_url }}}}\">{method.name}</a>"
                            )
                        lines.append("        </details>")
                lines.append("      </details>")
            if module.functions:
                lines.extend(["      <details>", "        <summary>Functions</summary>"])
                for func in module.functions:
                    lines.append(
                        f"        <a href=\"{{{{ '{module_href}#{anchor(func.name)}' | relative_url }}}}\">{func.name}</a>"
                    )
                lines.append("      </details>")
            lines.append("    </details>")
        lines.append("  </details>")

    lines.append("</nav>")
    SDK_NAV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def discover_modules() -> list[ModuleDoc]:
    modules: list[ModuleDoc] = []
    for package in PACKAGES:
        package_dir = SRC / package
        for path in sorted(package_dir.rglob("*.py")):
            if path.name == "__main__.py":
                continue
            modules.append(parse_module(path))
    return modules


def module_names_from_changed_paths(paths: list[str]) -> set[str]:
    module_names: set[str] = set()
    for raw in paths:
        path = (ROOT / raw).resolve()
        try:
            rel = path.relative_to(SRC)
        except ValueError:
            continue
        if path.suffix != ".py" or path.name == "__main__.py":
            continue
        package = rel.parts[0] if rel.parts else ""
        if package not in PACKAGES:
            continue
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            module_names.add(".".join(parts))
    return module_names


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate SDK reference pages for the documentation site.")
    parser.add_argument(
        "--changed",
        nargs="*",
        default=None,
        help="Only rewrite pages for these changed source files. Navigation and indexes are still refreshed.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    changed_modules = module_names_from_changed_paths(args.changed or []) if args.changed is not None else None

    if changed_modules is None and SDK_DIR.exists():
        shutil.rmtree(SDK_DIR)
    modules = discover_modules()
    existing_module_names = {module.name for module in modules}
    if changed_modules is not None:
        for deleted_module in sorted(changed_modules - existing_module_names):
            remove_existing_module_pages(deleted_module)
    write_sdk_index(modules)
    for module in modules:
        if changed_modules is not None and module.name not in changed_modules:
            continue
        remove_existing_module_pages(module.name)
        write_module_page(module)
        for cls in module.classes:
            write_class_page(module, cls)
    write_sdk_nav(modules)


if __name__ == "__main__":
    main()
