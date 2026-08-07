"""Scene-level RE-Blend settings and validation-report storage.

Element data lives as ``re_*`` custom properties on element collections
(:mod:`reblend.model.schema`); what lives here is per-scene: the project
link (§4.1) and the last validation report, so the panel can draw it.
"""

from __future__ import annotations

import bpy

from ..model import calibration, kinds
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


#: Dynamic enum item tuples must outlive the callback (Blender keeps only a
#: pointer to the strings), so both sets live at module level.
_RESOLUTION_ITEMS = (
    ("THEIRS", "Use Project's", "Take the value from the project's Lua files"),
    ("MINE", "Keep Scene's", "Keep the scene's value (Write Layout to Lua "
                              "writes it back)"),
)
_RESOLUTION_ITEMS_REMOVED = (
    ("MINE", "Keep", "Keep the element in the scene — it stays flagged until "
                     "its node returns to the Lua or you delete it"),
    ("DELETE", "Delete", "Remove the element from the scene: its collection, "
                         "objects, registration empty, guide boxes, driver "
                         "and state keyframes. The Lua files and any rendered "
                         "PNG on disk are not touched"),
)


#: Per-element shadow ownership (§5.1), drawn as a proxy over the active
#: element's ``re_shadow_owner``. Module level for the same reason as the
#: resolution items: Blender keeps only a pointer to these strings.
_SHADOW_OWNER_ITEMS = (
    (kinds.SHADOW_BACKGROUND, "Background",
     "Bake this element's cast shadow into the panel underneath. Right for "
     "anything that holds still across its frames (a knob spins in place, a "
     "button's cap presses within its own outline) — the shadow is rendered "
     "once into the backdrop instead of repeated in every frame"),
    (kinds.SHADOW_ELEMENT, "Own Sheet",
     "Render this element's cast shadow into its own sheet, where it travels "
     "with the art frame by frame. Needed whenever the art moves across the "
     "panel between frames — a fader handle bakes its whole travel, so a "
     "shadow left in the backdrop would sit frozen at one position. Costs a "
     "bigger frame to fit the shadow in, and needs Cycles"),
)
_SHADOW_OWNER_INDEX = {
    owner: index for index, (owner, _label, _desc) in enumerate(_SHADOW_OWNER_ITEMS)
}


def _resolution_items(self, _context):
    from ..project import merge  # local: keep module import light at register

    if self.status == merge.REMOVED:
        return _RESOLUTION_ITEMS_REMOVED
    return _RESOLUTION_ITEMS


