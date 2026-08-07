"""The N-panel "RE-Blend" tab (§8), ordered the way the work actually runs:
project setup → elements & sizes → model + rig the active element → render →
validate → preview/QA → sync & export.

Panels draw state and fire operators; they hold no logic of their own, so
everything visible here is equally reachable headlessly (§7).
"""

from __future__ import annotations

import bpy

from ..model import kinds, schema, state_tables
from ..project import merge, reporting

_SEVERITY_ICONS = {"error": "CANCEL", "warning": "ERROR"}  # ERROR = the ⚠ icon
_KIND_ICONS = {
    "knob": "MESH_CIRCLE",
    "button_toggle": "CHECKBOX_HLT",
    "button_momentary": "RADIOBUT_ON",
    "button_updown": "SORT_ASC",
    "fader_handle": "ARROW_LEFTRIGHT",
    "selector": "LINENUMBERS_ON",
    "lamp": "LIGHT",
    "backdrop": "MESH_PLANE",
    "static": "OBJECT_HIDDEN",
    "socket": "PLUGIN",
    # Not rendered by RE-Blend: Reason owns the pixels (sdk_supplied) or uses
    # the graphics only as a text/hit rectangle (text_bounds).
    "sdk_supplied": "LOCKED",
    "text_bounds": "SMALL_CAPS",
    "display": "DESKTOP",
}


def _active_element(context):
    """The active collection if it is an RE Element, else ``None``."""
    active = context.collection
    if active is not None and schema.is_element(active):
        return active
    return None


def _element_state_table(active, data):
    """The active element's state table for drawing, ``None`` when corrupt."""
    raw = str(active.get("re_states", ""))
    try:
        return (state_tables.StateTable.from_json(raw) if raw
                else state_tables.default_state_table(data.kind, data.frames)
                or state_tables.StateTable())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 1. RE Project — link, calibrate, import (setup only)
# ---------------------------------------------------------------------------


class REBLEND_PT_project(bpy.types.Panel):
    """Project link, calibration and import (§4.1, §4.4, §6.1) — the setup
    steps. Rendering and validation live in their own panels, beside their
    results."""

    bl_label = "RE Project"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        settings = context.scene.reblend
        layout.prop(settings, "project_root")
        row = layout.row(align=True)
        row.prop(settings, "ppb")
        row.prop(settings, "rack_units")
        layout.prop(settings, "origin")
        row = layout.row(align=True)
        row.prop(settings, "camera_axis")
        row.prop(settings, "rotation_axis")
        layout.operator("reblend.import_project", text="Import RE Project",
                        icon="IMPORT").reposition = False
        # Its own column, so the Move Geometry toggle visually binds to the
        # one button it affects.
        col = layout.column(align=True)
        col.operator("reblend.import_project", text="Re-import & Reposition",
                     icon="FILE_REFRESH").reposition = True
        col.prop(settings, "reposition_geometry")

        layout.separator()
        col = layout.column(align=True)
        col.operator("reblend.install_sdk_parts", icon="IMPORT")
        col.operator("reblend.reference_images", icon="IMAGE_REFERENCE")


# ---------------------------------------------------------------------------
# 2. Elements — the full list, bulk fixes, bulk rigging
# ---------------------------------------------------------------------------


