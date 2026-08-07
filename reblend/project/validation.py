"""The validation report: the full cross-check table of design §6.3.

Everything RE-Blend promises — art, Lua, and rig that cannot disagree — is
enforced here as explicit checks over the parsed config, the scene's
elements, and the sheets on disk. The engine is pure: the Blender side turns
element collections into :class:`~reblend.model.schema.ElementData` and a
:class:`SceneInfo`, tests build them directly, and (in M3) the headless CLI
exits non-zero when :attr:`Report.errors` is non-empty.

Render-time pixel checks (alpha classification, per-frame overflow) live in
:mod:`reblend.render.validators` and are merged into the same report by the
render queue — one list, one place to look.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..model import calibration, kinds, schema, state_tables
from ..render.validators import check_frame_bounds
from . import link as link_mod
from .lua_reader import PANELS, Device2D, Graphic, HDGui2D, Node2D
from .png_meta import PngError, PngMeta, read_png_meta

__all__ = [
    "ERROR",
    "WARNING",
    "Finding",
    "Report",
    "SceneInfo",
    "validate_project",
    "validate_link",
]

ERROR = "error"
WARNING = "warning"

#: The only view transform that keeps palette hex values intact (§5.2).
STANDARD_VIEW_TRANSFORM = "Standard"

#: Movement below this many panel pixels cannot strand a baked shadow
#: visibly — the sheet is authored in whole pixels, so sub-pixel drift (a
#: rotating rotor's bounding box breathing, say) is not a finding.
TRAVEL_TOLERANCE_PX = 1.0


@dataclass(frozen=True)
class Finding:
    """One validation result. ``subject`` is a node or sprite path name."""

    severity: str
    code: str
    message: str
    subject: str = ""
    panel: str = ""

    def __str__(self) -> str:
        where = f" [{self.panel}]" if self.panel else ""
        who = f" {self.subject}:" if self.subject else ""
        return f"{self.severity.upper()}{where}{who} {self.message}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, severity: str, code: str, message: str, subject: str = "", panel: str = "") -> None:
        self.findings.append(Finding(severity, code, message, subject, panel))


@dataclass(frozen=True)
class SceneInfo:
    """Scene-level facts only Blender knows; None = not available (headless tests)."""

    view_transform: str | None = None


def validate_link(
    link: link_mod.ProjectLink,
    elements: Sequence[schema.ElementData],
    scene: SceneInfo | None = None,
) -> Report:
    """Validate a scene's elements against a loaded project."""
    return validate_project(
        device=link.device,
        hdgui=link.hdgui,
        elements=elements,
        gui2d_dir=link.gui2d_dir,
        property_steps=link.property_steps,
        scene=scene,
    )


def validate_project(
    device: Device2D,
    hdgui: HDGui2D,
    elements: Sequence[schema.ElementData],
    gui2d_dir: Path | None = None,
    property_steps: Mapping[str, int] | None = None,
    scene: SceneInfo | None = None,
) -> Report:
    report = Report()
    by_path = {element.path: element for element in elements}
    lua_graphics = list(_iter_graphics(device))
    lua_paths = {graphic.path for _, _, graphic in lua_graphics}

    _check_duplicate_paths(report, elements)
    _check_art_coverage(report, lua_graphics, by_path, lua_paths, elements)
    _check_widget_links(report, device, hdgui)
    _check_frame_contracts(report, device, hdgui, dict(property_steps or {}))
    _check_panel_requirements(report, device, hdgui)
    _check_kinds(report, device, hdgui, elements)
    _check_placement_drift(report, elements)
    _check_shadow_owner(report, elements)
    _check_state_tables(report, elements)
    _check_rotors(report, elements)
    _check_frame_geometry(report, elements)
    if gui2d_dir is not None:
        _check_files(report, elements, gui2d_dir)
    _check_layout(report, elements, gui2d_dir, hdgui)

    if scene is not None and scene.view_transform is not None:
        if scene.view_transform != STANDARD_VIEW_TRANSFORM:
            report.add(
                WARNING,
                "view-transform",
                f"scene view transform is '{scene.view_transform}', expected "
                f"'{STANDARD_VIEW_TRANSFORM}' — palette colours will shift in the file",
            )
    return report


