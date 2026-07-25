"""Scene-level RE-Blend settings and validation-report storage.

Element data lives as ``re_*`` custom properties on element collections
(:mod:`reblend.model.schema`); what lives here is per-scene: the project
link (§4.1) and the last validation report, so the panel can draw it.
"""

from __future__ import annotations

import bpy

from ..model import calibration
from ..project import lua_reader


#: Signed world axes offered by the Camera Axis / Knob Rotation Axis settings,
#: −Y first so the §4.4 front-view default leads the dropdown.
_AXIS_ITEMS = (
    ("neg_y", "-Y (Front View)", "Look along −Y — Blender's front orthographic view"),
    ("pos_y", "+Y (Back View)", "Look along +Y"),
    ("neg_x", "-X", "Look along −X"),
    ("pos_x", "+X", "Look along +X"),
    ("neg_z", "-Z (Top-Down)", "Look along −Z"),
    ("pos_z", "+Z (Bottom-Up)", "Look along +Z"),
)


#: The add-on package registered with Blender — ``reblend`` as a plain add-on,
#: ``bl_ext.<repo>.reblend`` when installed as an extension. This module is
#: ``<package>.ui.props``, so the add-on id is two levels up.
ADDON_ID = __package__.rsplit(".", 1)[0]


class REBLEND_PG_finding(bpy.types.PropertyGroup):
    """One row of the last validation report (mirrors validation.Finding)."""

    severity: bpy.props.StringProperty()
    code: bpy.props.StringProperty()
    message: bpy.props.StringProperty()
    subject: bpy.props.StringProperty()
    panel: bpy.props.StringProperty()


class REBLEND_PG_merge_item(bpy.types.PropertyGroup):
    """One row of the last Sync diff (mirrors merge.MergeItem).

    ``resolution`` is the per-item accept-theirs/keep-mine choice (§6.1);
    removed items are flag-only (never auto-deleted), so the resolution is
    ignored for them.
    """

    path: bpy.props.StringProperty()
    status: bpy.props.StringProperty()
    summary: bpy.props.StringProperty()
    resolution: bpy.props.EnumProperty(
        name="Resolution",
        items=(
            ("THEIRS", "Theirs", "Take the value from the project's Lua files"),
            ("MINE", "Mine", "Keep the scene's value (export writes it back)"),
        ),
        default="THEIRS",
    )


class REBLEND_AP_preferences(bpy.types.AddonPreferences):
    """Per-machine settings: SDK tool paths and the SDK root (§5.3).

    Deliberately add-on preferences, not scene properties — these differ per
    machine and must never be committed with a project or a ``.blend``.
    """

    bl_idname = ADDON_ID

    re2drender_path: bpy.props.StringProperty(
        name="RE2DRender",
        description="Path to the SDK's RE2DRender executable (per machine)",
        subtype="FILE_PATH",
    )
    re2dpreview_path: bpy.props.StringProperty(
        name="RE2DPreview",
        description="Path to the SDK's RE2DPreview executable (per machine)",
        subtype="FILE_PATH",
    )
    sdk_root: bpy.props.StringProperty(
        name="SDK Root",
        description=(
            "Folder containing the Rack Extension SDK. RE-Blend reads the "
            "stock 2D parts (sockets, name tape, placeholder, browse groups) "
            "from here instead of rendering them — their appearance is fixed "
            "by Reason. Usually the folder holding RE2DRender/Images"
        ),
        subtype="DIR_PATH",
    )

    def draw(self, context):
        col = self.layout.column()
        col.label(text="SDK paths are per-machine settings; they are "
                       "never stored in the project or the .blend.")
        col.prop(self, "re2drender_path")
        col.prop(self, "re2dpreview_path")
        col.separator()
        col.prop(self, "sdk_root")


def tool_preferences(context):
    """This machine's add-on preferences, or None outside a registered add-on."""
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon is not None else None