class REBLEND_PT_elements(bpy.types.Panel):
    bl_label = "Elements"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 1

    def draw(self, context):
        from . import operators  # local: panels are imported during registration

        layout = self.layout
        settings = context.scene.reblend
        elements = operators._element_collections(context.scene)
        if not elements:
            layout.label(text="No elements — import a project", icon="INFO")
            return

        unsized = sum(
            1 for c in elements if not schema.props_to_data(c).has_frame_size
        )
        # Frame pixel size isn't in the RE Lua (§5.2), so fresh imports land
        # unsized. The bulk fill sits *above* the (potentially long) list so
        # the fix is in hand before scrolling through what it fixes.
        if unsized:
            box = layout.box()
            box.label(text=f"{unsized} element(s) need a frame size",
                      icon="ERROR")
            row = box.row(align=True)
            row.prop(settings, "frame_w")
            row.prop(settings, "frame_h")
            box.operator("reblend.set_frame_size",
                         text="Fill Missing Sizes",
                         icon="FULLSCREEN_ENTER").scope = "MISSING"

        # The last diff knows which elements lost their Lua node; the live
        # scene knows which have unexported moves. Badge both here so neither
        # needs the Sync & Export panel open to be visible.
        removed_paths = {item.path for item in settings.merge_items
                         if item.status == merge.REMOVED}
        moved_paths = {path for path, _dx, _dy in _moved_elements(context)}
        active = _active_element(context)
        for collection in sorted(elements, key=lambda c: c.name):
            data = schema.props_to_data(collection)
            row = layout.row(align=True)
            row.label(text=data.path or collection.name,
                      icon=_KIND_ICONS.get(data.kind, "QUESTION"))
            size = (f" · {data.frame_w}×{data.frame_h}"
                    if data.has_frame_size else "")
            row.label(text=f"{data.kind} · {data.frames}f{size}")
            if collection is active:
                row.label(text="", icon="LAYER_ACTIVE")
            if data.path in removed_paths:
                row.label(text="", icon="UNLINKED")   # no node in the Lua
            if data.path in moved_paths:
                row.label(text="", icon="ARROW_LEFTRIGHT")  # unexported move
            if not data.has_frame_size:
                row.label(text="", icon="ERROR")
            row.operator("reblend.select_element", text="",
                         icon="RESTRICT_SELECT_OFF").path = data.path
            row.operator("reblend.delete_element", text="",
                         icon="TRASH").path = data.path

        layout.separator()
        # Scene-wide, so it lives with the scene-wide list — and stays
        # reachable when the active collection is not a rigged element.
        layout.operator("reblend.generate_all_rigs", icon="OUTLINER")


# ---------------------------------------------------------------------------
# 3. Active Element — identity, size, shadow; State Table and Rig sub-panels
# ---------------------------------------------------------------------------


class REBLEND_PT_active(bpy.types.Panel):
    """The active element on its own, so it collapses independently of the
    (potentially long) element list. Rigging lives in the Rig sub-panel,
    which draws *below* the State Table — the order the work happens in."""

    bl_label = "Active Element"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        active = _active_element(context)
        if active is None:
            layout.label(text="Select an RE Element collection", icon="INFO")
            return

        data = schema.props_to_data(active)
        layout.label(text=data.path or active.name,
                     icon=_KIND_ICONS.get(data.kind, "OUTLINER_COLLECTION"))
        layout.label(text=f"{data.kind} · node '{data.node}' · "
                          f"{data.frame_w}x{data.frame_h}px · {data.frames}f")
        # Frame W/H are get/set proxies over the active element (props.py):
        # the getter reads whichever element is active, the setter writes
        # through and refits the guide boxes. Nothing to resync here — a
        # draw() may not write state, dict-style assignment included.
        settings = context.scene.reblend
        row = layout.row(align=True)
        row.prop(settings, "active_frame_w", text="Frame W")
        row.prop(settings, "active_frame_h", text="Frame H")
        if data.has_frame_size:
            layout.operator("reblend.scale_to_bounds", icon="FULLSCREEN_EXIT")
        # The backdrop *is* the plate other elements drop shadows onto, so it
        # has no plate of its own to choose between — the question only makes
        # sense for art that sits on top of one.
        if kinds.renders_art(data.kind) and data.kind != kinds.BACKDROP:
            layout.prop(settings, "active_shadow_owner")
            if (data.shadow_owner == kinds.SHADOW_ELEMENT
                    and context.scene.render.engine != "CYCLES"):
                warn = layout.row()
                warn.alert = True
                warn.label(text="Element shadows need Cycles", icon="ERROR")


