"""The bench's own layout rules: what Reason reserves and requires on a panel.

These complement :mod:`reblend.project.validation` (which already checks
panel sizes, bounds, overlap, margins, frame contracts, cable origin and the
placeholder). Each check reads only the project files: node rectangles come
from ``device_2D.lua`` offsets plus the frame size of the sheet on disk, and
widget kinds and bindings from ``hdgui_2D.lua``. A node with no known frame
size has no rectangle and is skipped by the geometric rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..model import calibration, kinds
from .link import ProjectLink
from .lua_reader import LuaConfigError, _execute_sandboxed

from .profile import Profile

__all__ = [
    "Rect", "LayoutFinding", "node_rects", "device_type", "check_layout", "check_labels",
    "check_screws", "check_routing_icons", "check_sdk_part_sizes", "check_rules",
    "routing_hint", "routing_icon_number", "panel_height",
]

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


def check_labels(sidecar, profile: Profile) -> list[LayoutFinding]:
    """Text size: functional labels at least ``profile.min_text_px`` (SDK guideline 43 px)."""
    out = []
    for label in sidecar.labels:
        if label.functional and label.size_px < profile.min_text_px:
            out.append(LayoutFinding(
                WARNING, "text-size",
                f"label '{label.text}' is {label.size_px:g} px; functional text should be at "
                f"least {profile.min_text_px} px to stay readable (about "
                f"{label.size_px / 2:g} px in the 0.5x set)",
                subject=label.id, panel=label.panel))
    return out


# --- panel art: screws and routing icons (Skou feedback #30, #31) ----------

#: The SDK's routing icons, by number and by name. Effects show one icon for
#: mono input and one for stereo input, and every one that applies.
ROUTING_ICONS = {
    "01": "MonoInMonoOut",
    "02": "Spreading",
    "03": "TrueStereo",
    "04": "MixingSum",
    "05": "MixingCombine",
}
MONO_ICONS = frozenset({"01", "02"})
STEREO_ICONS = frozenset({"03", "04", "05"})
#: The icons that describe each ``add_stereo_effect_routing_hint`` type:
#: (mono-input choices, stereo-input choices). ``mixing_stereo`` may be drawn
#: as a sum or a combine, so either counts.
HINT_ICONS = {
    "true_stereo": ({"01"}, {"03"}),
    "spreading": ({"02"}, {"03"}),
    "mixing_stereo": ({"01"}, {"04", "05"}),
}

_ICON_NAME = re.compile(r"Routing_Icon_(\w+)", re.IGNORECASE)
_HINT = re.compile(
    r"add_stereo_effect_routing_hint\s*\{[^}]*?\btype\s*=\s*[\"'](\w+)[\"']", re.DOTALL)


def routing_hint(root: Path) -> str | None:
    """The ``type`` of ``add_stereo_effect_routing_hint`` in motherboard_def.lua."""
    path = Path(root) / "motherboard_def.lua"
    if not path.is_file():
        return None
    match = _HINT.search(path.read_text(encoding="utf-8", errors="replace"))
    return match.group(1) if match else None


def routing_icon_number(name: str) -> str | None:
    """``Routing_Icon_03`` / ``Routing_Icon_TrueStereo`` -> ``"03"``."""
    match = _ICON_NAME.search(name or "")
    if not match:
        return None
    key = match.group(1)
    if key.isdigit():
        key = f"{int(key):02d}"
        return key if key in ROUTING_ICONS else None
    for number, title in ROUTING_ICONS.items():
        if key.lower() == title.lower():
            return number
    return None


def panel_height(link: ProjectLink, panel: str) -> int:
    """The panel's height in hi-res px: its backdrop's, else the calibrated one."""
    for spec in link.specs:
        if spec.kind == kinds.BACKDROP and spec.frame_h and \
                any(p.panel == panel for p in spec.placements):
            return int(spec.frame_h)
    return calibration.panel_size_px(panel).height


def rack_hole_rows(link: ProjectLink, panel: str, profile: Profile) -> tuple[float, ...]:
    if calibration.is_folded(panel):
        return (float(profile.rack_hole_folded_y),)
    top, bottom = profile.rack_hole_back if panel == "back" else profile.rack_hole_front
    return (float(top), float(panel_height(link, panel) - bottom))


def _screws(link: ProjectLink, rects) -> list[tuple[str, str, float, float]]:
    """(panel, name, cx, cy) for every screw node and sidecar screw mark."""
    found = []
    for r in rects:
        if "screw" in r.node.lower() or "screw" in r.path.lower():
            cx, cy = r.centre
            found.append((r.panel, r.node, cx, cy))
    for mark in getattr(link.sidecar, "art", ()):
        if mark.kind == "screw":
            found.append((mark.panel, mark.name or "screw", float(mark.x), float(mark.y)))
    return found


def check_screws(link: ProjectLink, profile: Profile) -> list[LayoutFinding]:
    """Rack-ear screws sit on Reason's rack-hole rows."""
    out = []
    for panel, name, cx, cy in _screws(link, node_rects(link)):
        rows = rack_hole_rows(link, panel, profile)
        nearest = min(rows, key=lambda y: abs(y - cy))
        if abs(nearest - cy) > profile.screw_tolerance_px:
            out.append(LayoutFinding(
                WARNING, "rack-screws",
                f"screw '{name}' is centred at y {cy:g}; Reason's rack holes on the {panel} "
                f"panel are at y {', '.join(f'{y:g}' for y in rows)} "
                f"(move it {nearest - cy:+g} px)",
                subject=name, panel=panel))
    return out


