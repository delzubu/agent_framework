"""CLI entry point for agent-framework-skills."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_framework_skills.installer import install, list_targets


def _cmd_install(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve() if args.target else None

    if args.list:
        targets = list_targets()
        if not targets:
            print("No known agentic directories configured.")
            return 0
        print("Known agentic skill directories:")
        for t in targets:
            status = "✓ exists" if t.exists else "✗ not found"
            print(f"  {status}  {t.label}: {t.path}")
        return 0

    results = install(target=target, dry_run=args.dry_run, force=args.force)

    if not results:
        if target:
            print(f"No skills installed: target directory does not exist: {args.target}")
        else:
            print(
                "No known agentic skill directories found. "
                "Use --target DIR to install to a specific location."
            )
        return 1

    prefix = "[dry-run] " if args.dry_run else ""
    for path, status in results:
        print(f"  {prefix}{status}: {path}")

    installed = sum(1 for _, s in results if s == "installed")
    skipped = sum(1 for _, s in results if s.startswith("skipped"))
    errors = sum(1 for _, s in results if s.startswith("error"))

    if args.dry_run:
        print(f"\n{len(results)} skill(s) would be installed (dry-run).")
    else:
        parts = []
        if installed:
            parts.append(f"{installed} installed")
        if skipped:
            parts.append(f"{skipped} skipped")
        if errors:
            parts.append(f"{errors} error(s)")
        print("\n" + ", ".join(parts) + ".")

    return 1 if errors else 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="agent-framework-skills",
        description="Install agent_framework Claude Code skills into agentic tool directories.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    install_p = sub.add_parser("install", help="Install skills into detected or specified directories.")
    install_p.add_argument(
        "--target",
        metavar="DIR",
        default=None,
        help="Install only to this directory (skips auto-discovery).",
    )
    install_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be installed without writing any files.",
    )
    install_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing skill files.",
    )
    install_p.add_argument(
        "--list",
        action="store_true",
        help="List detected agentic directories and whether each exists.",
    )

    args = parser.parse_args(argv)

    if args.command == "install":
        sys.exit(_cmd_install(args))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
