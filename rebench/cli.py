"""``python -m rebench check <project>``: the rules, from the command line.

Exit status: 0 when there are no errors (no errors or warnings with
``--strict``), 1 when there are, 2 when the project cannot be read. ``--json``
prints one object agents can parse: the counts and every finding with its
rule's source.
"""

from __future__ import annotations

import argparse
import json
import sys

from reblend.project.lua_reader import LuaConfigError

from .catalogue import RULES
from .check import run_checks
from .profile import load_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rebench", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="check an RE project against the rule catalogue")
    check.add_argument("project", help="RE project root (the folder holding GUI2D/)")
    check.add_argument("--json", action="store_true", help="machine-readable output")
    check.add_argument("--strict", action="store_true", help="warnings fail too")
    sub.add_parser("rules", help="list the rule catalogue")
    args = parser.parse_args(argv)

    if args.command == "rules":
        for rule in RULES.values():
            scene = "  [Blender scene only]" if rule.scene_only else ""
            print(f"{rule.code:16} {rule.source:6} {rule.title}{scene}")
        return 0

    try:
        result = run_checks(args.project, load_profile(args.project))
    except (LuaConfigError, OSError, ValueError) as exc:
        print(f"rebench: cannot read project: {exc}", file=sys.stderr)
        return 2

    failed = bool(result.errors) or (args.strict and bool(result.warnings))
    if args.json:
        print(json.dumps({
            "project": str(result.project),
            "ok": not failed,
            "errors": len(result.errors),
            "warnings": len(result.warnings),
            "findings": [f.to_dict() for f in result.findings],
        }, indent=2))
    else:
        for finding in result.findings:
            print(finding)
        print(f"{len(result.errors)} error(s), {len(result.warnings)} warning(s)")
    return 1 if failed else 0
