"""The bench's own layout rules: what Reason reserves and requires on a panel.

These complement :mod:`reblend.project.validation` (which already checks
panel sizes, bounds, overlap, margins, frame contracts, cable origin and the
placeholder). Each check reads only the project files: node rectangles come
from ``device_2D.lua`` offsets plus the frame size of the sheet on disk, and
widget kinds and bindings from ``hdgui_2D.lua``. A node with no known frame
size has no rectangle and is skipped by the geometric rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from reblend.model import calibration, kinds
from reblend.project.link import ProjectLink
from reblend.project.lua_reader import LuaConfigError, _execute_sandboxed

from .profile import Profile

__all__ = ["Rect", "LayoutFinding", "node_rects", "device_type", "check_layout"]

ERROR = "error"
WARNING = "warning"

#: device_type values that are effects (SDK info.lua). Effects need the
#: on/off/bypass control in the top-left corner.
EFFECT_TYPES = frozenset({"creative_fx", "studio_fx"})
BYPASS_PROPERTY = "/custom_properties/builtin_onoffbypass"

SOCKET_WIDGETS = frozenset({
    "audio_input_socket", "audio_output_socket", "cv_input_socket", "cv_output_socket",
})
#: Stock sprite frame counts. Every SDK example declares its SharedAudioJack /
#: SharedCVJack strips with frames = 3 (Skou feedback #9).
SOCKET_FRAMES = 3
DISPLAY_PANELS = frozenset({"front", "folded_front"})


@dataclass(frozen=True)
class Rect:
    panel: str
    node: str
    path: str
    kind: str
    x: float
    y: float
    w: int
    h: int

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def centre(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def intersects(self, box: tuple[float, float, float, float]) -> bool:
        bx, by, bw, bh = box
        return self.x < bx + bw and bx < self.right and self.y < by + bh and by < self.bottom


@dataclass(frozen=True)
class LayoutFinding:
    severity: str
    code: str
    message: str
    subject: str = ""
    panel: str = ""


def node_rects(link: ProjectLink) -> list[Rect]:
    """Every placed, sized, non-backdrop node as a panel rectangle."""
    rects = []
    for spec in link.specs:
        if spec.kind == kinds.BACKDROP or not (spec.frame_w and spec.frame_h):
            continue
        for p in spec.placements:
            rects.append(Rect(p.panel, p.node, spec.path, spec.kind, p.x, p.y,
                              spec.frame_w, spec.frame_h))
    return rects


def device_type(root: Path) -> str | None:
    """``device_type`` from ``info.lua``, or None when it cannot be read."""
    path = Path(root) / "info.lua"
    if not path.is_file():
        return None
    try:
        value = _execute_sandboxed(path).get("device_type")
    except LuaConfigError:
        return None
    return value if isinstance(value, str) else None


def _widgets(link: ProjectLink) -> Iterable[tuple[str, object]]:
    for panel_name, panel in link.hdgui.panels.items():
        for widget in panel.widgets:
            yield panel_name, widget


def check_layout(link: ProjectLink, profile: Profile) -> list[LayoutFinding]:
    out: list[LayoutFinding] = []
    rects = node_rects(link)
    by_node = {(r.panel, r.node): r for r in rects}
    widget_kinds: dict[tuple[str, str], set[str]] = {}
    for panel_name, widget in _widgets(link):
        if widget.node:
            widget_kinds.setdefault((panel_name, widget.node), set()).add(widget.kind)

    _fold_arrow(out, rects, profile)
    _rack_ears(out, rects, widget_kinds, profile)
    _bypass_top_left(out, link, by_node, profile)
    _device_name(out, link)
    _panel_placement(out, link)
    _socket_frames(out, link)
    return out


def _fold_arrow(out, rects, profile: Profile) -> None:
    severity = ERROR if profile.fold_arrow_measured else WARNING
    note = "" if profile.fold_arrow_measured else (
        " (keep-out position is provisional until measured in Recon)")
    for r in rects:
        box = profile.fold_arrow_folded if calibration.is_folded(r.panel) else profile.fold_arrow_full
        if r.intersects(box):
            out.append(LayoutFinding(
                severity, "fold-arrow",
                f"node '{r.node}' ({r.x:g}, {r.y:g}, {r.w}x{r.h}) overlaps Reason's fold/unfold "
                f"arrow keep-out {box}{note}; move it right of x = {box[0] + box[2]} "
                f"or below y = {box[1] + box[3]}",
                subject=r.node, panel=r.panel))


def _rack_ears(out, rects, widget_kinds, profile: Profile) -> None:
    allowed = set(profile.ear_art_widgets)
    for r in rects:
        if r.panel != "front":
            continue
        if widget_kinds.get((r.panel, r.node), set()) & allowed:
            continue
        if r.x < profile.ear_left_px or r.right > profile.ear_right_px:
            side = "left" if r.x < profile.ear_left_px else "right"
            out.append(LayoutFinding(
                WARNING, "rack-ear",
                f"node '{r.node}' spans x {r.x:g}..{r.right:g}, onto the {side} rack ear "
                f"(ear art only at x < {profile.ear_left_px} or x > {profile.ear_right_px})",
                subject=r.node, panel=r.panel))


def _bypass_top_left(out, link: ProjectLink, by_node, profile: Profile) -> None:
    if device_type(link.root) not in EFFECT_TYPES:
        return
    for panel in ("front", "folded_front"):
        hd = link.hdgui.panels.get(panel)
        if hd is None:
            continue
        bound = [w for w in hd.widgets if w.value == BYPASS_PROPERTY]
        if not bound:
            out.append(LayoutFinding(
                ERROR, "bypass-top-left",
                f"effect devices need an on/off/bypass control bound to {BYPASS_PROPERTY} "
                f"on the {panel} panel", panel=panel))
            continue
        rect = by_node.get((panel, bound[0].node))
        if rect is None:
            continue
        box_h = calibration.FOLDED_HEIGHT_PX if calibration.is_folded(panel) else profile.bypass_box_h_full
        cx, cy = rect.centre
        if cx > profile.bypass_box_w or cy > box_h:
            out.append(LayoutFinding(
                ERROR, "bypass-top-left",
                f"the on/off/bypass control '{rect.node}' is centred at ({cx:g}, {cy:g}); "
                f"effects need it in the top-left corner (centre within "
                f"{profile.bypass_box_w} x {box_h} px)",
                subject=rect.node, panel=panel))


def _device_name(out, link: ProjectLink) -> None:
    for panel in link.device.required_panels:
        hd = link.hdgui.panels.get(panel)
        if hd is None:
            continue  # panel-missing already reports it
        if not any(w.kind == "device_name" for w in hd.widgets):
            out.append(LayoutFinding(
                ERROR, "device-name",
                f"the {panel} panel has no device_name tape; it must appear on every panel",
                panel=panel))


def _panel_placement(out, link: ProjectLink) -> None:
    for panel, widget in _widgets(link):
        if widget.kind in SOCKET_WIDGETS and panel != "back":
            out.append(LayoutFinding(
                ERROR, "socket-panel",
                f"{widget.kind} '{widget.node}' is on the {panel} panel; sockets may only "
                "be used on the back panel", subject=widget.node or "", panel=panel))
        if widget.kind == "custom_display" and panel not in DISPLAY_PANELS:
            out.append(LayoutFinding(
                ERROR, "display-panel",
                f"custom_display '{widget.node}' is on the {panel} panel; custom displays "
                "are only allowed on the front and folded front", subject=widget.node or "",
                panel=panel))


def _socket_frames(out, link: ProjectLink) -> None:
    for panel, widget in _widgets(link):
        if widget.kind not in SOCKET_WIDGETS or not widget.node:
            continue
        node = link.device.node(panel, widget.node)
        if node is None or not node.graphics:
            continue
        frames = node.graphics[0].frames
        if frames != SOCKET_FRAMES:
            out.append(LayoutFinding(
                WARNING, "sdk-part-frames",
                f"socket node '{widget.node}' declares frames = {frames}; the SDK socket "
                f"sprites are {SOCKET_FRAMES}-frame strips", subject=widget.node, panel=panel))