# ---------------------------------------------------------------------------
# individual checks
# ---------------------------------------------------------------------------


def _iter_graphics(device: Device2D) -> Iterable[tuple[str, Node2D, Graphic]]:
    for panel in PANELS:
        for root in device.panels.get(panel, {}).values():
            for node in root.walk():
                for graphic in node.graphics:
                    yield panel, node, graphic


def _check_duplicate_paths(
    report: Report, elements: Sequence[schema.ElementData]
) -> None:
    """Two collections sharing one sprite path make every by-path lookup
    ambiguous — duplicating a collection in Blender copies its ``re_*``
    properties, and render/export/sync would each silently pick one copy."""
    counts: dict[str, int] = {}
    for element in elements:
        if element.path:
            counts[element.path] = counts.get(element.path, 0) + 1
    for path in sorted(p for p, count in counts.items() if count > 1):
        report.add(
            WARNING,
            "duplicate-path",
            f"{counts[path]} RE Element collections share sprite path "
            f"'{path}' — renders, sync and export silently use only one of "
            "them; delete or re-path the copies",
            subject=path,
        )


def _check_art_coverage(
    report: Report,
    lua_graphics: list[tuple[str, Node2D, Graphic]],
    by_path: Mapping[str, schema.ElementData],
    lua_paths: set[str],
    elements: Sequence[schema.ElementData],
) -> None:
    missing_seen: set[str] = set()
    for panel, node, graphic in lua_graphics:
        element = by_path.get(graphic.path)
        if element is None:
            if graphic.path not in missing_seen:
                missing_seen.add(graphic.path)
                report.add(
                    ERROR,
                    "missing-art",
                    f"device_2D node '{node.name}' needs sheet '{graphic.path}.png' "
                    "but no RE Element in the scene produces it",
                    subject=graphic.path,
                    panel=panel,
                )
        elif element.frames != graphic.frames:
            report.add(
                ERROR,
                "frame-count",
                f"element renders {element.frames} frames but device_2D node "
                f"'{node.name}' declares frames = {graphic.frames}",
                subject=graphic.path,
                panel=panel,
            )

    for element in elements:
        if element.path not in lua_paths:
            report.add(
                WARNING,
                "orphan-element",
                f"RE Element '{element.path}' has no node in device_2D.lua — "
                "its sheet would render but never be used",
                subject=element.path,
            )


def _check_widget_links(report: Report, device: Device2D, hdgui: HDGui2D) -> None:
    # RE2DRender enforces this link at render time (M0 finding 4); catching it
    # here means catching it before a render is wasted.
    for panel_name, panel in hdgui.panels.items():
        for widget in panel.widgets:
            node = widget.node
            if node and device.node(panel_name, node) is None:
                report.add(
                    ERROR,
                    "widget-node",
                    f"hdgui_2D {widget.kind} points at node '{node}' which does "
                    "not exist in device_2D.lua",
                    subject=node,
                    panel=panel_name,
                )


