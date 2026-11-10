"""``python -m rebench``: rules, edits, screenshots and the canvas, from the command line.

``check``: exit status 0 when there are no errors (no errors or warnings with
``--strict``), 1 when there are, 2 when the project cannot be read. ``--json``
prints one object agents can parse: the counts and every finding with its
rule's source.

``apply``: run a JSON list of edit operations (see :mod:`rebench.ops`); exit 3
when the batch is refused, in which case nothing was written.

``new``: start a device from a template. ``greybox``: placeholder sprites.
``render``: draw a panel to PNG or SVG. ``serve``: the browser canvas.
``mcp``: the same operations as an MCP server on stdio.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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

    apply = sub.add_parser("apply", help="apply edit operations from a JSON file (or - for stdin)")
    apply.add_argument("project")
    apply.add_argument("ops", help="JSON file holding a list of ops, or - for stdin")
    apply.add_argument("--dry-run", action="store_true", help="check only, write nothing")
    apply.add_argument("--json", action="store_true", help="machine-readable output")

    render = sub.add_parser("render", help="draw a panel to PNG (or SVG)")
    render.add_argument("project")
    render.add_argument("--panel", default="front",
                        choices=["front", "back", "folded_front", "folded_back"])
    render.add_argument("--out", required=True, help=".png or .svg path")
    render.add_argument("--scale", type=float, default=1.0, help="0.5 for Reason's lo-res view")
    render.add_argument("--frame", type=int, default=0, help="sprite frame to show")
    render.add_argument("--no-overlays", action="store_true", help="hide reserved areas")
    render.add_argument("--ops", help="JSON ops to preview before drawing (not written)")

    new = sub.add_parser("new", help="start a new device from a template")
    new.add_argument("target", help="folder to create (must not exist or be empty)")
    new.add_argument("--name", required=True, help='device name, e.g. "Grit Distortion"')
    new.add_argument("--manufacturer", required=True)
    new.add_argument("--product-id", default="", help="default: com.<manufacturer>.<name>")
    new.add_argument("--height", type=int, default=1, help="rack units (1-9)")
    new.add_argument("--template", default="effect", choices=["effect"])
    new.add_argument("--sdk", help="SDK root, to copy the real socket/tape/placeholder art")

    grey = sub.add_parser("greybox", help="draw placeholder sprites for every missing PNG")
    grey.add_argument("project")
    grey.add_argument("--sdk", help="SDK root, to copy the real socket/tape/placeholder art")
    grey.add_argument("--dry-run", action="store_true")
    grey.add_argument("--json", action="store_true")

    serve = sub.add_parser("serve", help="open the layout canvas in a browser")
    serve.add_argument("project")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    mcp = sub.add_parser("mcp", help="MCP server on stdio for agents")
    mcp.add_argument("--project", help="default project root for tool calls")
    args = parser.parse_args(argv)

    if args.command == "rules":
        for rule in RULES.values():
            scene = "  [Blender scene only]" if rule.scene_only else ""
            print(f"{rule.code:16} {rule.source:6} {rule.title}{scene}")
        return 0

    if args.command == "mcp":
        from .mcp_server import run_stdio
        return run_stdio(default_root=args.project)
    if args.command == "serve":
        from .server import serve
        try:
            serve(args.project, args.host, args.port)
        except (LuaConfigError, OSError, ValueError) as exc:
            print(f"rebench: cannot serve project: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "apply":
        return _apply(args)
    if args.command in ("new", "greybox"):
        return _greybox_or_new(args)
    if args.command == "render":
        return _render(args)

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


def _read_ops(source: str) -> list[dict]:
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    ops = json.loads(text)
    if isinstance(ops, dict):
        ops = ops.get("ops", [])
    if not isinstance(ops, list):
        raise ValueError("ops must be a JSON list (or an object with an \"ops\" list)")
    return ops


def _apply(args) -> int:
    from .ops import OpError, apply_ops
    try:
        ops = _read_ops(args.ops)
        result = apply_ops(args.project, ops, dry_run=args.dry_run)
    except OpError as exc:
        if args.json:
            print(json.dumps({"refused": True, "reasons": exc.reasons}, indent=2))
        else:
            for reason in exc.reasons:
                print(f"refused: {reason}", file=sys.stderr)
        return 3
    except (LuaConfigError, OSError, ValueError) as exc:
        print(f"rebench: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        for line in result.applied:
            print(("would " if args.dry_run else "") + line)
        for f in result.added:
            print(f"+ {f}")
        for f in result.resolved:
            print(f"- {f}")
        state = "dry run, nothing written" if args.dry_run else (
            "written" if result.written else "no change")
        print(f"{len(result.check.errors)} error(s), {len(result.check.warnings)} warning(s); {state}")
    return 0


def _render(args) -> int:
    from .agent import view_render
    from .ops import OpError
    try:
        ops = _read_ops(args.ops) if args.ops else None
        reply = view_render(args.project, args.panel, out=args.out, scale=args.scale,
                            frame=args.frame, overlays=not args.no_overlays, ops=ops)
    except OpError as exc:
        print("refused: " + "; ".join(exc.reasons), file=sys.stderr)
        return 3
    except (LuaConfigError, OSError, ValueError) as exc:
        print(f"rebench: {exc}", file=sys.stderr)
        return 2
    print(reply["path"])
    return 0


def _greybox_or_new(args) -> int:
    from .greybox import build_greybox
    from .new import DeviceIdentity, new_device
    try:
        if args.command == "new":
            result = new_device(args.target, DeviceIdentity(
                args.name, args.manufacturer, args.product_id, args.height),
                template=args.template, sdk_root=args.sdk)
        else:
            result = build_greybox(args.project, sdk_root=args.sdk, dry_run=args.dry_run)
    except (LuaConfigError, OSError, ValueError) as exc:
        print(f"rebench: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), indent=2))
        return 0
    if args.command == "new":
        print(f"created {args.target}")
    verb = "would draw" if getattr(args, "dry_run", False) else "drew"
    print(f"{verb} {len(result.drawn)} placeholder sheet(s), copied {len(result.copied)} SDK "
          f"part(s), kept {len(result.kept)} existing")
    if args.command == "new":
        print(f"next: python3 -m rebench serve {args.target}")
    return 0
