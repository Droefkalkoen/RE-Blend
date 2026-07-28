"""The N-panel "RE-Blend" tab: project, elements, sync, preview, validation (§8).

Panels draw state and fire operators; they hold no logic of their own, so
everything visible here is equally reachable headlessly (§7).
"""

from __future__ import annotations

import bpy

from ..model import kinds, schema, state_tables
from ..project import merge

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


class REBLEND_PT_project(bpy.types.Panel):
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
        col = layout.column(align=True)
        col.operator("reblend.import_project", text="Import RE Project",
                     icon="IMPORT").reposition = False
        col.operator("reblend.import_project", text="Re-import & Reposition",
                     icon="FILE_REFRESH").reposition = True
        col.prop(settings, "reposition_geometry")

        layout.separator()
        layout.operator("reblend.validate", icon="CHECKMARK")
        layout.prop(settings, "inactive_render")
        if (settings.inactive_render == "SHADOW"
                and context.scene.render.engine != "CYCLES"):
            layout.label(text="Cast Shadows needs Cycles", icon="ERROR")
        col = layout.column(align=True)
        col.operator("reblend.render_elements", text="Render All",
                     icon="RENDER_ANIMATION").scope = "ALL"
        col.operator("reblend.render_elements", text="Render Active",
                     icon="RENDER_STILL").scope = "ACTIVE"


class REBLEND_PT_active(bpy.types.Panel):
    """The active element on its own, above the full list, so it stays in view
    and collapses independently of the (potentially long) element list."""

    bl_label = "Active Element"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 1

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
        row = layout.row(align=True)
        row.operator("reblend.generate_rig", icon="DRIVER")
        row.operator("reblend.generate_all_rigs", text="All", icon="OUTLINER")


