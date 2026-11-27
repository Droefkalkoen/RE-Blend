"""Draw a panel as SVG: the art that exists, blockouts for what does not,
Reason's reserved areas, labels, and the findings on the nodes they concern.

One SVG unit is one hi-res panel pixel. The same SVG feeds the browser canvas
and the PNG screenshots agents look at (:func:`render_png`).
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Callable

from reblend.model import calibration, kinds

from .check import BenchFinding
from .layout import node_rects
from .profile import Profile
from .project import BenchProject

__all__ = ["panel_size", "render_svg", "render_png"]

COLORS = {"error": "#e5484d", "warning": "#f5a524", "ok": "#3e9bff"}


def panel_size(project: BenchProject, panel: str) -> tuple[int, int]:
    hd = project.link.hdgui.panels.get(panel)
    backdrop = hd.graphics_node if hd else None
    for spec in project.link.specs:
        if spec.kind == kinds.BACKDROP and spec.frame_w and spec.frame_h:
            if any(p.panel == panel and p.node == backdrop for p in spec.placements):
                return spec.frame_w, spec.frame_h
    if calibration.is_folded(panel):
        return calibration.PANEL_WIDTH_PX, calibration.FOLDED_HEIGHT_PX
    return calibration.PANEL_WIDTH_PX, 2 * calibration.UNIT_HEIGHT_PX


def _sprite_href_file(project: BenchProject) -> Callable[[str], str | None]:
    def href(path: str) -> str | None:
        png = project.link.gui2d_dir / f"{path}.png"
        return png.resolve().as_uri() if png.is_file() else None
    return href


def render_svg(project: BenchProject, panel: str, findings: list[BenchFinding],
               profile: Profile, href: Callable[[str], str | None] | None = None,
               frame: int = 0, overlays: bool = True) -> str:
    href = href or _sprite_href_file(project)
    w, h = panel_size(project, panel)
    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" data-panel="{panel}" font-family="Helvetica, Arial, sans-serif">',
        '<defs><pattern id="hatch" width="16" height="16" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><rect width="8" height="16" fill="rgba(229,72,77,0.28)"/>'
        '</pattern></defs>',
        f'<rect width="{w}" height="{h}" fill="#3a3f45"/>',
    ]

    # Backdrop art.
    hd = project.link.hdgui.panels.get(panel)
    backdrop = hd.graphics_node if hd else None
    for spec in project.link.specs:
        if spec.kind == kinds.BACKDROP and any(
                p.panel == panel and p.node == backdrop for p in spec.placements):
            url = href(spec.path)
            if url:
                out.append(f'<image href="{html.escape(url)}" x="0" y="0" width="{w}" height="{h}"/>')

    by_subject: dict[str, str] = {}
    for f in findings:
        if f.panel in ("", panel):
            for part in f.subject.split("+"):
                if f.severity == "error" or by_subject.get(part) != "error":
                    by_subject[part] = f.severity

    specs_by_path = {s.path: s for s in project.link.specs}
    for r in node_rects(project.link):
        if r.panel != panel:
            continue
        spec = specs_by_path[r.path]
        url = href(r.path)
        status = by_subject.get(r.node) or by_subject.get(r.path)
        out.append(f'<g class="node" data-node="{html.escape(r.node)}" data-x="{r.x:g}" '
                   f'data-y="{r.y:g}" transform="translate({r.x:g},{r.y:g})">')
        if url:
            f_idx = min(max(frame, 0), spec.frames - 1)
            out.append(f'<svg width="{r.w}" height="{r.h}" viewBox="0 {f_idx * r.h} {r.w} {r.h}" '
                       'overflow="hidden">'
                       f'<image href="{html.escape(url)}" width="{r.w}" height="{r.h * spec.frames}"/></svg>')
        else:
            shape = project.sidecar.sprites.get(r.path)
            shape = shape.shape if shape else {}
            if shape.get("type") == "knob":
                d = float(shape.get("diameter_px", min(r.w, r.h)))
                out.append(f'<circle cx="{r.w / 2:g}" cy="{r.h / 2:g}" r="{d / 2:g}" '
                           'fill="#6b7178" stroke="#c9ced3" stroke-width="3"/>')
            else:
                out.append(f'<rect width="{r.w}" height="{r.h}" rx="6" fill="#6b7178" '
                           'fill-opacity="0.7" stroke="#c9ced3" stroke-width="3"/>')
        # A transparent hit area: the canvas drags by it, whatever the art's alpha.
        out.append(f'<rect class="hit" width="{r.w}" height="{r.h}" fill="transparent"/>')
        if overlays:
            color = COLORS.get(status or "ok")
            dash = "" if status else ' stroke-dasharray="10 8"'
            width = 6 if status else 2
            out.append(f'<rect class="outline" width="{r.w}" height="{r.h}" fill="none" '
                       f'stroke="{color}" stroke-width="{width}"{dash}/>')
            out.append(f'<text x="4" y="-8" font-size="22" fill="{color}">{html.escape(r.node)}</text>')
        out.append('</g>')

    for label in project.sidecar.labels:
        if label.panel != panel:
            continue
        status = by_subject.get(label.id)
        fill = COLORS[status] if (status and overlays) else "#e8ecef"
        out.append(f'<text class="label" data-label="{html.escape(label.id)}" x="{label.x:g}" '
                   f'y="{label.y:g}" font-size="{label.size_px:g}" fill="{fill}" '
                   f'text-anchor="middle">{html.escape(label.text)}</text>')

    if overlays:
        out.append(_reserved(panel, w, h, profile))
    out.append('</svg>')
    return "\n".join(out)


def _reserved(panel: str, w: int, h: int, profile: Profile) -> str:
    parts = ['<g class="reserved" pointer-events="none">']
    m = calibration.EDGE_MARGIN_PX
    for x in (0, w - m):
        parts.append(f'<rect x="{x}" y="0" width="{m}" height="{h}" fill="url(#hatch)"/>')
    if panel == "front":
        parts.append(f'<line x1="{profile.ear_left_px}" y1="0" x2="{profile.ear_left_px}" y2="{h}" '
                     'stroke="#f5a524" stroke-width="2" stroke-dasharray="12 10"/>')
        parts.append(f'<line x1="{profile.ear_right_px}" y1="0" x2="{profile.ear_right_px}" y2="{h}" '
                     'stroke="#f5a524" stroke-width="2" stroke-dasharray="12 10"/>')
    fx, fy, fw, fh = (profile.fold_arrow_folded if calibration.is_folded(panel)
                      else profile.fold_arrow_full)
    parts.append(f'<rect x="{fx}" y="{fy}" width="{fw}" height="{fh}" fill="url(#hatch)" '
                 'stroke="#e5484d" stroke-width="2"/>')
    cx, cy = fx + fw / 2, fy + fh / 2
    tri = (f"{cx - 18:g},{cy - 10:g} {cx + 18:g},{cy - 10:g} {cx:g},{cy + 14:g}"
           if not calibration.is_folded(panel) else
           f"{cx - 10:g},{cy - 18:g} {cx - 10:g},{cy + 18:g} {cx + 14:g},{cy:g}")
    parts.append(f'<polygon points="{tri}" fill="none" stroke="#e5484d" stroke-width="3"/>')
    parts.append('</g>')
    return "".join(parts)


def render_png(svg: str, out: Path | str, scale: float = 1.0) -> Path:
    """Rasterise an SVG with the pre-installed headless Chromium (Playwright)."""
    from playwright.sync_api import sync_playwright  # optional dependency

    out = Path(out)
    import re
    vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    w, h = int(vb.group(1)), int(vb.group(2))
    page_w, page_h = max(1, round(w * scale)), max(1, round(h * scale))
    sized = svg.replace(f'width="{w}" height="{h}"', f'width="{page_w}" height="{page_h}"', 1)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        page_file = Path(tmp) / "panel.html"
        page_file.write_text(
            "<!doctype html><html><body style='margin:0;background:#222'>" + sized + "</body></html>",
            encoding="utf-8")
        with sync_playwright() as pw:
            browser = _launch(pw)
            page = browser.new_page(viewport={"width": page_w, "height": page_h})
            page.goto(page_file.as_uri())
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": page_w, "height": page_h})
            browser.close()
    return out


def _launch(pw):
    """Chromium from Playwright, or from ``REBENCH_CHROMIUM`` when Playwright's
    own browser download is absent (a machine with a system Chromium)."""
    import os
    args = ["--allow-file-access-from-files"]
    exe = os.environ.get("REBENCH_CHROMIUM")
    if exe:
        return pw.chromium.launch(executable_path=exe, args=args)
    try:
        return pw.chromium.launch(args=args)
    except Exception:
        fallback = "/opt/pw-browsers/chromium"
        if os.path.exists(fallback):
            return pw.chromium.launch(executable_path=fallback, args=args)
        raise