def _check_frame_contracts(
    report: Report, device: Device2D, hdgui: HDGui2D, steps: dict[str, int]
) -> None:
    """Enforce each widget's documented animation frame count.

    Two distinct rules, from the SDK 4.6.0 scripting specification:

    - Most animated widgets have a *fixed* frame count that has nothing to do
      with the bound property (a ``radio_button`` is two frames whether its
      property has 2 steps or 12; an ``up_down_button`` is three). Getting
      this wrong makes the control misbehave silently, so it is an error.
    - A ``sequence_fader`` bakes the handle's whole travel, so on a stepped
      property its frame count should equal the property's ``steps``. That
      one stays a warning: the fader may legitimately be driven by a
      continuous property, or by ``value_switch``/``values`` rather than a
      single ``value``, and the motherboard read is best-effort.
    """
    for panel_name, panel in hdgui.panels.items():
        for widget in panel.widgets:
            if not widget.node:
                continue
            node = device.node(panel_name, widget.node)
            if node is None:
                continue  # already reported by _check_widget_links

            rule = kinds.frame_rule_for_widget(widget.kind)
            if rule is None:
                continue

            if not rule.permits(node.frames):
                note = f" ({rule.note})" if rule.note else ""
                report.add(
                    ERROR,
                    "widget-frames",
                    f"{widget.kind} on node '{widget.node}' declares "
                    f"{node.frames} frames but the SDK requires "
                    f"{rule.describe()}{note}",
                    subject=widget.node,
                    panel=panel_name,
                )

            if not rule.steps_bound or not steps or not widget.value:
                continue
            declared = steps.get(widget.value)
            if declared is not None and node.frames != declared:
                report.add(
                    WARNING,
                    "steps",
                    f"{widget.kind} on node '{widget.node}' has {node.frames} frames "
                    f"but its property {widget.value} has {declared} steps — a fader "
                    "bakes one frame per handle position, so a stepped property "
                    "wants one frame per step",
                    subject=widget.node,
                    panel=panel_name,
                )


def _check_panel_requirements(report: Report, device: Device2D, hdgui: HDGui2D) -> None:
    """Structural requirements the GUI design guidelines impose on every device.

    RE2DRender catches some of these (a missing panel aborts the read); the
    rest are submission-review requirements it happily lets through, which is
    exactly the silent-failure class RE-Blend exists to close.
    """
    required = device.required_panels
    for panel in required:
        if panel not in device.panels:
            report.add(
                ERROR,
                "panel-missing",
                f"device_2D.lua declares no '{panel}' panel",
                panel=panel,
            )
        if panel not in hdgui.panels:
            report.add(
                ERROR,
                "panel-missing",
                f"hdgui_2D.lua declares no '{panel}' panel",
                panel=panel,
            )

    if device.is_player:
        for panel in ("folded_front", "folded_back"):
            if panel in device.panels or panel in hdgui.panels:
                report.add(
                    WARNING,
                    "player-folded",
                    f"device declares panel_type = 'note_player' but still defines "
                    f"'{panel}' — Players have no folded panels",
                    panel=panel,
                )
        return

    for panel_name, panel in hdgui.panels.items():
        origin = panel.cable_origin_node
        if panel_name == "folded_back":
            if origin is None:
                report.add(
                    ERROR,
                    "cable-origin",
                    "the folded back panel must declare cable_origin = { node = ... } "
                    "naming a point node in device_2D.lua",
                    panel=panel_name,
                )
            elif device.node(panel_name, origin) is None:
                report.add(
                    ERROR,
                    "cable-origin",
                    f"cable_origin names node '{origin}', which does not exist in "
                    "device_2D.lua",
                    subject=origin,
                    panel=panel_name,
                )
        elif origin is not None:
            report.add(
                ERROR,
                "cable-origin",
                "cable_origin must appear only on the folded back panel",
                subject=origin,
                panel=panel_name,
            )

    back = hdgui.panels.get("back")
    if back is not None and not any(w.kind == "placeholder" for w in back.widgets):
        report.add(
            ERROR,
            "placeholder",
            "the back panel must declare a jbox.placeholder — Reason reserves "
            f"{calibration.PLACEHOLDER_SIZE_PX[0]}x{calibration.PLACEHOLDER_SIZE_PX[1]} "
            "px there for future controls",
            panel="back",
        )


def _check_kinds(
    report: Report,
    device: Device2D,
    hdgui: HDGui2D,
    elements: Sequence[schema.ElementData],
) -> None:
    # Re-deriving the specs reuses the exact import-time logic, so "what kind
    # should this element be" can never drift between import and validation.
    expected = {spec.path: spec.kind for spec in link_mod.derive_specs(device, hdgui)}
    for element in elements:
        want = expected.get(element.path)
        if want is not None and element.kind != want:
            report.add(
                WARNING,
                "kind",
                f"element kind is '{element.kind}' but its hdgui_2D widgets imply "
                f"'{want}' — the rig may not match what Reason does with the sheet",
                subject=element.path,
            )