def _routing_icons(link: ProjectLink) -> set[str]:
    numbers = set()
    for spec in link.specs:
        if any(p.panel == "back" for p in spec.placements):
            number = routing_icon_number(spec.path)
            if number:
                numbers.add(number)
    for mark in getattr(link.sidecar, "art", ()):
        if mark.kind == "routing_icon" and mark.panel == "back":
            number = routing_icon_number(mark.name)
            if number:
                numbers.add(number)
    return numbers


def check_routing_icons(link: ProjectLink) -> list[LayoutFinding]:
    """Effects show a mono-input and a stereo-input routing icon on the back
    panel, matching the declared routing hint."""
    if device_type(link.root) not in EFFECT_TYPES:
        return []
    shown = _routing_icons(link)
    hint = routing_hint(link.root)
    if not shown:
        return [LayoutFinding(
            WARNING, "routing-icons",
            "no Routing_Icon_* found on the back panel: place the SDK icons as nodes, or "
            "declare the ones painted into the panel art in the sidecar's \"art\" list "
            "(kind \"routing_icon\")", panel="back")]
    out = []
    names = lambda ns: " or ".join(f"Routing_Icon_{n} ({ROUTING_ICONS[n]})" for n in sorted(ns))
    if not shown & MONO_ICONS:
        out.append(LayoutFinding(
            ERROR, "routing-icons",
            f"the back panel shows no mono-input routing icon ({names(MONO_ICONS)})",
            panel="back"))
    if not shown & STEREO_ICONS:
        out.append(LayoutFinding(
            ERROR, "routing-icons",
            f"the back panel shows no stereo-input routing icon ({names(STEREO_ICONS)})",
            panel="back"))
    if hint in HINT_ICONS:
        for want, what in zip(HINT_ICONS[hint], ("mono", "stereo")):
            if shown & (MONO_ICONS if what == "mono" else STEREO_ICONS) and not shown & want:
                out.append(LayoutFinding(
                    WARNING, "routing-icons",
                    f"the routing hint is {hint}, but the {what}-input icon shown is not "
                    f"{names(want)}", panel="back"))
    return out


def check_sdk_part_sizes(link: ProjectLink) -> list[LayoutFinding]:
    """Stock parts are the SDK's hi-res files, not low-res or edited copies
    (Skou feedback #25)."""
    from . import sdk_parts

    out, seen = [], set()
    for panel, widget in _widgets(link):
        part = sdk_parts.stock_part_for_widget(widget.kind)
        node = link.device.node(panel, widget.node) if widget.node else None
        if part is None or node is None or not node.graphics:
            continue
        path = link.gui2d_dir / f"{node.graphics[0].path}.png"
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        problem = sdk_parts.check_stock_size(part, path)
        if problem and widget.kind == "device_name":
            if sdk_parts.check_stock_size(sdk_parts.VERTICAL_TAPE, path) is None:
                problem = None
        if problem:
            out.append(LayoutFinding(WARNING, "sdk-part-size", problem,
                                     subject=widget.node, panel=panel))
    return out


def check_rules(link: ProjectLink, profile: Profile | None = None) -> list[LayoutFinding]:
    """Every rule here that reads the project files alone: what RE Bench's
    ``check`` and RE-Blend's Validate both run on top of
    :func:`reblend.project.validation.validate_link`."""
    profile = profile or Profile()
    return (check_layout(link, profile) + check_labels(link.sidecar, profile)
            + check_screws(link, profile) + check_routing_icons(link)
            + check_sdk_part_sizes(link))
