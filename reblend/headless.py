"""Headless entry points (design §7, ROADMAP M3).

Run inside Blender, on a saved ``.blend``::

    blender -b MyDevice.blend --python <reblend>/run_cli.py -- render --dirty --strict
    blender -b MyDevice.blend --python-expr "import reblend; reblend.cli()" -- validate

``run_cli.py`` finds the package however it was installed (as a Blender
extension its module is ``bl_ext.<repo>.reblend``, so ``import reblend`` only
works for a legacy add-on install or with the repo on ``sys.path``).

Commands (everything after ``--``):

``render [--all | --dirty | --element NAME ...]``
    Render sheets. ``--dirty`` renders only the elements whose inputs or
    sheet changed since the manifest was written (``GUI2D/reblend-manifest.json``).
``validate``
    The full cross-check table.
``export-config --patch [--dry-run]``
    Write moved offsets and frame counts to ``device_2D.lua`` (patch mode).
``status``
    List every element with its dirty reason, rendering nothing.

Common options: ``--project DIR`` (overrides the scene's RE Project),
``--strict`` (warnings fail too), ``--json`` (one JSON object on stdout),
``--progress`` (one JSON line per rendered element on stdout, for a caller
that shows progress), ``--save`` (save the .blend afterwards, so the render
state and findings are kept), ``--report FILE`` (write the JSON result there).

Exit codes: 0 success, 1 findings with errors (or warnings with ``--strict``)
or a failed render, 2 bad usage or a project that cannot be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["main", "parse_args"]

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2


def _argv_after_dashes(argv: list[str]) -> list[str]:
    return argv[argv.index("--") + 1:] if "--" in argv else []


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="reblend", description="RE-Blend headless commands")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", help="RE project root (the folder holding GUI2D/)")
    common.add_argument("--strict", action="store_true", help="warnings fail too")
    common.add_argument("--json", action="store_true", help="print one JSON result")
    common.add_argument("--report", help="also write the JSON result to this file")
    common.add_argument("--save", action="store_true", help="save the .blend afterwards")
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", parents=[common], help="render sprite sheets")
    which = render.add_mutually_exclusive_group()
    which.add_argument("--all", action="store_true", help="every element (the default)")
    which.add_argument("--dirty", action="store_true", help="only stale sheets")
    which.add_argument("--element", action="append", default=[],
                       help="an element by collection name or sprite path (repeatable)")
    render.add_argument("--progress", action="store_true",
                        help="one JSON line per element on stdout")
    render.add_argument("--validate", action="store_true",
                        help="run validate after rendering; its findings count too")

    sub.add_parser("validate", parents=[common], help="run every check")
    export = sub.add_parser("export-config", parents=[common],
                            help="write layout changes to device_2D.lua")
    export.add_argument("--patch", action="store_true", required=True,
                        help="patch mode: offsets and frame counts only")
    export.add_argument("--dry-run", action="store_true", help="list the edits, write nothing")
    sub.add_parser("status", parents=[common], help="list elements and dirty reasons")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run one command; returns the exit code (does not exit)."""
    import bpy

    from . import _migrate_all, register

    argv = _argv_after_dashes(sys.argv) if argv is None else argv
    try:
        args = parse_args(argv)
    except SystemExit as exc:  # argparse already printed the message
        return EXIT_USAGE if exc.code else EXIT_OK

    if not hasattr(bpy.types.Scene, "reblend"):
        register()
    _migrate_all()  # the load-time timer never fires in a -b script run

    from .ui import operators

    context = bpy.context
    settings = context.scene.reblend
    if args.project:
        settings.project_root = str(Path(args.project).resolve()) + "/"
    try:
        root = operators._project_root(context)
    except Exception as exc:  # LuaConfigError and friends
        return _finish(args, {"command": args.command, "error": str(exc)}, EXIT_USAGE)

    handler = {
        "render": _render,
        "validate": _validate,
        "export-config": _export,
        "status": _status,
    }[args.command]
    result, code = handler(args, context, root)
    result = {"command": args.command, "project": str(root), **result}
    if args.save and bpy.data.filepath:
        bpy.ops.wm.save_mainfile()
    return _finish(args, result, code)


def _finish(args, result: dict, code: int) -> int:
    result["exit_code"] = code
    text = json.dumps(result, indent=2, default=str)
    if getattr(args, "report", None):
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    if getattr(args, "json", False):
        print(text)
    else:
        _print_human(result)
    sys.stdout.flush()
    return code


def _print_human(result: dict) -> None:
    if "error" in result:
        print(f"reblend {result['command']}: {result['error']}", file=sys.stderr)
        return
    for finding in result.get("findings", []):
        where = f" [{finding['panel']}]" if finding.get("panel") else ""
        who = f" {finding['subject']}:" if finding.get("subject") else ""
        print(f"{finding['severity'].upper()} {finding['code']}{where}{who} {finding['message']}")
    for line in result.get("lines", []):
        print(line)
    summary = result.get("summary")
    if summary:
        print(summary)