def _check_placement_drift(
    report: Report, elements: Sequence[schema.ElementData]
) -> None:
    """Report elements the designer has moved but not exported.

    A registration empty *is* the element's position (§6.1), so dragging one is
    a real change to the layout — but it lives only in the scene until an
    export writes it into ``device_2D.lua``. Without this the two can disagree
    indefinitely and nothing says so: the sheet renders from the scene, Reason
    places it from the Lua, and the device is subtly mis-laid-out.
    """
    for element in elements:
        for stored, derived in element.moved:
            dx = derived.x - stored.x
            dy = derived.y - stored.y
            report.add(
                WARNING,
                "moved",
                f"moved {dx:+.0f}, {dy:+.0f} px in the scene: "
                f"({derived.x:.0f}, {derived.y:.0f}) vs "
                f"({stored.x:.0f}, {stored.y:.0f}) in device_2D.lua — "
                "Export Layout writes it, Re-import & Reposition discards it",
                subject=element.path,
                panel=stored.panel,
            )


def _check_shadow_owner(
    report: Report, elements: Sequence[schema.ElementData]
) -> None:
    """Catch art that moves while its shadow is nailed to the panel (§5.1).

    An element that hands its shadow to the background is promising the
    shadow can be baked once and stay put, which is only true while the art
    holds still relative to the panel. Break that promise and nothing
    complains: RE2DRender compiles it, Reason draws it, and the control
    simply drags a shadow that is in the wrong place — exactly the
    silent-failure class RE-Blend exists to close.

    Severity follows the axis the movement is on, because they fail
    differently:

    - **Across the camera plane** is an error. The art slides over the panel
      and leaves the baked shadow behind at frame 0's position; there is no
      lighting setup that makes that read correctly.
    - **Along the camera axis** is a warning. The art only moves towards or
      away from the viewer, so the shadow shifts and softens rather than
      being stranded. On a button cap's few pixels of travel a baked shadow
      is usually the right call — but whether the shift shows is a judgement
      about the scene's lights, which is the designer's to make, not ours.

    Elements that own their shadow are never flagged: moving is precisely
    what that setting is for. Elements the scene has not measured
    (``frame_travel is None``) are skipped rather than assumed still.
    """
    for element in elements:
        travel = element.frame_travel
        if travel is None or element.shadow_owner != kinds.SHADOW_BACKGROUND:
            continue
        if travel.across > TRAVEL_TOLERANCE_PX:
            report.add(
                ERROR,
                "shadow-owner",
                f"geometry slides {travel.across:.0f} px across the panel "
                f"between frames, but its shadow is baked into the background "
                "— the baked shadow stays where frame 0 put it while the art "
                "moves away from it. Set Shadow to 'This Element' so the "
                "shadow travels in the sheet",
                subject=element.path,
            )
        elif travel.depth > TRAVEL_TOLERANCE_PX:
            report.add(
                WARNING,
                "shadow-owner",
                f"geometry moves {travel.depth:.0f} px along the camera axis "
                f"between frames (towards or away from the viewer) with its "
                "shadow baked into the background — the real shadow would "
                "shift and soften, the baked one cannot. Usually fine for a "
                "button cap; set Shadow to 'This Element' if the shift shows",
                subject=element.path,
            )


