"""The operations agents call, shared by the CLI and the MCP server.

Each returns a JSON-ready dict. They are the same operations the canvas uses
(``apply_ops`` for edits, ``render_svg`` for the picture), so an agent and a
person editing in the browser cannot get different answers.

=====================  =====================================================
``project.load``       panels, nodes with positions and frame sizes, labels
``rules.check``        every finding, with the rule's source
``layout.dry_run``     apply ops in memory: findings added and resolved
``layout.apply``       the same, then write ``device_2D.lua`` / ``rebench.json``
``view.render``        a panel as PNG (headless Chromium) or SVG
=====================  =====================================================
"""

from __future__ import annotations

from pathlib import Path

from .check import check_project
from .ops import apply_ops
from .profile import load_profile
from .project import BenchProject
from .render import panel_size, render_png, render_svg

__all__ = ["project_load", "rules_check", "layout_dry_run", "layout_apply", "view_render",
           "PANELS"]

PANELS = ("front", "back", "folded_front", "folded_back")


def _panels(project: BenchProject) -> list[str]:
    return [p for p in PANELS if p in project.link.hdgui.panels]


def project_load(root: Path | str) -> dict:
    project = BenchProject.load(root)
    panels = {}
    for panel in _panels(project):
        nodes = []
        for spec in project.link.specs:
            for p in spec.placements:
                if p.panel == panel:
                    nodes.append({
                        "node": p.node, "sprite": spec.path, "kind": spec.kind,
                        "frames": spec.frames, "x": p.x, "y": p.y,
                        "w": spec.frame_w, "h": spec.frame_h,
                    })
        panels[panel] = {"size": list(panel_size(project, panel)), "nodes": nodes}
    return {
        "project": str(project.root),
        "panels": panels,
        "labels": [vars(l) for l in project.sidecar.labels],
        "sprites": {k: vars(v) for k, v in project.sidecar.sprites.items()},
    }


def rules_check(root: Path | str) -> dict:
    project = BenchProject.load(root)
    result = check_project(project, load_profile(root))
    return {
        "project": str(project.root),
        "errors": len(result.errors),
        "warnings": len(result.warnings),
        "findings": [f.to_dict() for f in result.findings],
    }


def layout_dry_run(root: Path | str, ops: list[dict]) -> dict:
    return apply_ops(root, ops, dry_run=True).to_dict()


def layout_apply(root: Path | str, ops: list[dict]) -> dict:
    return apply_ops(root, ops, dry_run=False).to_dict()


def view_render(root: Path | str, panel: str, out: Path | str | None = None,
                scale: float = 1.0, frame: int = 0, overlays: bool = True,
                ops: list[dict] | None = None) -> dict:
    """Draw a panel, after ``ops`` in memory when given (a preview of a dry run).

    With ``out`` ending in ``.svg`` the SVG is written; otherwise a PNG at
    ``scale`` (0.5 is Reason's lo-res view). Without ``out`` the SVG text is
    returned.
    """
    root = Path(root)
    profile = load_profile(root)
    result = apply_ops(root, ops or [], dry_run=True, profile=profile)
    project = result.project
    if panel not in _panels(project):
        raise ValueError(f"no {panel!r} panel; the project has {', '.join(_panels(project))}")
    findings = [f for f in result.check.findings if f.panel in (panel, "")]
    svg = render_svg(project, panel, result.check.findings, profile, frame=frame,
                     overlays=overlays)
    reply = {
        "panel": panel,
        "size": list(panel_size(project, panel)),
        "findings": [f.to_dict() for f in findings],
    }
    if out is None:
        reply["svg"] = svg
        return reply
    out = Path(out)
    if out.suffix.lower() == ".svg":
        out.write_text(svg, encoding="utf-8")
    else:
        render_png(svg, out, scale)
    reply["path"] = str(out.resolve())
    return reply