class REBLEND_PG_settings(bpy.types.PropertyGroup):
    project_root: bpy.props.StringProperty(
        name="RE Project",
        description="Root of the linked RE project (the directory containing GUI2D/)",
        subtype="DIR_PATH",
    )
    ppb: bpy.props.FloatProperty(
        name="Pixels / Unit",
        description="World calibration: panel pixels per Blender unit (§4.4)",
        default=calibration.DEFAULT_PPB,
        min=1.0,
    )
    rack_units: bpy.props.IntProperty(
        name="Rack Units",
        description="Device height, used for panel guides when no backdrop sheet exists yet",
        default=1,
        min=1,
    )
    origin: bpy.props.EnumProperty(
        name="World Origin",
        description="Which panel pixel the Blender world origin lands on when "
                    "placing elements (§4.4). This only moves guides and "
                    "registration empties in Blender — re_offset and the RE Lua "
                    "stay top-left panel pixels. Change it, then Re-import & "
                    "Reposition to move existing elements onto the new origin",
        items=(
            (calibration.ORIGIN_TOP_LEFT, "Top-Left of Device",
             "Panel pixel (0,0) at the world origin — the native RE convention"),
            (calibration.ORIGIN_TOP_CENTER, "Top-Center",
             "World origin at the middle of the panel's top edge"),
            (calibration.ORIGIN_CENTER, "Center",
             "World origin at the panel centre"),
        ),
        default=calibration.ORIGIN_TOP_LEFT,
    )
    reposition_geometry: bpy.props.BoolProperty(
        name="Move Geometry Too",
        description="When Re-import & Reposition moves an element, also shift "
                    "its modelled geometry (backdrop plane, control meshes) by "
                    "the same amount so it stays registered to its empty. Turn "
                    "off to move only the registration empties and guide boxes "
                    "and leave your models where they are",
        default=True,
    )
    camera_axis: bpy.props.EnumProperty(
        name="Camera Axis",
        description="World axis each element's render camera looks along (§4.4). "
                    "The default −Y is Blender's front orthographic view; change "
                    "it if the device is modelled facing another way. Applied "
                    "through the registration empty, so per-element tilt still "
                    "works",
        items=_AXIS_ITEMS,
        default=calibration.DEFAULT_CAMERA_AXIS,
    )
    rotation_axis: bpy.props.EnumProperty(
        name="Knob Rotation Axis",
        description="World axis a knob's rotor spins around when Generate Rig "
                    "builds its turntable driver. Auto follows the Camera Axis "
                    "through the registration empty (the rotor faces the camera "
                    "and spins in view) — pick an explicit axis to override",
        items=(("auto", "Auto (Camera Axis)",
                "Spin around the camera axis through the registration empty"),)
              + _AXIS_ITEMS,
        default="auto",
    )
    frame_w: bpy.props.IntProperty(
        name="Frame W",
        description="Per-frame width in pixels applied by Set Frame Size. Frame "
                    "size isn't in the RE Lua (§5.2) — the designer picks it, so "
                    "fresh imports start unsized until this fills them in",
        default=0,
        min=0,
    )
    frame_h: bpy.props.IntProperty(
        name="Frame H",
        description="Per-frame height in pixels applied by Set Frame Size",
        default=0,
        min=0,
    )
    inactive_render: bpy.props.EnumProperty(
        name="Inactive Elements",
        description="How the other RE Elements behave while one element is "
                    "rendered (§5.1). Shadow-only keeps neighbouring geometry "
                    "shadowing the active element without appearing in its sheet",
        items=(
            ("SHADOW", "Cast Shadows",
             "Invisible to the camera but still cast shadows on the active "
             "element (and catch none themselves) — the default (Cycles ray "
             "visibility)"),
            ("HIDDEN", "Hidden",
             "Excluded from the render entirely; the active element renders alone"),
        ),
        default="SHADOW",
    )
    preview_panel: bpy.props.EnumProperty(
        name="Panel",
        description="Which panel the compositor previews (§5.3)",
        items=tuple(
            (panel, panel.replace("_", " ").title(), "")
            for panel in lua_reader.PANELS
        ),
        default=lua_reader.PANELS[0],
    )
    findings: bpy.props.CollectionProperty(type=REBLEND_PG_finding)
    findings_index: bpy.props.IntProperty(default=0)
    merge_items: bpy.props.CollectionProperty(type=REBLEND_PG_merge_item)
    merge_index: bpy.props.IntProperty(default=0)


def store_report(settings: REBLEND_PG_settings, findings) -> None:
    settings.findings.clear()
    for finding in findings:
        row = settings.findings.add()
        row.severity = finding.severity
        row.code = finding.code
        row.message = finding.message
        row.subject = finding.subject
        row.panel = finding.panel


def store_merge_items(settings: REBLEND_PG_settings, items) -> None:
    """Persist a Sync diff, keeping any resolution already picked for an item
    still in the diff (re-running Sync must not reset choices).

    Keyed by (path, status): a choice made while an item was *changed* must
    not carry over if the same path later shows up as *added* — a stale
    keep-mine there would silently block the element from ever importing.
    """
    kept = {(row.path, row.status): row.resolution
            for row in settings.merge_items}
    settings.merge_items.clear()
    for item in items:
        row = settings.merge_items.add()
        row.path = item.path
        row.status = item.status
        row.summary = item.summary
        if (item.path, item.status) in kept:
            row.resolution = kept[(item.path, item.status)]


def attach() -> None:
    bpy.types.Scene.reblend = bpy.props.PointerProperty(type=REBLEND_PG_settings)


def detach() -> None:
    del bpy.types.Scene.reblend


CLASSES = (
    REBLEND_PG_finding,
    REBLEND_PG_merge_item,
    REBLEND_AP_preferences,
    REBLEND_PG_settings,
)