def _check_state_tables(report: Report, elements: Sequence[schema.ElementData]) -> None:
    """Check the rig side of a multi-state element: does it *differ* per frame?

    Frame counts and widget contracts are checked elsewhere; this looks at
    whether the art those frames will contain is actually distinct, which is
    the failure the SDK tools cannot see at all. A fader with no state table
    renders three identical frames and RE2DRender compiles them happily.
    """
    for element in elements:
        if kinds.rig_for_kind(element.kind) != kinds.RIG_STATES:
            continue

        if not element.states:
            if element.frames > 1:
                report.add(
                    WARNING,
                    "states",
                    f"'{element.kind}' element has {element.frames} frames but no "
                    "state table — every frame would render identically",
                    subject=element.path,
                )
            continue

        try:
            table = state_tables.StateTable.from_json(element.states)
        except ValueError as exc:
            report.add(ERROR, "states", f"state table is unreadable: {exc}",
                       subject=element.path)
            continue

        if table.frames != element.frames:
            report.add(
                ERROR,
                "states",
                f"state table has {table.frames} states but the element declares "
                f"{element.frames} frames — the rig cannot fill the sheet",
                subject=element.path,
            )
        if not table.channels() and element.frames > 1:
            report.add(
                WARNING,
                "states",
                "state table names its states but carries no actions — every "
                "frame would render identically",
                subject=element.path,
            )

        # Only a fader's handle is required to travel a constant distance per
        # frame (SDK scripting specification); other kinds move freely.
        if element.kind != kinds.FADER_HANDLE:
            continue
        for channel, values in table.uneven_travel_channels():
            spacing = ", ".join(f"{v:.4f}" for v in values)
            report.add(
                WARNING,
                "travel",
                f"{state_tables.describe_channel(channel)} is not evenly spaced "
                f"({spacing}) — a sequence_fader's handle must travel the same "
                "distance between every frame; use Spread Between Extremes",
                subject=element.path,
            )


def _check_rotors(report: Report, elements: Sequence[schema.ElementData]) -> None:
    """The knob counterpart of the state-table check: is anything rigged to
    spin? A knob with no recorded rotor has no rotation driver, so all of its
    frames render identically — and nothing downstream can tell, because a
    61-frame sheet of identical frames is structurally valid everywhere.

    ``rotor is None`` means the source never recorded one (spec-derived data
    has no scene to ask — the ``frame_travel`` convention) and stays quiet;
    ``""`` means the element was read and no rotor is recorded yet.
    """
    for element in elements:
        if kinds.rig_for_kind(element.kind) != kinds.RIG_DRIVER:
            continue
        if element.rotor is None or element.rotor:
            continue
        if element.frames > 1:
            report.add(
                WARNING,
                "rotor",
                "no rotor recorded — pick the rotating part in the Rig panel "
                "and Generate Rig, or every frame renders identically",
                subject=element.path,
            )


def _check_frame_geometry(report: Report, elements: Sequence[schema.ElementData]) -> None:
    for element in elements:
        if not element.has_frame_size:
            report.add(
                WARNING,
                "frame-size",
                "per-frame pixel size not set yet — set it before rendering",
                subject=element.path,
            )
            continue
        for problem in check_frame_bounds(element.frame_w, element.frame_h, element.frames):
            report.add(ERROR, "frame-bounds", problem, subject=element.path)


def _check_files(
    report: Report, elements: Sequence[schema.ElementData], gui2d_dir: Path
) -> None:
    try:
        actual_names = {entry.name for entry in gui2d_dir.iterdir() if entry.is_file()}
    except OSError:
        actual_names = set()

    for name in sorted(actual_names):
        if name.lower().endswith("-reframed.png"):
            # RE2DRender reframed a sheet it was given (M0 finding 6): the
            # authored pixels were NOT used and registration is broken.
            report.add(
                ERROR,
                "reframed",
                f"RE2DRender wrote '{name}' — a sheet had unsupported frame bounds "
                "and was silently reframed; fix the frame size and re-render",
                subject=name,
            )

    by_fold = {name.casefold(): name for name in actual_names}
    for element in elements:
        expected = f"{element.path}.png"
        if expected in actual_names:
            _check_png(report, element, gui2d_dir / expected)
        elif expected.casefold() in by_fold:
            report.add(
                ERROR,
                "case",
                f"sheet on disk is named '{by_fold[expected.casefold()]}' but the Lua "
                f"path says '{expected}' — case mismatch breaks case-sensitive builds",
                subject=element.path,
            )
        elif kinds.is_sdk_supplied(element.kind):
            report.add(
                WARNING,
                "png-missing",
                f"'{expected}' not found in GUI2D — this part's art comes from the "
                "SDK, not from a render (RE-Blend > Install SDK Parts)",
                subject=element.path,
            )
        elif not kinds.renders_art(element.kind):
            report.add(
                WARNING,
                "png-missing",
                f"'{expected}' not found in GUI2D — Reason draws this widget's "
                "contents itself and uses the graphics only as a bounding box, so "
                "the sheet just needs to be the right size",
                subject=element.path,
            )
        else:
            report.add(
                WARNING,
                "png-missing",
                f"'{expected}' not found in GUI2D (expected until first render)",
                subject=element.path,
            )


