"""World calibration: the fixed world-to-pixel convention (§4.4).

One scene-level convention makes everything else automatic: a fixed
world-to-pixel scale (default 1 Blender unit = 100 panel px). This module is
the single place that scale and the panel geometry constants live; camera
creation applies these numbers inside Blender, but the numbers themselves are
pure math and tested without ``bpy``.

Spatial convention (documented here because every Blender-side module leans
on it): the panel lies in the world X/Z plane facing −Y — Blender's front
orthographic view. Panel pixel (0, 0) (top-left) sits at the world origin,
+x panel px runs along +X, +y panel px (downward) runs along −Z. Cameras look
along +Y at the panel from −Y.

The *world origin* — which panel pixel the world's (0, 0) lands on — is a
placement convenience, not part of the RE contract: ``re_offset_*`` and the
Lua stay top-left panel pixels regardless. The origin only shifts where the
guides and registration empties sit in Blender, so a designer can model a
device around its centre (``ORIGIN_CENTER``) or its top-centre
(``ORIGIN_TOP_CENTER``) instead of the native top-left corner
(``ORIGIN_TOP_LEFT``). Pass the pixel offset from :func:`origin_offset_px`
into :func:`panel_px_to_world` / :func:`world_to_panel_px`.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "PANEL_WIDTH_PX",
    "UNIT_HEIGHT_PX",
    "FOLDED_HEIGHT_PX",
    "MAX_RACK_UNITS",
    "PLAYER_VISIBLE_WIDTH_PX",
    "EDGE_MARGIN_PX",
    "PLACEHOLDER_SIZE_PX",
    "SNAPSHOT_WIDTH_PX",
    "SNAPSHOT_UNIT_HEIGHT_PX",
    "SNAPSHOT_FOLDED_HEIGHT_PX",
    "SNAPSHOT_FILENAMES",
    "DEFAULT_PPB",
    "DEFAULT_SWEEP_DEG",
    "CAMERA_DISTANCE",
    "PanelSize",
    "panel_size_px",
    "rack_units_for_height",
    "is_folded",
    "ortho_scale",
    "dominant_axis",
    "ORIGIN_TOP_LEFT",
    "ORIGIN_TOP_CENTER",
    "ORIGIN_CENTER",
    "ORIGINS",
    "origin_offset_px",
    "AXIS_VECTORS",
    "DEFAULT_CAMERA_AXIS",
    "axis_vector",
    "panel_px_to_world",
    "world_to_panel_px",
    "element_center_px",
    "element_offset_px",
]

#: World-origin modes: which panel pixel the world (0, 0) lands on. Top-left is
#: the native RE panel-pixel convention; the other two recentre the device in
#: Blender for symmetric modelling without changing anything RE sees.
ORIGIN_TOP_LEFT = "top_left"
ORIGIN_TOP_CENTER = "top_center"
ORIGIN_CENTER = "center"
ORIGINS = (ORIGIN_TOP_LEFT, ORIGIN_TOP_CENTER, ORIGIN_CENTER)

#: Named signed world axes for the configurable camera / knob-rotation axis
#: settings. The camera looks along its chosen axis from that side of the
#: panel; a knob spins around its chosen axis. The §4.4 default is −Y (the
#: panel faces −Y, Blender's front orthographic view).
AXIS_VECTORS = {
    "pos_x": (1.0, 0.0, 0.0),
    "neg_x": (-1.0, 0.0, 0.0),
    "pos_y": (0.0, 1.0, 0.0),
    "neg_y": (0.0, -1.0, 0.0),
    "pos_z": (0.0, 0.0, 1.0),
    "neg_z": (0.0, 0.0, -1.0),
}
DEFAULT_CAMERA_AXIS = "neg_y"


def axis_vector(name: str) -> tuple[float, float, float]:
    """The unit world vector for a named axis, defaulting to the −Y view axis."""
    return AXIS_VECTORS.get(name, AXIS_VECTORS[DEFAULT_CAMERA_AXIS])

#: The SDK's hi-res panel world (design §1). Every number here is a stated
#: *requirement* of the SDK 4.6.0 GUI design guidelines / GUI designer manual
#: Table 1 — see docs/sdk-gui-reference.md. RE2DRender does not enforce them
#: (it echoes back whatever backdrop size it is handed, which is how M0 came
#: to record a folded height of 130); submission review does.
PANEL_WIDTH_PX = 3770
UNIT_HEIGHT_PX = 345
FOLDED_HEIGHT_PX = 150

#: Tallest device the rack accepts.
MAX_RACK_UNITS = 9

#: Player panels are narrower than the rack, but their *images* are still
#: authored at PANEL_WIDTH_PX — the build pipeline crops the side margins.
#: Only the visible width differs, and it differs front vs back.
PLAYER_VISIBLE_WIDTH_PX = {"front": 3590, "back": 3180}

#: Required empty margin along the left and right panel edges: no widget that
#: responds to user input may intersect it. The same 25 px at the top and
#: bottom edges is a guideline rather than a requirement.
EDGE_MARGIN_PX = 25

#: The reserved back-panel placeholder is a fixed size.
PLACEHOLDER_SIZE_PX = (300, 100)

#: Optional custom device thumbnails RE2DRender picks up from GUI2D/ when
#: present and correctly sized (GUI designer manual Table 2).
SNAPSHOT_WIDTH_PX = 754
SNAPSHOT_UNIT_HEIGHT_PX = 69
SNAPSHOT_FOLDED_HEIGHT_PX = 30
SNAPSHOT_FILENAMES = {
    "front": "ThumbSnapshotFront.png",
    "folded_front": "ThumbSnapshotFoldedFront.png",
}

#: Default calibration: panel pixels per Blender unit.
DEFAULT_PPB = 100.0

#: Default knob sweep in degrees (−150°…+150°), configurable per element.
DEFAULT_SWEEP_DEG = 300.0

#: How far (Blender units) an element camera sits from its registration
#: empty. Orthographic, so the distance only needs to clear the geometry.
CAMERA_DISTANCE = 5.0


@dataclass(frozen=True)
class PanelSize:
    """A panel's pixel dimensions."""

    width: int
    height: int