class REBLEND_PT_state_table(bpy.types.Panel):
    """The state-table editor (§4.3): build each state's actions without
    leaving the N-panel, so a named-but-empty default table can be filled in
    by hand. Generate Rig (the Rig sub-panel below) compiles it to keys."""

    bl_label = "State Table"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_parent_id = "REBLEND_PT_active"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        active = _active_element(context)
        if active is None:
            return False
        data = schema.props_to_data(active)
        return kinds.rig_for_kind(data.kind) == kinds.RIG_STATES

    def draw(self, context):
        layout = self.layout
        active = _active_element(context)
        data = schema.props_to_data(active)
        table = _element_state_table(active, data)
        if table is None:
            layout.label(text="re_states JSON is corrupt", icon="ERROR")
            return

        header = layout.row(align=True)
        header.operator("reblend.add_state_action", text="Add Action",
                        icon="ADD")
        header.operator("reblend.reverse_states", text="", icon="ARROW_LEFTRIGHT")
        header.operator("reblend.repair_state_channels", text="", icon="TOOL_SETTINGS")
        if table.frames != data.frames:
            warn = layout.row()
            warn.alert = True
            warn.label(
                text=f"{table.frames} states vs re_frames {data.frames}",
                icon="ERROR")

        controls = table.controls()
        if not controls:
            layout.label(text="No actions yet — add one above", icon="INFO")
            return

        box = layout.box()
        box.label(text="Actions", icon="ANIM")
        for index, channels in enumerate(controls):
            channel = channels[0]
            row = box.row(align=True)
            row.label(text=state_tables.describe_channel(channel))
            if state_tables.id_property_of(channel[2]) is not None:
                row.operator("reblend.copy_driver_reference",
                             text="", icon="COPYDOWN").control = index
            # Spreading a flag is meaningless, and with fewer than three states
            # there is no in-between to fill: hide the button rather than offer
            # an action that can only report an error.
            if state_tables.is_interpolatable(channel) and table.frames > 2:
                row.operator("reblend.spread_state_values",
                             text="", icon="IPO_LINEAR").control = index
            row.operator("reblend.remove_state_action",
                         text="", icon="X").control = index

        uneven = dict(
            (chan, values) for chan, values in table.uneven_travel_channels()
        ) if data.kind == kinds.FADER_HANDLE else {}
        for chan in uneven:
            warn = layout.row()
            warn.alert = True
            warn.label(
                text=f"{state_tables.describe_channel(chan)}: uneven travel",
                icon="ERROR")

        for state_index, state in enumerate(table.states):
            sbox = layout.box()
            title = sbox.row(align=True)
            title.label(text=f"{state_index}: {state.name}", icon="KEYFRAME")
            title.operator("reblend.rename_state", text="",
                           icon="OUTLINER_DATA_FONT").state = state_index
            title.operator("reblend.show_state", text="",
                           icon="RESTRICT_VIEW_OFF").state = state_index
            for index, channels in enumerate(controls):
                channel = channels[0]
                row = sbox.row(align=True)
                row.label(text=_short_channel(channel))
                row.label(text=_format_value(channel,
                                             table.value_in(state_index, channel)))
                grab = row.operator("reblend.capture_state_value",
                                    text="", icon="EYEDROPPER")
                grab.state = state_index
                grab.control = index
                op = row.operator("reblend.set_state_value",
                                  text="", icon="GREASEPENCIL")
                op.state = state_index
                op.control = index


def _short_channel(channel) -> str:
    """The channel's target/kind without repeating the target on every row."""
    return state_tables.describe_channel(channel).split(":", 1)[-1].strip()


def _format_value(channel, value) -> str:
    """A compact, readable rendering of a channel's stored value."""
    if value is None:
        return "—"
    data_path = channel[2]
    if data_path in ("hide_render", "hide_viewport"):
        return "hidden" if value else "visible"
    if isinstance(value, (tuple, list)):
        return ", ".join(f"{component:.2f}" for component in value)
    return f"{float(value):.3f}"


def _axis_short(axis_id: str) -> str:
    """'neg_y' → '-Y' — the axis enum id as its compact display form."""
    sign = "-" if axis_id.startswith("neg") else "+"
    return f"{sign}{axis_id[-1].upper()}"