def _check_png(report: Report, element: schema.ElementData, path: Path) -> None:
    try:
        meta = read_png_meta(path)
    except PngError as exc:
        report.add(ERROR, "png-dims", f"unreadable PNG: {exc}", subject=element.path)
        return
    if element.has_frame_size:
        want_w = element.frame_w
        want_h = element.frame_h * element.frames
        if (meta.width, meta.height) != (want_w, want_h):
            report.add(
                ERROR,
                "png-dims",
                f"sheet is {meta.width}x{meta.height} but {element.frames} frames of "
                f"{element.frame_w}x{element.frame_h} require {want_w}x{want_h}",
                subject=element.path,
            )
    if not meta.is_8bit_rgba:
        report.add(
            WARNING,
            "png-format",
            f"sheet is not 8-bit RGBA (bit depth {meta.bit_depth}, "
            f"colour type {meta.color_type}) — the SDK expects 8-bit straight-alpha RGBA",
            subject=element.path,
        )


@dataclass(frozen=True)
class _Rect:
    """One placed element's rectangle plus what validation needs to judge it."""

    path: str
    node: str
    x: float
    y: float
    w: int
    h: int
    kind: str
    #: True when a static_decoration binds this node — those are explicitly
    #: allowed to sit under other widgets (that is how the SDK's hideable
    #: widgets get a background).
    decoration: bool = False
    #: True when every widget on the node has a visibility_switch, so it can
    #: be hidden while a neighbour is shown.
    hideable: bool = False

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def intersects(self, other: "_Rect") -> bool:
        return (
            self.x < other.right
            and other.x < self.right
            and self.y < other.bottom
            and other.y < self.bottom
        )


def _node_widget_index(hdgui: HDGui2D | None) -> dict[tuple[str, str], list]:
    """(panel, node) -> the widgets bound to it."""
    index: dict[tuple[str, str], list] = {}
    if hdgui is None:
        return index
    for panel_name, panel in hdgui.panels.items():
        for widget in panel.widgets:
            if widget.node:
                index.setdefault((panel_name, widget.node), []).append(widget)
    return index


def _check_layout(
    report: Report,
    elements: Sequence[schema.ElementData],
    gui2d_dir: Path | None,
    hdgui: HDGui2D | None = None,
) -> None:
    panel_sizes = _panel_sizes(elements, gui2d_dir)
    widget_index = _node_widget_index(hdgui)

    rects: dict[str, list[_Rect]] = {}
    for element in elements:
        if element.kind == kinds.BACKDROP or not element.has_frame_size:
            continue
        # The layout to check is the one the designer can see: an element they
        # have dragged is validated where it now sits, not where the Lua still
        # thinks it is. The drift itself is reported separately.
        for placement in element.effective_placements:
            bound = widget_index.get((placement.panel, placement.node), [])
            rects.setdefault(placement.panel, []).append(
                _Rect(
                    path=element.path,
                    node=placement.node,
                    x=placement.x,
                    y=placement.y,
                    w=element.frame_w,
                    h=element.frame_h,
                    kind=element.kind,
                    decoration=any(w.kind == "static_decoration" for w in bound),
                    hideable=bool(bound)
                    and all("visibility_switch" in w.attrs for w in bound),
                )
            )

    for panel, panel_rects in rects.items():
        size = panel_sizes.get(panel)
        if size is not None:
            _check_panel_bounds(report, panel, panel_rects, size)
        for i, a in enumerate(panel_rects):
            for b in panel_rects[i + 1 :]:
                if not a.intersects(b):
                    continue
                # "Widget boundaries may not overlap, except for
                # static_decoration widgets or widgets with a visibility
                # switch (which can not overlap if it is possible that they
                # are visible at the same time)."
                if a.decoration or b.decoration or (a.hideable and b.hideable):
                    continue
                report.add(
                    WARNING,
                    "overlap",
                    f"nodes '{a.node}' and '{b.node}' overlap",
                    subject=f"{a.node}+{b.node}",
                    panel=panel,
                )