class REBLEND_PT_state_table(bpy.types.Panel):
    """State playground (§5.3): build each state's actions without leaving the
    N-panel, so a named-but-empty default table can be filled in by hand."""

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
        raw = str(active.get("re_states", ""))
        try:
            table = (state_tables.StateTable.from_json(raw) if raw
                     else state_tables.default_state_table(data.kind, data.frames)
                     or state_tables.StateTable())
        except ValueError:
            layout.label(text="re_states JSON is corrupt", icon="ERROR")
            return

        header = layout.row(align=True)
        header.operator("reblend.add_state_action", icon="ADD")
        header.operator("reblend.reverse_states", text="", icon="ARROW_LEFTRIGHT")
        header.operator("reblend.repair_state_channels", text="", icon="TOOL_SETTINGS")
        if table.frames != data.frames:
            layout.label(
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
            layout.label(
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


class REBLEND_PT_elements(bpy.types.Panel):
    bl_label = "All RE Elements"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 2

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
        # The last Sync knows which elements lost their Lua node; badge them
        # here so an orphan is visible without opening the Sync panel.
        removed_paths = {item.path for item in settings.merge_items
                         if item.status == merge.REMOVED}
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
            if not data.has_frame_size:
                row.label(text="", icon="ERROR")
            row.operator("reblend.select_element", text="",
                         icon="RESTRICT_SELECT_OFF").path = data.path
            row.operator("reblend.delete_element", text="",
                         icon="TRASH").path = data.path

        # Frame pixel size isn't in the RE Lua (§5.2), so fresh imports land
        # unsized. Offer a bulk fill so the designer isn't hand-editing dozens
        # of elements to clear the expected per-element warnings.
        if unsized:
            box = layout.box()
            box.label(text=f"{unsized} element(s) need a frame size",
                      icon="ERROR")
            row = box.row(align=True)
            row.prop(settings, "frame_w")
            row.prop(settings, "frame_h")
            box.operator("reblend.set_frame_size",
                         text="Set All Missing Sizes",
                         icon="FULLSCREEN_ENTER").scope = "MISSING"


_STATUS_ICONS = {
    merge.ADDED: "ADD",
    merge.REMOVED: "TRASH",
    merge.CHANGED: "ARROW_LEFTRIGHT",
}


class REBLEND_PT_sync(bpy.types.Panel):
    """Two-way sync (M2): patch-mode export and the re-import merge (§6.1,
    §6.2). The merge list carries per-item accept-theirs/keep-mine; removed
    nodes are flagged here and deleted only when explicitly resolved as
    Delete (or via Clean Up Removed Elements), never automatically."""

    bl_label = "Sync & Export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 3

    def draw(self, context):
        layout = self.layout
        settings = context.scene.reblend
        col = layout.column(align=True)
        col.operator("reblend.export_patch", icon="EXPORT")
        col.operator("reblend.sync_project", icon="FILE_REFRESH")

        # Live, without needing a Sync run: dragging a registration empty is a
        # layout edit, and the panel that offers to export should say when
        # there is something to export.
        moved = _moved_elements(context)
        if moved:
            box = layout.box()
            box.label(text=f"{len(moved)} element(s) moved since the last export",
                      icon="ERROR")
            for path, dx, dy in moved[:5]:
                box.label(text=f"{path}: {dx:+.0f}, {dy:+.0f} px")
            if len(moved) > 5:
                box.label(text=f"…and {len(moved) - 5} more")

        items = settings.merge_items
        if not items:
            layout.label(text="No differences recorded — run Sync", icon="INFO")
            return
        for item in items:
            box = layout.box()
            row = box.row(align=True)
            row.label(text=f"{item.path} · {item.status}",
                      icon=_STATUS_ICONS.get(item.status, "QUESTION"))
            row.prop(item, "resolution", text="")
            for line in _wrap(item.summary):
                box.label(text=line)
        layout.operator("reblend.apply_sync", icon="CHECKMARK")
        if any(item.status == merge.REMOVED for item in items):
            layout.operator("reblend.purge_removed", icon="TRASH")
        layout.operator("reblend.save_report", text="Save Sync Log…",
                        icon="FILE_TICK").source = "SYNC"


def _moved_elements(context) -> list[tuple[str, float, float]]:
    """``(path, dx, dy)`` for every element dragged since the last export."""
    from . import operators   # local: panels are imported during registration

    moved = []
    for element in operators._scene_elements(context):
        for stored, derived in element.moved:
            moved.append((element.path, derived.x - stored.x, derived.y - stored.y))
    return moved


class REBLEND_PT_preview(bpy.types.Panel):
    """Panel compositor preview and QA tools (§5.3, §5.4): the state
    playground picks each element's frame, Preview composites them at their
    offsets, and the SDK tools close the real loop."""

    bl_label = "Preview & QA"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 4

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
            box.label(text="State Playground", icon="ACTION")
            for collection, data in sorted(playable, key=lambda e: e[1].path):
                if "re_preview_frame" not in collection:
                    continue  # pre-M2 element; migration fills it on file load
                row = box.row(align=True)
                row.label(text=data.path)
                row.prop(collection, '["re_preview_frame"]',
                         text=f"0..{data.frames - 1}")

        col = layout.column(align=True)
        col.operator("reblend.contact_sheet", icon="IMGDISPLAY")
        col.operator("reblend.flipbook", icon="PLAY")
        col = layout.column(align=True)
        col.operator("reblend.install_sdk_parts", icon="IMPORT")
        col = layout.column(align=True)
        col.operator("reblend.launch_tool", text="Run RE2DRender",
                     icon="RENDER_STILL").tool = "RENDER"
        col.operator("reblend.launch_tool", text="Run RE2DPreview",
                     icon="WORKSPACE").tool = "PREVIEW"


class REBLEND_PT_validation(bpy.types.Panel):
    bl_label = "Validation Report"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RE-Blend"
    bl_order = 5

    def draw(self, context):
        layout = self.layout
        settings = context.scene.reblend
        findings = settings.findings
        if not findings:
            layout.label(text="No report yet — run Validate", icon="INFO")
            return

        # Validate and the render queue share this store, overwriting each
        # other — the header says which one produced what's on screen.
        source = ("Render QA" if settings.findings_source == "render"
                  else "Validation")
        when = f" — {settings.findings_time}" if settings.findings_time else ""
        layout.label(text=f"{source}{when}", icon="INFO")
        errors = sum(1 for f in findings if f.severity == "error")
        layout.label(
            text=f"{errors} error(s), {len(findings) - errors} warning(s)",
            icon="CANCEL" if errors else "CHECKMARK",
        )
        # Collapse repeats of the same code (e.g. an unsized fresh import fires
        # one frame-size warning per element) into a single counted box, so a
        # wall of identical findings can't bury the ones that differ.
        for (severity, code), group in _group_by_code(findings):
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
                for line in _wrap(finding.message):
                    box.label(text=line)
                continue

            box.label(text=f"{code}: {len(group)} items", icon=icon)
            messages = {f.message for f in group}
            if len(messages) == 1:
                # Identical text (the frame-size case): show it once, then
                # list who it applies to.
                for line in _wrap(next(iter(messages))):
                    box.label(text=line)
                subjects = ", ".join(
                    sorted(f.subject or f.panel for f in group if f.subject or f.panel)
                )
                for line in _wrap(subjects):
                    box.label(text=line, icon="BLANK1")
            else:
                # Same code, different detail per subject: keep every line.
                for finding in group:
                    who = finding.subject or finding.panel
                    prefix = f"{who}: " if who else ""
                    for line in _wrap(f"{prefix}{finding.message}"):
                        box.label(text=line, icon="BLANK1")

        layout.operator("reblend.save_report", text="Save Report…",
                        icon="FILE_TICK").source = "FINDINGS"


def _group_by_code(findings):
    """Group findings by (severity, code), preserving first-seen order.

    Returns a list of ((severity, code), [findings]) so the report can show one
    box per code with a count, instead of one box per finding.
    """
    groups: dict[tuple[str, str], list] = {}
    for finding in findings:
        groups.setdefault((finding.severity, finding.code), []).append(finding)
    return list(groups.items())


def _wrap(text: str, width: int = 55) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


CLASSES = (
    REBLEND_PT_project,
    REBLEND_PT_active,
    REBLEND_PT_state_table,
    REBLEND_PT_elements,
    REBLEND_PT_sync,
    REBLEND_PT_preview,
    REBLEND_PT_validation,
)