def _finding_dicts(findings) -> list[dict]:
    return [{"severity": f.severity, "code": f.code, "message": f.message,
             "subject": f.subject, "panel": f.panel} for f in findings]


def _code_for(findings: list[dict], strict: bool) -> int:
    if any(f["severity"] == "error" for f in findings):
        return EXIT_FINDINGS
    if strict and findings:
        return EXIT_FINDINGS
    return EXIT_OK


# -- commands ----------------------------------------------------------------


def _select(args, context, root):
    from .model import schema
    from .ui import operators

    every = operators._element_collections(context.scene)
    if args.element:
        wanted = set(args.element)
        chosen = [c for c in every
                  if c.name in wanted or str(c.get("re_path", "")) in wanted]
        found = {c.name for c in chosen} | {str(c.get("re_path", "")) for c in chosen}
        missing = sorted(wanted - found)
        return chosen, missing, {}
    if args.dirty:
        dirty = operators.dirty_elements(context, root, every)
        return [c for c, _r in dirty], [], {c.name: r for c, r in dirty}
    return [c for c in every if schema.is_element(c)], [], {}


def _render(args, context, root):
    from .ui import operators

    chosen, missing, reasons = _select(args, context, root)
    if missing:
        return {"error": "no element named " + ", ".join(missing)}, EXIT_USAGE

    def progress(done, total, name):
        if args.progress:
            print(json.dumps({"progress": done, "total": total, "element": name}))
            sys.stdout.flush()

    results = operators.run_render(context, chosen, root, progress=progress) if chosen else []
    findings = _finding_dicts(f for r in results for f in r.findings)
    sheets = [{"element": r.element, "ok": r.ok,
               "sheet": str(r.strip_path) if r.strip_path else None,
               "derived": [str(p) for p in r.derived_paths],
               "reason": reasons.get(r.element, "")} for r in results]
    failed = [s["element"] for s in sheets if not s["ok"]]
    code = EXIT_FINDINGS if failed else EXIT_OK
    summary = (f"rendered {len(results) - len(failed)}/{len(results)} sheet(s)"
               + (f"; failed: {', '.join(failed)}" if failed else "")
               + ("" if chosen or not args.dirty else " (nothing dirty)"))
    out = {"rendered": sheets, "findings": findings, "summary": summary,
           "render_state": context.scene.reblend.render_state}
    if args.validate:
        validation, vcode = _validate(args, context, root)
        out["validation"] = validation
        code = max(code, vcode)
    elif args.strict and findings:
        code = max(code, _code_for(findings, True))
    return out, code


def _validate(args, context, root):
    import bpy

    try:
        bpy.ops.reblend.validate("EXEC_DEFAULT")
    except RuntimeError as exc:
        return {"error": str(exc)}, EXIT_USAGE
    findings = _finding_dicts(context.scene.reblend.findings)
    errors = sum(f["severity"] == "error" for f in findings)
    summary = f"validation: {errors} error(s), {len(findings) - errors} warning(s)"
    return {"findings": findings, "summary": summary}, _code_for(findings, args.strict)


def _export(args, context, root):
    import bpy

    from .project import lua_writer
    from .project.link import load_project
    from .ui import operators

    link = load_project(root)
    edits, notes = lua_writer.compute_device_edits(
        link.device, operators._scene_elements(context))
    lines = [operators._describe_edit(edit) for edit in edits] + notes
    if args.dry_run or not edits:
        summary = f"{len(edits)} edit(s)" + (" (dry run, nothing written)" if edits else "")
        return {"edits": lines, "lines": lines, "summary": summary}, EXIT_OK
    try:
        bpy.ops.reblend.export_patch("EXEC_DEFAULT")
    except RuntimeError as exc:
        return {"edits": lines, "error": str(exc)}, EXIT_FINDINGS
    return {"edits": lines, "lines": lines,
            "summary": f"wrote {len(edits)} edit(s) to device_2D.lua"}, EXIT_OK


def _status(args, context, root):
    from .model import kinds, schema
    from .ui import operators

    every = operators._element_collections(context.scene)
    dirty = {c.name: r for c, r in operators.dirty_elements(context, root, every)}
    rows = []
    for c in every:
        data = schema.props_to_data(c)
        rows.append({"element": c.name, "path": data.path, "kind": data.kind,
                     "renders": kinds.renders_art(data.kind),
                     "dirty": dirty.get(c.name, "")})
    lines = [f"{'DIRTY' if r['dirty'] else 'ok   '} {r['path']}"
             + (f"  ({r['dirty']})" if r["dirty"] else "") for r in rows if r["renders"]]
    return {"elements": rows, "lines": lines,
            "summary": f"{len(dirty)} of {sum(r['renders'] for r in rows)} sheet(s) dirty"}, EXIT_OK