class REBLEND_PT_rig(bpy.types.Panel):
    """Rig generation (§4.3), drawn *after* the State Table because that is
    the order the work happens in: fill the table (state kinds) or pick the
    rotor (knobs), then generate. Kinds with no rig never see this panel."""

    bl_label = "Rig"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_parent_id = "REBLEND_PT_active"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        active = _active_element(context)
        if active is None:
            return False
        data = schema.props_to_data(active)
        return kinds.rig_for_kind(data.kind) is not None

    def draw(self, context):
        layout = self.layout
        settings = context.scene.reblend
        active = _active_element(context)
        data = schema.props_to_data(active)

        if kinds.rig_for_kind(data.kind) == kinds.RIG_DRIVER:
            self._draw_knob(layout, context, settings, active, data)
        else:
            self._draw_states(layout, active, data)

    def _draw_knob(self, layout, context, settings, active, data) -> None:
        # The knob's whole rig is three inputs: which object spins (recorded
        # on the element), how far, and around which axis (a scene-wide
        # setting, referenced here rather than duplicated).
        layout.prop_search(settings, "active_rotor", active, "all_objects",
                           text="Rotor")
        layout.prop(settings, "active_sweep_deg", text="Sweep")
        if settings.rotation_axis == "auto":
            axis = f"the Camera Axis ({_axis_short(settings.camera_axis)})"
        else:
            axis = _axis_short(settings.rotation_axis)
        layout.label(text=f"Spins around {axis} — set in RE Project",
                     icon="ORIENTATION_GYROS")

        rotor_name = data.rotor or ""
        resolvable = bool(rotor_name) and (
            active.all_objects.get(rotor_name) is not None)
        selected = context.active_object
        selectable = (selected is not None
                      and selected.name in active.all_objects)

        col = layout.column()
        col.operator("reblend.generate_rig", icon="DRIVER")
        if resolvable:
            return
        if selectable:
            # The legacy select-and-generate habit still works; say what it
            # will do instead of doing it silently.
            layout.label(text=f"Will record '{selected.name}' as the rotor",
                         icon="INFO")
        elif rotor_name:
            col.enabled = False
            warn = layout.row()
            warn.alert = True
            warn.label(text=f"Rotor '{rotor_name}' is gone — pick it again",
                       icon="ERROR")
        else:
            col.enabled = False
            layout.label(text="Pick the rotating part as Rotor first",
                         icon="INFO")

    def _draw_states(self, layout, active, data) -> None:
        table = _element_state_table(active, data)
        col = layout.column()
        col.operator("reblend.generate_rig", icon="DRIVER")
        if table is None:
            col.enabled = False
            layout.label(text="Fix the state table first", icon="ERROR")
        elif not table.controls():
            col.enabled = False
            layout.label(text="No actions yet — add them in State Table above",
                         icon="INFO")


# ---------------------------------------------------------------------------
# 4. Render — batch renders and their QA findings
# ---------------------------------------------------------------------------


class REBLEND_PT_render(bpy.types.Panel):
    """Batch rendering (§5.1). Per-sheet QA findings (alpha, overflow, the
    written file) land in the Render Report sub-panel below."""

    bl_label = "Render"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 3

    def draw(self, context):
        layout = self.layout
        settings = context.scene.reblend
        layout.prop(settings, "inactive_render")
        if (settings.inactive_render == "SHADOW"
                and context.scene.render.engine != "CYCLES"):
            warn = layout.row()
            warn.alert = True
            warn.label(text="Cast Shadows needs Cycles", icon="ERROR")
        col = layout.column(align=True)
        col.operator("reblend.render_elements", text="Render All Sheets",
                     icon="RENDER_ANIMATION").scope = "ALL"
        col.operator("reblend.render_elements", text="Render Active Sheet",
                     icon="RENDER_STILL").scope = "ACTIVE"


class REBLEND_PT_render_report(bpy.types.Panel):
    """The last render's QA findings — its own store, so rendering never
    overwrites a Validate report (or vice versa)."""

    bl_label = "Render Report"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_parent_id = "REBLEND_PT_render"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return len(context.scene.reblend.render_findings) > 0

    def draw(self, context):
        layout = self.layout
        settings = context.scene.reblend
        if settings.render_findings_time:
            layout.label(text=f"Rendered {settings.render_findings_time}",
                         icon="INFO")
        _draw_findings(layout, settings.render_findings)
        layout.operator("reblend.save_report", text="Save Render Report…",
                        icon="FILE_TICK").source = "RENDER"