def _check_panel_bounds(
    report: Report, panel: str, panel_rects: list[_Rect], size: calibration.PanelSize
) -> None:
    """Panel-edge requirements: inside the panel, clear of the side margins."""
    if not calibration.is_folded(panel):
        units = calibration.rack_units_for_height(size.height)
        if units is None:
            report.add(
                ERROR,
                "rack-height",
                f"the {panel} backdrop is {size.height} px tall, which is not a whole "
                f"number of {calibration.UNIT_HEIGHT_PX} px rack units",
                panel=panel,
            )
        elif units > calibration.MAX_RACK_UNITS:
            report.add(
                ERROR,
                "rack-height",
                f"the device is {units}U tall; the rack allows at most "
                f"{calibration.MAX_RACK_UNITS}U",
                panel=panel,
            )
    elif size.height != calibration.FOLDED_HEIGHT_PX:
        report.add(
            ERROR,
            "panel-size",
            f"the {panel} backdrop is {size.height} px tall; folded panels must be "
            f"exactly {calibration.FOLDED_HEIGHT_PX} px",
            panel=panel,
        )
    if size.width != calibration.PANEL_WIDTH_PX:
        report.add(
            ERROR,
            "panel-size",
            f"the {panel} backdrop is {size.width} px wide; every panel image must be "
            f"{calibration.PANEL_WIDTH_PX} px wide (narrower Player panels are cropped "
            "from a full-width image)",
            panel=panel,
        )

    margin = calibration.EDGE_MARGIN_PX
    for rect in panel_rects:
        if rect.x < 0 or rect.y < 0 or rect.right > size.width or rect.bottom > size.height:
            report.add(
                ERROR,
                "bounds",
                f"node '{rect.node}' at ({rect.x:g}, {rect.y:g}) size {rect.w}x{rect.h} "
                f"extends outside the {size.width}x{size.height} panel — every widget "
                "boundary must be completely inside its panel",
                subject=rect.path,
                panel=panel,
            )
            continue
        if not kinds.is_interactive(rect.kind):
            continue
        if rect.x < margin or rect.right > size.width - margin:
            # A warning, not an error: hit_boundaries can pull the interactive
            # area inward from the widget rectangle, which legitimises a
            # rectangle that reaches into the margin.
            report.add(
                WARNING,
                "edge-margin",
                f"interactive node '{rect.node}' reaches into the required {margin} px "
                "left/right panel margin — inset it, or shrink its interactive area "
                "with graphics.hit_boundaries",
                subject=rect.path,
                panel=panel,
            )


def _panel_sizes(
    elements: Sequence[schema.ElementData], gui2d_dir: Path | None
) -> dict[str, calibration.PanelSize]:
    """Panel pixel sizes, taken from each panel's backdrop element or sheet.

    RE2DRender derives the device's rack height from the backdrop PNGs
    (M0 finding 7), so the backdrop *is* the authority on panel size here too.
    Panels whose backdrop size is unknown are skipped by the layout checks.
    """
    sizes: dict[str, calibration.PanelSize] = {}
    for element in elements:
        if element.kind != "backdrop":
            continue
        size: calibration.PanelSize | None = None
        if element.has_frame_size:
            size = calibration.PanelSize(element.frame_w, element.frame_h)
        elif gui2d_dir is not None:
            try:
                meta: PngMeta = read_png_meta(gui2d_dir / f"{element.path}.png")
                size = calibration.PanelSize(meta.width, meta.height)
            except PngError:
                size = None
        if size is None:
            continue
        for placement in element.placements:
            sizes.setdefault(placement.panel, size)
    return sizes