class REBLEND_PG_merge_item(bpy.types.PropertyGroup):
    """One row of the last Sync diff (mirrors merge.MergeItem).

    ``resolution`` is the per-item choice (§6.1): accept-theirs/keep-mine for
    added and changed items; keep-or-delete for removed items. Removed
    elements are never deleted automatically — Delete is an explicit choice
    Apply Choices confirms before acting on.
    """

    path: bpy.props.StringProperty()
    status: bpy.props.StringProperty()
    summary: bpy.props.StringProperty()
    resolution: bpy.props.EnumProperty(
        name="Choice",
        items=_resolution_items,
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
        name="Width",
        description="Per-frame width in pixels applied by Fill Missing Sizes. Frame "
                    "size isn't in the RE Lua (§5.2) — the designer picks it, so "
                    "fresh imports start unsized until this fills them in",
        default=0,
        min=0,
    )
    frame_h: bpy.props.IntProperty(
        name="Height",
        description="Per-frame height in pixels applied by Fill Missing Sizes",
        default=0,
        min=0,
    )
    inactive_render: bpy.props.EnumProperty(
        name="Other Elements",
        description="How the other RE Elements behave while one element is "
                    "rendered (§5.1). Shadow-only keeps neighbouring geometry "
                    "shadowing the active element without appearing in its "
                    "sheet. This is about shadows falling *on* the element "
                    "being rendered — where an element's own cast shadow goes "
                    "is the per-element Cast Shadow Into setting",
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
    #: The last Validate report. Render QA findings live in their own store
    #: (``render_findings``) so a render can never silently overwrite the
    #: validation report the designer is working through, or vice versa.
    findings: bpy.props.CollectionProperty(type=REBLEND_PG_finding)
    findings_index: bpy.props.IntProperty(default=0)
    findings_time: bpy.props.StringProperty(default="")
    #: The last render's QA findings (per-sheet alpha/overflow/write checks).
    render_findings: bpy.props.CollectionProperty(type=REBLEND_PG_finding)
    render_findings_time: bpy.props.StringProperty(default="")
    merge_items: bpy.props.CollectionProperty(type=REBLEND_PG_merge_item)
    merge_index: bpy.props.IntProperty(default=0)
    sync_time: bpy.props.StringProperty(default="")
    # Live frame-size editing (§5.2): get/set proxies over the active
    # element's re_frame_w/h (raw IDProperties cannot sit in layout.prop).
    # The getter reads whichever element is active on every redraw and the
    # setter writes through and refreshes the guide boxes, so the fields
    # track the selection and a drag live — with no stored state to resync,
    # because a draw() may not write anything (dict-style assignment
    # included, which Blender rejects with "Writing to ID classes in this
    # context is not allowed").
    active_frame_w: bpy.props.IntProperty(
        name="Frame W",
        description="Active element's per-frame width in pixels — the guide "
                    "boxes follow while you drag",
        min=0,
        get=lambda self: _active_frame_size()[0],
        set=lambda self, value: _set_active_frame_size(w=value),
    )
    active_frame_h: bpy.props.IntProperty(
        name="Frame H",
        description="Active element's per-frame height in pixels — the guide "
                    "boxes follow while you drag",
        min=0,
        get=lambda self: _active_frame_size()[1],
        set=lambda self, value: _set_active_frame_size(h=value),
    )
    active_shadow_owner: bpy.props.EnumProperty(
        name="Cast Shadow Into",
        description="Where the active element's cast shadow is rendered (§5.1)",
        items=_SHADOW_OWNER_ITEMS,
        get=lambda self: _active_shadow_owner(),
        set=lambda self, value: _set_active_shadow_owner(value),
    )
    # Knob rig inputs (§4.3), proxied over the active element's re_rotor /
    # re_sweep_deg the same way Frame W/H proxy re_frame_w/h.
    active_rotor: bpy.props.StringProperty(
        name="Rotor",
        description="The knob's rotating part — Generate Rig drives its "
                    "rotation (frame 0 = minimum, last frame = maximum). "
                    "Recorded on the element (re_rotor), so the rig can be "
                    "rebuilt any time without reselecting",
        get=lambda self: _active_rotor(),
        set=lambda self, value: _set_active_rotor(value),
    )
    active_sweep_deg: bpy.props.FloatProperty(
        name="Sweep",
        description="The knob's rotation sweep in degrees, centred on the "
                    "rest pose: −sweep/2 at frame 0 to +sweep/2 at the last "
                    "frame (§4.3). 300° is the usual Reason knob",
        min=1.0,
        max=360.0,
        get=lambda self: _active_sweep_deg(),
        set=lambda self, value: _set_active_sweep_deg(value),
    )
    re2drender_output: bpy.props.EnumProperty(
        name="Output",
        description="Which asset set RE2DRender produces (its third "
                    "argument). Run without it the tool renders only the "
                    "legacy lo-res set, which Reason/Recon 12+ do not use",
        items=(
            ("hi-res-only", "Hi-res only",
             "Hi-res form only (Reason/Recon 12+); skips the lo-res pass — "
             "the fast choice while iterating"),
            ("hi-res", "Hi-res + lo-res",
             "Both forms — what a submission build needs"),
            ("lo-res", "Lo-res only (legacy)",
             "Omit the argument entirely: legacy lo-res form only"),
        ),
        default="hi-res-only",
    )


def _active_frame_size() -> tuple[int, int]:
    from . import operators  # local: operators imports props at module load

    return operators.active_frame_size(bpy.context)


def _set_active_frame_size(w: int | None = None, h: int | None = None) -> None:
    from . import operators  # local: operators imports props at module load

    operators.set_active_frame_size(bpy.context, w=w, h=h)


def _active_shadow_owner() -> int:
    """Enum get/set work in item indices, so translate at the boundary."""
    from . import operators  # local: operators imports props at module load

    owner = operators.active_shadow_owner(bpy.context)
    return _SHADOW_OWNER_INDEX.get(owner, 0)


def _set_active_shadow_owner(index: int) -> None:
    from . import operators  # local: operators imports props at module load

    if 0 <= index < len(_SHADOW_OWNER_ITEMS):
        operators.set_active_shadow_owner(bpy.context, _SHADOW_OWNER_ITEMS[index][0])


def _active_rotor() -> str:
    from . import operators  # local: operators imports props at module load

    return operators.active_rotor(bpy.context)


def _set_active_rotor(name: str) -> None:
    from . import operators  # local: operators imports props at module load

    operators.set_active_rotor(bpy.context, name)


def _active_sweep_deg() -> float:
    from . import operators  # local: operators imports props at module load

    return operators.active_sweep_deg(bpy.context)


def _set_active_sweep_deg(value: float) -> None:
    from . import operators  # local: operators imports props at module load

    operators.set_active_sweep_deg(bpy.context, value)


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fill_findings(collection, findings) -> None:
    collection.clear()
    for finding in findings:
        row = collection.add()
        row.severity = finding.severity
        row.code = finding.code
        row.message = finding.message
        row.subject = finding.subject
        row.panel = finding.panel


def store_validation_report(settings: REBLEND_PG_settings, findings) -> None:
    _fill_findings(settings.findings, findings)
    settings.findings_time = _timestamp()


def store_render_report(settings: REBLEND_PG_settings, findings) -> None:
    _fill_findings(settings.render_findings, findings)
    settings.render_findings_time = _timestamp()


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
    settings.sync_time = _timestamp()
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