# ---------------------------------------------------------------------------
# 5. Validation — the cross-check table and its report, together
# ---------------------------------------------------------------------------


class REBLEND_PT_validation(bpy.types.Panel):
    bl_label = "Validation"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 4

    def draw(self, context):
        layout = self.layout
        settings = context.scene.reblend
        layout.operator("reblend.validate", icon="CHECKMARK")
        findings = settings.findings
        if not findings:
            layout.label(text="No report yet — click Validate above",
                         icon="INFO")
            return

        if settings.findings_time:
            layout.label(text=f"Validated {settings.findings_time}",
                         icon="INFO")
        _draw_findings(layout, findings)
        layout.operator("reblend.save_report", text="Save Validation Report…",
                        icon="FILE_TICK").source = "VALIDATION"


def _draw_findings(layout, findings) -> None:
    """The shared findings renderer: counts, then one box per (severity,
    code) group so a wall of identical findings can't bury the rest."""
    errors = sum(1 for f in findings if f.severity == "error")
    layout.label(
        text=f"{errors} error(s), {len(findings) - errors} warning(s)",
        icon="CANCEL" if errors else "CHECKMARK",
    )
    for (severity, code), group in reporting.group_findings_by_code(findings):
        box = layout.box()
        icon = _SEVERITY_ICONS.get(severity, "QUESTION")
        if len(group) == 1:
            finding = group[0]
            row = box.row(align=True)
            row.label(text=f"{code}: {finding.subject or finding.panel}",
                      icon=icon)
            if finding.subject:
                # Click-to-select (§6.3): jump from the finding to the
                # element it names, when one is in the scene to jump to.
                row.operator("reblend.select_element", text="",
                             icon="RESTRICT_SELECT_OFF").path = finding.subject
            for line in reporting.wrap_text(finding.message):
                box.label(text=line)
            continue

        box.label(text=f"{code}: {len(group)} items", icon=icon)
        messages = {f.message for f in group}
        if len(messages) == 1:
            # Identical text (the frame-size case): show it once, then
            # list who it applies to.
            for line in reporting.wrap_text(next(iter(messages))):
                box.label(text=line)
            subjects = ", ".join(
                sorted(f.subject or f.panel for f in group if f.subject or f.panel)
            )
            for line in reporting.wrap_text(subjects):
                box.label(text=line, icon="BLANK1")
        else:
            # Same code, different detail per subject: keep every line.
            for finding in group:
                who = finding.subject or finding.panel
                prefix = f"{who}: " if who else ""
                for line in reporting.wrap_text(f"{prefix}{finding.message}"):
                    box.label(text=line, icon="BLANK1")


# ---------------------------------------------------------------------------
# 6. Preview & QA — composite, flip, and hand off to the SDK tools
# ---------------------------------------------------------------------------


class REBLEND_PT_preview(bpy.types.Panel):
    """Panel compositor preview and QA tools (§5.3, §5.4): pick each
    element's preview frame, composite them at their offsets, then close the
    real loop with the SDK tools."""

    bl_label = "Preview & QA"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 5

    def draw(self, context):
        from . import operators  # local: panels are imported during registration

        layout = self.layout
        settings = context.scene.reblend
        row = layout.row(align=True)
        row.prop(settings, "preview_panel", text="")
        row.operator("reblend.preview_panel", text="Preview", icon="RENDERLAYERS")

        playable = []
        for collection in operators._element_collections(context.scene):
            data = schema.props_to_data(collection)
            if data.frames > 1 and any(
                p.panel == settings.preview_panel for p in data.placements
            ):
                playable.append((collection, data))
        if playable:
            box = layout.box()
            box.label(text="Preview Frames", icon="ACTION")
            for collection, data in sorted(playable, key=lambda e: e[1].path):
                if "re_preview_frame" not in collection:
                    continue  # pre-M2 element; migration fills it on file load
                row = box.row(align=True)
                row.label(text=data.path)
                row.prop(collection, '["re_preview_frame"]', text="Frame")

        col = layout.column(align=True)
        col.operator("reblend.contact_sheet", icon="IMGDISPLAY")
        col.operator("reblend.flipbook", icon="PLAY")
        col = layout.column(align=True)
        row = col.row(align=True)
        run = row.operator("reblend.launch_tool", text="Run RE2DRender",
                           icon="RENDER_STILL")
        run.tool = "RENDER"
        run.resolution = settings.re2drender_output
        row.prop(settings, "re2drender_output", text="")
        col.operator("reblend.launch_tool", text="Run RE2DPreview",
                     icon="WORKSPACE").tool = "PREVIEW"