def is_folded(panel: str) -> bool:
    return panel in ("folded_front", "folded_back")


def panel_size_px(panel: str, rack_units: int = 1) -> PanelSize:
    """Pixel size of a panel at a rack height (folded panels ignore units)."""
    if rack_units < 1:
        raise ValueError(f"rack_units must be >= 1, got {rack_units}")
    height = FOLDED_HEIGHT_PX if is_folded(panel) else UNIT_HEIGHT_PX * rack_units
    return PanelSize(width=PANEL_WIDTH_PX, height=height)


def snapshot_size_px(panel: str, rack_units: int = 1) -> PanelSize | None:
    """Required size of a custom thumbnail snapshot, or None if the panel has none.

    Only ``front`` and ``folded_front`` take custom snapshots, and RE2DRender
    ignores a file whose dimensions don't match exactly.
    """
    if panel == "folded_front":
        return PanelSize(SNAPSHOT_WIDTH_PX, SNAPSHOT_FOLDED_HEIGHT_PX)
    if panel == "front":
        if rack_units < 1:
            raise ValueError(f"rack_units must be >= 1, got {rack_units}")
        return PanelSize(SNAPSHOT_WIDTH_PX, SNAPSHOT_UNIT_HEIGHT_PX * rack_units)
    return None


def rack_units_for_height(height_px: int) -> int | None:
    """Rack units implied by a front/back backdrop height, None if not a
    whole number of units (which validation should flag)."""
    units, remainder = divmod(height_px, UNIT_HEIGHT_PX)
    return units if remainder == 0 and units >= 1 else None


def ortho_scale(frame_w: int, frame_h: int, ppb: float = DEFAULT_PPB) -> float:
    """Blender ortho_scale framing exactly ``frame_w × frame_h`` panel px.

    ``ortho_scale`` is the world size of the sensor's *larger* side; Blender
    fits the smaller side from the render aspect ratio, so one number plus
    the resolution pins the framing exactly.
    """
    if frame_w <= 0 or frame_h <= 0:
        raise ValueError(f"frame size must be positive, got {frame_w}x{frame_h}")
    if ppb <= 0:
        raise ValueError(f"ppb must be positive, got {ppb}")
    return max(frame_w, frame_h) / ppb


def dominant_axis(axis: tuple[float, float, float]) -> tuple[int, float]:
    """Map an axis vector to a ``rotation_euler`` component index and a sign.

    A knob spins around whichever world axis dominates its registration
    empty's axis (X=0, Y=1, Z=2); a negative dominant component flips the
    sweep so min→max still runs the way the axis points. Proven in the M0
    spike (axis-driven rotation fix).
    """
    magnitudes = tuple(abs(c) for c in axis)
    if max(magnitudes) == 0.0:
        raise ValueError("axis must be non-zero")
    index = magnitudes.index(max(magnitudes))
    return index, (1.0 if axis[index] >= 0.0 else -1.0)


def origin_offset_px(
    origin: str, panel_width: float, panel_height: float
) -> tuple[float, float]:
    """Panel-pixel offset of the chosen world origin from the top-left corner.

    Subtracted from a panel pixel before it is converted to world space, so
    ``ORIGIN_CENTER`` puts world (0, 0) at the panel centre and
    ``ORIGIN_TOP_CENTER`` at the middle of the top edge. Any unknown mode (and
    ``ORIGIN_TOP_LEFT``) is the identity offset.
    """
    if origin == ORIGIN_CENTER:
        return (panel_width / 2.0, panel_height / 2.0)
    if origin == ORIGIN_TOP_CENTER:
        return (panel_width / 2.0, 0.0)
    return (0.0, 0.0)


def panel_px_to_world(
    x: float, y: float, ppb: float = DEFAULT_PPB,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float, float]:
    """Panel pixel (top-left origin, +y down) → world XYZ on the panel plane.

    ``origin`` is a panel-pixel offset (see :func:`origin_offset_px`) folded in
    so world (0, 0) can sit somewhere other than the panel's top-left corner.
    """
    return ((x - origin[0]) / ppb, 0.0, -(y - origin[1]) / ppb)


def world_to_panel_px(
    location: tuple[float, float, float], ppb: float = DEFAULT_PPB,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float]:
    """World XYZ → panel pixel (inverse of :func:`panel_px_to_world`)."""
    return (location[0] * ppb + origin[0], -location[2] * ppb + origin[1])


def element_center_px(
    offset_x: float, offset_y: float, frame_w: int, frame_h: int
) -> tuple[float, float]:
    """Centre of an element's frame rect in panel px.

    The registration empty (and therefore the camera) sits at the frame
    centre: the device_2D ``offset`` is the frame's top-left corner.
    """
    return (offset_x + frame_w / 2.0, offset_y + frame_h / 2.0)


def element_offset_px(
    center_x: float, center_y: float, frame_w: int, frame_h: int
) -> tuple[float, float]:
    """Inverse of :func:`element_center_px`: frame top-left from its centre.

    Import places the registration empty via the forward conversion; export
    derives the ``offset`` to write back from the empty via this one. They
    live side by side so the centre convention can never change on one side
    only — that half-pixel class of drift is a registration bug (§4.2).
    """
    return (center_x - frame_w / 2.0, center_y - frame_h / 2.0)