# ---------------------------------------------------------------------------
# 7. Sync & Export — check (read) → resolve → write, top to bottom
# ---------------------------------------------------------------------------


_STATUS_ICONS = {
    merge.ADDED: "ADD",
    merge.REMOVED: "TRASH",
    merge.CHANGED: "ARROW_LEFTRIGHT",
}


class REBLEND_PT_sync(bpy.types.Panel):
    """Two-way sync (§6.1, §6.2), staged in execution order: Check reads the
    Lua and diffs without changing anything; Resolve applies per-item choices
    to the *scene*; Write patches the scene's layout into the *Lua*. Removed
    nodes are flagged, never deleted automatically."""

    bl_label = "Sync & Export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 6

    def draw(self, context):
        layout = self.layout
        settings = context.scene.reblend

        layout.label(text="Step 1 — Check", icon="VIEWZOOM")
        layout.operator("reblend.sync_project", icon="FILE_REFRESH")
        if settings.sync_time:
            layout.label(text=f"Last checked: {settings.sync_time}")
        layout.separator()

        layout.label(text="Step 2 — Resolve", icon="ARROW_LEFTRIGHT")
        items = settings.merge_items
        if not items:
            layout.label(text="No differences recorded yet", icon="INFO")
        else:
            for item in items:
                box = layout.box()
                row = box.row(align=True)
                row.label(text=f"{item.path} · {item.status}",
                          icon=_STATUS_ICONS.get(item.status, "QUESTION"))
                row.prop(item, "resolution", text="")
                for line in reporting.wrap_text(item.summary):
                    box.label(text=line)
            layout.operator("reblend.apply_sync", icon="CHECKMARK")
            if any(item.status == merge.REMOVED for item in items):
                layout.operator("reblend.purge_removed", icon="TRASH")
            layout.operator("reblend.save_report", text="Save Sync Log…",
                            icon="FILE_TICK").source = "SYNC"
        layout.separator()

        layout.label(text="Step 3 — Write", icon="EXPORT")
        # Live, without needing a Check run: dragging a registration empty is
        # a layout edit, and the write step should say when there is
        # something to write.
        moved = _moved_elements(context)
        if moved:
            box = layout.box()
            box.label(text=f"{len(moved)} element(s) moved since the last export",
                      icon="ERROR")
            for path, dx, dy in moved[:5]:
                box.label(text=f"{path}: {dx:+.0f}, {dy:+.0f} px")
            if len(moved) > 5:
                box.label(text=f"…and {len(moved) - 5} more")
        layout.operator("reblend.export_patch", icon="EXPORT")


def _moved_elements(context) -> list[tuple[str, float, float]]:
    """``(path, dx, dy)`` for every element dragged since the last export."""
    from . import operators   # local: panels are imported during registration

    moved = []
    for element in operators._scene_elements(context):
        for stored, derived in element.moved:
            moved.append((element.path, derived.x - stored.x, derived.y - stored.y))
    return moved


CLASSES = (
    REBLEND_PT_project,
    REBLEND_PT_elements,
    REBLEND_PT_active,
    REBLEND_PT_state_table,
    REBLEND_PT_rig,
    REBLEND_PT_render,
    REBLEND_PT_render_report,
    REBLEND_PT_validation,
    REBLEND_PT_preview,
    REBLEND_PT_sync,
)
