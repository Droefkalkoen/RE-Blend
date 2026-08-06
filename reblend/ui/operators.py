"""RE-Blend operators: import, validate, render, rig generation.

UI-stateless by design (§7): every operator reads its inputs from scene
properties and its arguments, never from panel state, so the same operators
can be driven headlessly. The Blender-independent work (parsing, correlation,
cross-checking) all happens in the pure layers; these operators only
materialise the results into the scene and report.
"""

from __future__ import annotations

import contextlib
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector

from .. import __version__
from ..model import calibration, kinds, rigs, schema, state_tables
from ..project import lua_writer, merge, reporting, sdk_parts, validation
from ..project.link import ElementSpec, ProjectLink, load_project
from ..project.lua_reader import LuaConfigError
from ..project.lua_writer import PatchError
from ..render import bpy_io, compositor, renderer, shadows, stitcher
from . import props

#: Root collection name per panel (§4.2).
PANEL_ROOTS = {
    "front": "RE Front",
    "back": "RE Back",
    "folded_front": "RE Folded Front",
    "folded_back": "RE Folded Back",
}


def _settings(context):
    return context.scene.reblend


def _set_world_location(obj, world_co) -> None:
    """Place ``obj`` at a world-space location, honouring any parent transform.

    Assigning ``obj.location`` sets the *local* offset, which lands a parented
    object in the wrong place; rewriting ``matrix_world`` sets the true world
    position and lets Blender back out the local transform.
    """
    matrix = obj.matrix_world.copy()
    matrix.translation = Vector(world_co)
    obj.matrix_world = matrix


def _project_root(context) -> Path:
    raw = _settings(context).project_root
    if not raw:
        raise LuaConfigError("(unset)", "no RE project linked — set the project root first")
    return Path(bpy.path.abspath(raw))


def _element_collections(scene) -> list[bpy.types.Collection]:
    """Every RE Element collection linked under the scene, once each.

    Scene-scoped on purpose, matching the render queue's traversal
    (:func:`reblend.render.renderer._element_collections`): a collection the
    user deleted in the Outliner is only *unlinked* — it survives in
    ``bpy.data`` until the file is saved — and enumerating ``bpy.data`` kept
    such ghosts listing, validating and rendering. A multi-panel element is
    linked under several panel roots but appears here once.
    """
    found: list[bpy.types.Collection] = []
    seen: set[str] = set()

    def walk(collection):
        for child in collection.children:
            if child.name in seen:
                continue
            seen.add(child.name)
            if schema.is_element(child):
                found.append(child)
            walk(child)

    walk(scene.collection)
    return found


def _collection_by_path(scene, path: str) -> bpy.types.Collection | None:
    for collection in _element_collections(scene):
        if str(collection.get("re_path", "")) == path:
            return collection
    return None


# ---------------------------------------------------------------------------
# import materialisation (§6.1)
# ---------------------------------------------------------------------------
#
# Module-level so Sync's per-item accept-theirs (§6.1) applies a spec through
# exactly the same path a full import does — one materialisation, two doors.


def _origin_offset(settings, panel: str) -> tuple[float, float]:
    """The world-origin pixel offset for one panel (§4.4).

    Derived from the *canonical* SDK panel geometry — width is always
    PANEL_WIDTH_PX and height comes from the rack-unit setting (or the
    folded height) — never from a probed element's size. A backdrop sheet
    that is missing or mis-authored must not drag the centre off; the whole
    workspace centres on the same rack-height-derived origin regardless of
    whether any one element happens to be sized correctly (§4.4).
    """
    size = calibration.panel_size_px(panel, settings.rack_units)
    return calibration.origin_offset_px(settings.origin, size.width, size.height)


def _materialise(context, spec: ElementSpec, settings, reposition: bool) -> bool:
    """Create or update one element collection from a spec; True when new."""
    collection = _collection_by_path(context.scene, spec.path)
    is_new = collection is None
    if is_new:
        collection = bpy.data.collections.new(spec.path)

    # Fill/update the Lua-derived properties. User-owned properties
    # (sweep, states, registration, preview frame, shadow owner) keep their
    # existing values on update — shadow ownership included, because the
    # kind-derived default is only a starting guess and re-import must not
    # quietly move a shadow the designer has already placed. A frame size the
    # user already chose is likewise never clobbered by "unknown". A kept key
    # that is *absent* still gets its default:
    # data_to_props stamps the current re_schema, so every versioned property
    # must exist afterwards or the migration that would add it never runs.
    keep = set()
    if not is_new:
        keep = {"re_sweep_deg", "re_states", "re_registration", "re_preview_frame",
                "re_shadow_owner"}
        if spec.frame_w == 0:
            keep |= {"re_frame_w", "re_frame_h"}
    old_w = int(collection.get("re_frame_w", 0)) if not is_new else 0
    old_h = int(collection.get("re_frame_h", 0)) if not is_new else 0
    for key, value in schema.data_to_props(spec.to_element_data()).items():
        if key not in keep or key not in collection:
            collection[key] = value
    if not is_new:
        # A probed size overwriting a different (or unset) one changes what
        # the registration empty's position *means* — compensate so the
        # derived placement stays put (see _shift_for_resize).
        new_w = int(collection.get("re_frame_w", 0))
        new_h = int(collection.get("re_frame_h", 0))
        if (new_w, new_h) != (old_w, old_h):
            _shift_for_resize(collection, settings, old_w, old_h, new_w, new_h)
            _refresh_guide_boxes(collection, settings)
    if is_new:
        table = state_tables.default_state_table(spec.kind, spec.frames)
        collection["re_states"] = table.to_json() if table else ""

    for panel in spec.panels:
        root = _panel_root(context, panel)
        if collection.name not in {c.name for c in root.children}:
            root.children.link(collection)

    if is_new:
        _registration_empty(collection, spec, settings)
        _refresh_guide_boxes(collection, settings)
    elif reposition:
        _reposition(collection, spec, settings)
    return is_new


def _reposition(collection, spec: ElementSpec, settings) -> None:
    """Move an already-materialised element onto the current calibration.

    Re-import keeps the registration empty (it is user-owned calibration),
    so a plain re-read never moves anything. Reposition deliberately snaps
    the element onto the freshly read placement, the current Pixels/Unit
    and the current World Origin.

    Everything is computed in *world* space (via ``matrix_world``), so an
    element whose empty or geometry is parented under an organising master
    empty still lands where it should instead of being nudged by only its
    local offset. With Move Geometry on (the default) the whole element
    travels by the same delta the empty moves, keeping modelled geometry
    registered; with it off only the empty moves. Guide boxes are always
    rebuilt at the new absolute coordinates.
    """
    empty = bpy.data.objects.get(str(collection.get("re_registration", "")))
    if empty is not None and spec.placements:
        primary = spec.placements[0]
        origin = _origin_offset(settings, primary.panel)
        cx, cy = _center_px(spec, primary)
        target = Vector(
            calibration.panel_px_to_world(cx, cy, settings.ppb, origin))
        delta = target - empty.matrix_world.translation
        if settings.reposition_geometry:
            _translate_element(collection, delta)
        else:
            _set_world_location(empty, target)
    _refresh_guide_boxes(collection, settings)


def _translate_element(collection, delta: Vector) -> None:
    """Shift the element's objects by ``delta`` in world space.

    Each element *root* moves; a child parented to another object in the
    same collection is left alone so it rides its parent (moving both would
    double-shift it). A root parented to something *outside* the element —
    e.g. every empty parented under one organising master empty — still
    gets the delta, so those elements are not silently left behind. Guide
    boxes are skipped because reposition rebuilds them at new coordinates.
    """
    if delta.length == 0.0:
        return
    members = set(collection.objects)
    for obj in collection.objects:
        if obj.get("re_guide") == "box":
            continue
        if obj.parent is not None and obj.parent in members:
            continue  # rides an in-collection parent
        _set_world_location(obj, obj.matrix_world.translation + delta)


def _panel_root(context, panel: str) -> bpy.types.Collection:
    name = PANEL_ROOTS[panel]
    root = bpy.data.collections.get(name)
    if root is None:
        root = bpy.data.collections.new(name)
    if name not in {c.name for c in context.scene.collection.children}:
        context.scene.collection.children.link(root)
    return root


def _center_px(spec: ElementSpec, placement) -> tuple[float, float]:
    """Frame centre in panel px, or the raw offset when size is unknown."""
    if spec.frame_w and spec.frame_h:
        return calibration.element_center_px(placement.x, placement.y,
                                             spec.frame_w, spec.frame_h)
    return (placement.x, placement.y)


def _registration_empty(collection, spec: ElementSpec, settings) -> None:
    primary = spec.placements[0]
    origin = _origin_offset(settings, primary.panel)
    cx, cy = _center_px(spec, primary)
    empty = bpy.data.objects.new(f"reg_{spec.path}", None)
    empty.empty_display_type = "PLAIN_AXES"
    empty.empty_display_size = 0.1
    empty.location = Vector(
        calibration.panel_px_to_world(cx, cy, settings.ppb, origin))
    collection.objects.link(empty)
    collection["re_registration"] = empty.name


def _refresh_guide_boxes(collection, settings) -> None:
    """Fit the element's guide-box wireframes (never rendered) to its current
    properties.

    Placement *and* frame size are read back from the element through the
    same snapshot every scene/project comparison uses, so the boxes follow a
    dragged registration empty and a live Frame W/H edit alike. Existing box
    meshes are resized in place — the same 4-vertex-ring rewrite the panel
    guides use — so dragging a number field updates the viewport without
    churning datablocks; boxes are created or removed only when the placement
    count or sized-ness changes.
    """
    data = _element_snapshot(collection, settings)
    placements = data.effective_placements if data.has_frame_size else ()

    boxes = sorted(
        (o for o in collection.objects if o.get("re_guide") == "box"),
        key=lambda o: o.name,
    )
    for surplus in boxes[len(placements):]:
        mesh = surplus.data
        bpy.data.objects.remove(surplus, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    boxes = boxes[:len(placements)]

    for index, placement in enumerate(placements):
        origin = _origin_offset(settings, placement.panel)
        corners_px = (
            (placement.x, placement.y),
            (placement.x + data.frame_w, placement.y),
            (placement.x + data.frame_w, placement.y + data.frame_h),
            (placement.x, placement.y + data.frame_h),
        )
        verts = [calibration.panel_px_to_world(x, y, settings.ppb, origin)
                 for x, y in corners_px]
        if index < len(boxes):
            mesh = boxes[index].data
            for vert, co in zip(mesh.vertices, verts):
                vert.co = co
            mesh.update()
            continue
        mesh = bpy.data.meshes.new(f"box_{data.path}_{index}")
        mesh.from_pydata(verts, [(0, 1), (1, 2), (2, 3), (3, 0)], [])
        obj = bpy.data.objects.new(mesh.name, mesh)
        obj.display_type = "WIRE"
        obj.hide_render = True
        obj.hide_select = True
        obj["re_guide"] = "box"
        collection.objects.link(obj)


def _shift_for_resize(collection, settings, old_w: int, old_h: int,
                      new_w: int, new_h: int) -> None:
    """Keep the element's panel placement fixed across a frame-size change.

    The registration empty sits at the frame *centre* once a size is known,
    but at the raw device_2D offset point while it is not (§4.2 import) — so
    the same world position changes meaning the moment a size is assigned.
    Left alone, sizing an element silently shifts its derived placement by
    half a frame (and an export then writes that phantom move into the Lua).
    Shifting the empty by the half-size delta keeps the derived top-left
    offset — the value the Lua actually stores — exactly where it was. With
    Move Geometry on the element's objects travel too, the same way
    Re-import & Reposition moves them, so modelled art stays registered.
    """
    empty = bpy.data.objects.get(str(collection.get("re_registration", "")))
    if empty is None or (old_w, old_h) == (new_w, new_h):
        return
    old_cx, old_cy = calibration.element_center_px(0.0, 0.0, old_w, old_h)
    new_cx, new_cy = calibration.element_center_px(0.0, 0.0, new_w, new_h)
    delta = (
        Vector(calibration.panel_px_to_world(new_cx, new_cy, settings.ppb))
        - Vector(calibration.panel_px_to_world(old_cx, old_cy, settings.ppb))
    )
    if delta.length == 0.0:
        return
    if settings.reposition_geometry:
        _translate_element(collection, delta)
    else:
        _set_world_location(empty, empty.matrix_world.translation + delta)


def _apply_frame_size(collection, settings, w: int, h: int) -> None:
    """Write one element's frame size and refit everything that depends on
    it: the registration empty's placement convention and the guide boxes."""
    old_w = int(collection.get("re_frame_w", 0))
    old_h = int(collection.get("re_frame_h", 0))
    if (old_w, old_h) == (w, h):
        return
    collection["re_frame_w"] = w
    collection["re_frame_h"] = h
    _shift_for_resize(collection, settings, old_w, old_h, w, h)
    _refresh_guide_boxes(collection, settings)


def active_frame_size(context) -> tuple[int, int]:
    """The active element's per-frame size, ``(0, 0)`` when there is none.

    The panel's Frame W/H fields are get/set proxies over this (raw
    IDProperties cannot sit in ``layout.prop``), so the fields always show
    the active element without the panel writing anything during draw —
    Blender forbids draw-time writes, dict-style assignment included.
    """
    collection = getattr(context, "collection", None)
    if collection is None or not schema.is_element(collection):
        return (0, 0)
    return (int(collection.get("re_frame_w", 0)),
            int(collection.get("re_frame_h", 0)))


def active_shadow_owner(context) -> str:
    """The active element's shadow owner (§5.1), defaulted by kind.

    Empty string when no element is active, so the panel can leave the field
    out rather than show a choice that belongs to nothing.
    """
    collection = getattr(context, "collection", None)
    if collection is None or not schema.is_element(collection):
        return ""
    return schema.props_to_data(collection).shadow_owner


def set_active_shadow_owner(context, owner: str) -> None:
    """Setter behind the panel's Shadow proxy: write it through to the element.

    Only the property changes — nothing is re-rendered here. The choice takes
    effect on the next render of this element (its own shadow arrives in its
    sheet) *and* on the next render of the panel backdrop (its shadow stops
    being baked there), which is why it is stored rather than acted on.
    """
    collection = getattr(context, "collection", None)
    if collection is None or not schema.is_element(collection):
        return
    if owner in kinds.SHADOW_OWNERS:
        collection["re_shadow_owner"] = owner


def set_active_frame_size(context, w: int | None = None,
                          h: int | None = None) -> None:
    """Setter behind the panel's Frame W/H proxies (§5.2).

    Blender fires the set continuously during a drag, so the guide boxes
    (and the placement-preserving empty shift) track the value live.
    """
    collection = getattr(context, "collection", None)
    if collection is None or not schema.is_element(collection):
        return
    settings = _settings(context)
    if w is None:
        w = int(collection.get("re_frame_w", 0))
    if h is None:
        h = int(collection.get("re_frame_h", 0))
    _apply_frame_size(collection, settings, int(w), int(h))


def _clear_guide_boxes(collection) -> None:
    """Remove the guide-box wireframes (marked ``re_guide``), leaving any
    user geometry (rotors, meshes) in the collection untouched."""
    for obj in [o for o in collection.objects if o.get("re_guide") == "box"]:
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _delete_element(context, collection) -> list[str]:
    """Remove one RE Element and every trace RE-Blend created for it (§6.1).

    This is what an Outliner delete cannot do: the add-on's records name the
    registration empty, mark the guide boxes, and enumerate the state-table
    channels — which may key datablocks *outside* the collection — so only
    this sweep can find all of it. The project's files are never touched: the
    Lua stays the user's, and a rendered ``GUI2D/<path>.png`` is only noted.
    Returns human-readable notes of what was swept, for the operator report.
    """
    notes: list[str] = []
    data = schema.props_to_data(collection)

    raw = str(collection.get("re_states", ""))
    if raw:
        try:
            table = state_tables.StateTable.from_json(raw)
        except ValueError:
            notes.append("state table JSON unreadable — keyframes not swept")
        else:
            cleared = rigs.clear_state_table(table)
            if cleared:
                notes.append(f"removed {cleared} state f-curve(s)")

    # Which object spins is the one thing the properties don't record (§4.3),
    # so sweep the collection's own objects — where Generate Rig drives.
    if kinds.rig_for_kind(data.kind) == kinds.RIG_DRIVER:
        drivers = 0
        for obj in list(collection.objects):
            with contextlib.suppress(RuntimeError, TypeError):
                drivers += bool(rigs.clear_turntable_driver(obj))
        if drivers:
            notes.append(f"cleared {drivers} turntable driver(s)")

    empty = bpy.data.objects.get(str(collection.get("re_registration", "")))
    if empty is not None:
        bpy.data.objects.remove(empty, do_unlink=True)
        notes.append(f"removed registration empty '{data.path}'")

    _clear_guide_boxes(collection)

    removed = shared = 0
    for obj in list(collection.objects):
        if len(obj.users_collection) > 1:
            shared += 1     # also lives elsewhere — unlink only, via the
            continue        # collection removal below
        block = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
        if block is not None and block.users == 0:
            bpy.data.batch_remove([block])
    if removed:
        notes.append(f"removed {removed} object(s)")
    if shared:
        notes.append(f"kept {shared} object(s) linked in other collections")

    context_name = f"{collection.name} context"
    if bpy.data.collections.get(context_name) is not None:
        notes.append(f"kept '{context_name}' (user content)")

    bpy.data.collections.remove(collection)

    try:
        png = _project_root(context) / "GUI2D" / f"{data.path}.png"
    except LuaConfigError:
        png = None
    if png is not None and png.is_file():
        notes.append(f"rendered sheet kept on disk: GUI2D/{png.name}")
    return notes


def _panel_guides(context, link: ProjectLink, settings, reposition: bool) -> None:
    for panel in link.device.panels:
        name = f"RE Panel {panel}"
        existing = bpy.data.objects.get(name)
        if existing is not None and not reposition:
            continue
        # Canonical SDK panel geometry (rack height + PANEL_WIDTH_PX), so
        # the guide rect and its centre match every element placed on it —
        # a mis-sized backdrop must not warp the outline (§4.4).
        size = calibration.panel_size_px(panel, settings.rack_units)
        origin = calibration.origin_offset_px(settings.origin, size.width,
                                              size.height)
        corners_px = ((0, 0), (size.width, 0),
                      (size.width, size.height), (0, size.height))
        verts = [calibration.panel_px_to_world(x, y, settings.ppb, origin)
                 for x, y in corners_px]
        if existing is not None:
            # Reposition in place: same 4-vertex ring, new coordinates.
            for vert, co in zip(existing.data.vertices, verts):
                vert.co = co
            continue
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, [(0, 1), (1, 2), (2, 3), (3, 0)], [])
        obj = bpy.data.objects.new(name, mesh)
        obj.display_type = "WIRE"
        obj.hide_render = True
        obj.hide_select = True
        _panel_root(context, panel).objects.link(obj)


class REBLEND_OT_import_project(bpy.types.Operator):
    """Import (or re-read) the linked RE project's GUI2D config (§6.1).

    Read-only towards the project: parses device_2D.lua + hdgui_2D.lua and
    materialises panel guides, element collections with bounding boxes,
    registration empties, filled re_* properties, and default rigs. Lua files
    are never written (that is M2's patch mode).
    """

    bl_idname = "reblend.import_project"
    bl_label = "Import RE Project"
    bl_options = {"REGISTER", "UNDO"}

    reposition: bpy.props.BoolProperty(
        name="Reposition Elements",
        description="Also move existing registration empties and guide boxes "
                    "to match the current Pixels/Unit and World Origin — a "
                    "fresh re-read otherwise leaves already-placed elements "
                    "where they were",
        default=False,
    )

    def invoke(self, context, event):
        """Warn before repositioning discards scene-side changes.

        Repositioning snaps every registration empty back onto what the Lua
        says, so any drag that has not been exported is lost — silently, and
        with no undo across a file save. Importing *without* repositioning only
        adds, so it needs no confirmation.
        """
        if not self.reposition:
            return self.execute(context)
        keepable, derived = self._scene_side_changes(context)
        if not keepable and not derived:
            return self.execute(context)
        self._keepable = keepable
        self._derived = derived
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        col = self.layout.column()
        keepable = getattr(self, "_keepable", [])
        derived = getattr(self, "_derived", [])
        col.label(
            text=f"{len(keepable) + len(derived)} value(s) differ from the "
                 "project files:",
            icon="ERROR")
        if keepable:
            box = col.box().column(align=True)
            for line in keepable[:8]:
                box.label(text=line)
            if len(keepable) > 8:
                box.label(text=f"…and {len(keepable) - 8} more")
            col.label(text="Repositioning overwrites these with the file's values.")
            col.label(text="Export Layout first to keep them instead.")
        if derived:
            box = col.box().column(align=True)
            for line in derived[:8]:
                box.label(text=line)
            if len(derived) > 8:
                box.label(text=f"…and {len(derived) - 8} more")
            col.label(text="These come from the project itself — the hdgui_2D")
            col.label(text="widget type and the PNG on disk. Export cannot keep")
            col.label(text="them; change the widget or the art instead.")

    def _scene_side_changes(self, context) -> tuple[list[str], list[str]]:
        """Scene values a reposition would overwrite, grouped for the dialog.

        Two groups, because the escape hatches differ. *Keepable* values —
        placements (a dragged empty, or a stored offset the file disagrees
        with) and frame counts — are exactly what Export Layout writes, so
        exporting first preserves them. *Derived* values are defined by the
        project itself: kind follows the hdgui_2D widget type and frame size
        is probed from the PNG on disk, so no export can keep the scene's
        version — the widget or the art has to change instead. Lumping the
        two together read as a contradiction: this dialog said "Export
        Layout first", the export replied "already matches" (it only writes
        offsets/frames), and the next re-import warned again.
        """
        try:
            link = load_project(_project_root(context))
        except LuaConfigError:
            return [], []   # execute() will report the same failure properly
        elements = _scene_elements(context)
        keepable: list[str] = []
        derived: list[str] = []
        moved_paths: set[str] = set()
        for element in elements:
            for stored, drifted in element.moved:
                moved_paths.add(element.path)
                keepable.append(
                    f"{element.path}  moved to {drifted.x:.0f}, {drifted.y:.0f} "
                    f"(Lua: {stored.x:.0f}, {stored.y:.0f})"
                )
        for item in merge.diff_link(link.specs, elements):
            if item.status != merge.CHANGED:
                continue
            for change in item.changes:
                if change.field == "placements" and item.path in moved_paths:
                    continue    # already listed as a move, in friendlier terms
                line = f"{item.path}  {change}"
                if change.field in ("placements", "frames"):
                    keepable.append(line)
                else:           # kind, frame size
                    derived.append(line)
        return keepable, derived

    def execute(self, context):
        try:
            link = load_project(_project_root(context))
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        settings = _settings(context)
        created = updated = 0
        for spec in link.specs:
            was_new = _materialise(context, spec, settings, self.reposition)
            created += was_new
            updated += not was_new
        _panel_guides(context, link, settings, self.reposition)

        verb = "re-imported" if self.reposition else "imported"
        placed = f", {updated} repositioned" if self.reposition else ""
        self.report(
            {"INFO"},
            f"{verb} {link.root.name}: {created} new elements, "
            f"{updated} updated{placed}",
        )
        return {"FINISHED"}


class REBLEND_OT_validate(bpy.types.Operator):
    """Run the full cross-check table (§6.3) and store the report."""

    bl_idname = "reblend.validate"
    bl_label = "Validate"

    def execute(self, context):
        try:
            link = load_project(_project_root(context))
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        elements = _scene_elements(context, measure_travel=True)
        scene_info = validation.SceneInfo(
            view_transform=context.scene.view_settings.view_transform
        )
        report = validation.validate_link(link, elements, scene_info)
        props.store_report(_settings(context), report.findings,
                           source="validation")

        if report.ok and not report.warnings:
            self.report({"INFO"}, "validation clean: no errors, no warnings")
        else:
            level = {"INFO"} if report.ok else {"WARNING"}
            self.report(
                level,
                f"validation: {len(report.errors)} error(s), "
                f"{len(report.warnings)} warning(s) — see the RE panel",
            )
        return {"FINISHED"}


class REBLEND_OT_set_frame_size(bpy.types.Operator):
    """Fill in per-frame pixel size, which the RE Lua never carries (§5.2).

    Frame size is the designer's choice (or read from existing art at import),
    so a fresh import lands with every element unsized and the validator flags
    one ``frame-size`` warning per element. This applies the panel's Width and
    Height in bulk so the whole set can be cleared at once, or to just the
    active element. ``MISSING`` never clobbers a size already set (a probed or
    hand-picked one); ``ACTIVE`` overwrites the active element deliberately.
    """

    bl_idname = "reblend.set_frame_size"
    bl_label = "Set Frame Size"
    bl_options = {"REGISTER", "UNDO"}

    scope: bpy.props.EnumProperty(
        name="Scope",
        items=(
            ("MISSING", "Missing", "Every element that has no frame size yet"),
            ("ACTIVE", "Active", "Only the active collection's element"),
        ),
        default="MISSING",
    )

    def execute(self, context):
        settings = _settings(context)
        w, h = int(settings.frame_w), int(settings.frame_h)
        if w <= 0 or h <= 0:
            self.report({"ERROR"}, "set a positive Frame W and Frame H first")
            return {"CANCELLED"}

        if self.scope == "ACTIVE":
            active = context.collection
            if active is None or not schema.is_element(active):
                self.report({"ERROR"}, "active collection is not an RE Element")
                return {"CANCELLED"}
            targets = [active]
        else:
            targets = [c for c in _element_collections(context.scene)
                       if not schema.props_to_data(c).has_frame_size]

        for collection in targets:
            _apply_frame_size(collection, settings, w, h)

        if not targets:
            self.report({"INFO"}, "no elements needed a frame size")
        else:
            self.report({"INFO"}, f"set {w}x{h}px on {len(targets)} element(s)")
        return {"FINISHED"}


#: Which two world-axis indices are the camera's screen plane (width, height)
#: for a given Camera Axis — the pair perpendicular to the view direction.
_SCREEN_AXES = {
    "neg_y": (0, 2), "pos_y": (0, 2),   # front/back: X wide, Z tall
    "neg_x": (1, 2), "pos_x": (1, 2),   # side: Y wide, Z tall
    "neg_z": (0, 1), "pos_z": (0, 1),   # top/bottom: X wide, Y tall
}


class REBLEND_OT_scale_to_bounds(bpy.types.Operator):
    """Scale the active object to the active element's frame bounds (§5.2).

    Handy for backdrops: model a rough plane, then snap it to exactly
    ``re_frame_w × re_frame_h`` in world units (at the current Pixels/Unit)
    across the camera's screen plane. ``Stretch`` fills the bounds on both
    axes independently; ``Uniform`` keeps the object's aspect and fits inside.

    Scaling is applied along the object's local axes, so it is exact for an
    axis-aligned (un-rotated) object — the usual case for a panel plane.
    """

    bl_idname = "reblend.scale_to_bounds"
    bl_label = "Scale to Bounds"
    bl_options = {"REGISTER", "UNDO"}

    fit: bpy.props.EnumProperty(
        name="Fit",
        items=(
            ("STRETCH", "Stretch", "Fill the frame on both axes independently"),
            ("UNIFORM", "Uniform", "Preserve aspect ratio and fit inside the frame"),
        ),
        default="STRETCH",
    )

    def execute(self, context):
        collection = context.collection
        if collection is None or not schema.is_element(collection):
            self.report({"ERROR"}, "active collection is not an RE Element")
            return {"CANCELLED"}
        data = schema.props_to_data(collection)
        if not data.has_frame_size:
            self.report({"ERROR"}, f"'{data.path}': set a frame size first")
            return {"CANCELLED"}

        obj = context.active_object
        if obj is None:
            self.report({"ERROR"}, "select the object to scale first")
            return {"CANCELLED"}

        settings = _settings(context)
        w_idx, h_idx = _SCREEN_AXES[settings.camera_axis]
        dims = obj.dimensions
        cur_w, cur_h = dims[w_idx], dims[h_idx]
        if cur_w <= 0.0 or cur_h <= 0.0:
            self.report({"ERROR"}, "object has no extent across the camera plane")
            return {"CANCELLED"}

        target_w = data.frame_w / settings.ppb
        target_h = data.frame_h / settings.ppb
        sw, sh = target_w / cur_w, target_h / cur_h
        if self.fit == "UNIFORM":
            sw = sh = min(sw, sh)

        scale = list(obj.scale)
        scale[w_idx] *= sw
        scale[h_idx] *= sh
        obj.scale = scale
        self.report(
            {"INFO"},
            f"scaled '{obj.name}' to {data.frame_w}x{data.frame_h}px bounds",
        )
        return {"FINISHED"}


class REBLEND_OT_render_elements(bpy.types.Operator):
    """Batch-render element sheets into the linked project's GUI2D (§5.1)."""

    bl_idname = "reblend.render_elements"
    bl_label = "Render Elements"

    scope: bpy.props.EnumProperty(
        name="Scope",
        items=(
            ("ALL", "All", "Every RE Element in the scene"),
            ("ACTIVE", "Active", "Only the active collection's element"),
        ),
        default="ALL",
    )

    def execute(self, context):
        try:
            root = _project_root(context)
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        out_dir = root / "GUI2D"

        if self.scope == "ACTIVE":
            active = context.collection
            if active is None or not schema.is_element(active):
                self.report({"ERROR"}, "active collection is not an RE Element")
                return {"CANCELLED"}
            collections = [active]
        else:
            collections = _element_collections(context.scene)
        if not collections:
            self.report({"ERROR"}, "no RE Elements in the scene — import the project first")
            return {"CANCELLED"}

        settings = _settings(context)
        results = renderer.render_elements(
            context.scene, collections, out_dir, ppb=settings.ppb,
            inactive_render=settings.inactive_render,
            view_axis=calibration.axis_vector(settings.camera_axis),
        )
        findings = [f for result in results for f in result.findings]
        props.store_report(settings, findings, source="render")

        failed = [r.element for r in results if not r.ok]
        warnings = sum(1 for f in findings if f.severity != validation.ERROR)
        if failed:
            self.report(
                {"ERROR"},
                f"rendered {len(results) - len(failed)}/{len(results)} sheets; "
                f"failed: {', '.join(failed)} — see the RE panel",
            )
        elif warnings:
            self.report(
                {"WARNING"},
                f"rendered {len(results)} sheet(s); {warnings} warning(s) "
                "— see the RE panel",
            )
        else:
            self.report({"INFO"}, f"rendered {len(results)} sheet(s) into {out_dir}")
        return {"FINISHED"}


class REBLEND_OT_generate_rig(bpy.types.Operator):
    """(Re)generate the active element's rig from its re_* properties (§4.3).

    Knobs: rotation driver on the active object (the rotating part), around
    the registration empty's view axis. Multi-state kinds: the element's
    state table applied as constant-interpolation keyframes.
    """

    bl_idname = "reblend.generate_rig"
    bl_label = "Generate Rig"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        collection = context.collection
        if collection is None or not schema.is_element(collection):
            self.report({"ERROR"}, "active collection is not an RE Element")
            return {"CANCELLED"}
        data = schema.props_to_data(collection)
        rig = kinds.rig_for_kind(data.kind)

        if rig == kinds.RIG_DRIVER:
            rotor = context.active_object
            if rotor is None:
                self.report({"ERROR"}, "select the knob's rotating part first")
                return {"CANCELLED"}
            axis = self._knob_axis(context, collection)
            try:
                rigs.ensure_turntable_driver(
                    rotor,
                    frames=data.frames,
                    sweep_deg=float(collection.get("re_sweep_deg",
                                                   calibration.DEFAULT_SWEEP_DEG)),
                    axis=axis,
                )
            except ValueError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            self.report({"INFO"}, f"turntable driver on '{rotor.name}': "
                                  f"{data.frames} frames")
            return {"FINISHED"}

        if rig == kinds.RIG_STATES:
            raw = str(collection.get("re_states", ""))
            if not raw:
                self.report({"ERROR"}, "element has no state table (re_states)")
                return {"CANCELLED"}
            try:
                table = state_tables.StateTable.from_json(raw)
                keys = table.compile()
                stale = rigs.apply_state_table(table)
            except (ValueError, KeyError) as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            if table.frames != data.frames:
                self.report(
                    {"WARNING"},
                    f"state table has {table.frames} states but re_frames = "
                    f"{data.frames} — fix before rendering",
                )
            elif not keys:
                self.report(
                    {"WARNING"},
                    "state table has named states but no actions yet — add "
                    "visibility/emission/transform actions to each state",
                )
            else:
                pruned = f", pruned {stale} stale key(s)" if stale else ""
                self.report({"INFO"}, f"keyed {len(keys)} state action(s) over "
                                      f"{table.frames} frames{pruned}")
            return {"FINISHED"}

        self.report({"INFO"}, f"'{data.kind}' elements need no rig")
        return {"FINISHED"}

    def _knob_axis(self, context, collection) -> tuple[float, float, float]:
        return _knob_axis(context, collection)


def _knob_axis(context, collection) -> tuple[float, float, float]:
    """The world axis a knob spins around (§4.2).

    An explicit Knob Rotation Axis setting wins outright; otherwise the knob
    follows the Camera Axis through the registration empty, so it faces the
    camera and spins in view even when the empty is tilted.
    """
    settings = _settings(context)
    if settings.rotation_axis != "auto":
        return calibration.axis_vector(settings.rotation_axis)
    base = Vector(calibration.axis_vector(settings.camera_axis))
    empty = bpy.data.objects.get(str(collection.get("re_registration", "")))
    if empty is None:
        return tuple(base)
    axis = empty.matrix_world.to_quaternion() @ base
    return tuple(axis.normalized())


class REBLEND_OT_generate_all_rigs(bpy.types.Operator):
    """(Re)generate every element's rig in one pass (§4.3, §7).

    A rig is derived from ``re_frames``, so changing a frame count — or pulling
    new counts in from the Lua on sync — leaves rigs stale across the whole
    scene, and a stale rig renders a wrong sheet without complaining. Knobs are
    re-driven only where a rotation driver already exists, since which object
    spins is the one thing the element properties don't record; knobs with no
    driver yet are reported so they can be done by hand.
    """

    bl_idname = "reblend.generate_all_rigs"
    bl_label = "Generate All Rigs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        rigged = skipped = pruned = 0
        problems: list[str] = []

        for collection in _element_collections(context.scene):
            data = schema.props_to_data(collection)
            rig = kinds.rig_for_kind(data.kind)
            if rig is None:
                continue
            try:
                if rig == kinds.RIG_DRIVER:
                    done = self._redrive_knob(context, collection, data)
                    rigged += done
                    skipped += not done
                    if not done:
                        problems.append(f"{data.path}: no rotation driver to rebuild")
                    continue
                stale, ok = self._reapply_states(collection, data)
                pruned += stale
                rigged += ok
                skipped += not ok
                if not ok:
                    problems.append(f"{data.path}: no state actions yet")
            except (ValueError, KeyError) as exc:
                skipped += 1
                problems.append(f"{data.path}: {exc}")

        summary = f"rigged {rigged} element(s)"
        if pruned:
            summary += f", pruned {pruned} stale key(s)"
        if skipped:
            summary += f", skipped {skipped}"
        self.report({"WARNING"} if problems else {"INFO"},
                    summary + (" — " + "; ".join(problems[:3]) if problems else ""))
        return {"FINISHED"}

    def _redrive_knob(self, context, collection, data) -> bool:
        rotor = _driven_rotor(collection)
        if rotor is None:
            return False
        rigs.ensure_turntable_driver(
            rotor,
            frames=data.frames,
            sweep_deg=float(collection.get("re_sweep_deg",
                                           calibration.DEFAULT_SWEEP_DEG)),
            axis=_knob_axis(context, collection),
        )
        return True

    def _reapply_states(self, collection, data) -> tuple[int, bool]:
        raw = str(collection.get("re_states", ""))
        if not raw:
            return 0, False
        table = state_tables.StateTable.from_json(raw)
        if not table.compile():          # named states, no actions
            return 0, False
        if table.frames != data.frames:
            raise ValueError(
                f"state table has {table.frames} states but re_frames = {data.frames}"
            )
        return rigs.apply_state_table(table), True


def _driven_rotor(collection):
    """The object in an element that already carries a rotation driver."""
    for obj in collection.all_objects:
        anim = obj.animation_data
        if anim is None:
            continue
        if any(fcurve.data_path == "rotation_euler" for fcurve in anim.drivers):
            return obj
    return None


# ---------------------------------------------------------------------------
# state-table editing (the "state playground", §5.3)
# ---------------------------------------------------------------------------
#
# The persisted source of truth stays the ``re_states`` JSON string; these
# operators load it, mutate it through the pure StateTable helpers (which keep
# it total by construction), and write it back. No parallel live model, so the
# same edits are reproducible headlessly.


def _require_states_element(op, context):
    """The active collection if it's a state-rigged element, else report and None."""
    collection = context.collection
    if collection is None or not schema.is_element(collection):
        op.report({"ERROR"}, "active collection is not an RE Element")
        return None, None
    data = schema.props_to_data(collection)
    if kinds.rig_for_kind(data.kind) != kinds.RIG_STATES:
        op.report({"ERROR"}, f"'{data.kind}' elements have no state table")
        return None, None
    return collection, data


def _load_state_table(collection, data) -> state_tables.StateTable:
    """The element's state table, seeding the default names if it has none."""
    raw = str(collection.get("re_states", ""))
    if raw:
        return state_tables.StateTable.from_json(raw)  # may raise ValueError
    return state_tables.default_state_table(data.kind, data.frames) \
        or state_tables.StateTable()


#: The model owns the data-path grammar, so it decides what widget a channel
#: needs. Kept as a local alias because every operator here asks.
_value_kind = state_tables.value_kind

_EMISSION_ACTIONS = {"EMISSION_STRENGTH", "EMISSION_COLOR"}


def _emission_node(material_name: str, node_name: str):
    """The named shader node of a material, or ``None``."""
    material = bpy.data.materials.get(material_name)
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return None
    return tree.nodes.get(node_name)


def _resolve_socket(node, want: str) -> str | None:
    """The input on ``node`` that means ``want`` ('Strength' or 'Color').

    Shaders disagree on the name: an *Emission* node has ``Strength`` and
    ``Color``, a *Principled BSDF* has ``Emission Strength`` and ``Emission
    Color``, and a group node has whatever its author typed. Resolving against
    the real node is why the designer never has to know which — passing the
    wrong one produced an unresolvable RNA path at Generate Rig time, long
    after the mistake was made.
    """
    names = [socket.name for socket in node.inputs]
    if want in names:
        return want
    wanted = want.lower()
    emissive = [
        name for name in names
        if "emission" in name.lower() and wanted in name.lower()
    ]
    if emissive:
        return emissive[0]
    suffixed = [name for name in names if name.lower().endswith(wanted)]
    return suffixed[0] if suffixed else None


def _default_value_owner(context, collection):
    """The *default* owner for a new driver value: the registration empty.

    It is the one object every element is guaranteed to have and the one that
    never moves, so a value parked on it survives any amount of re-modelling.

    Only a default, though — this seeds an editable field, and falls back to
    the active object when the element has no resolvable registration empty. So
    a value can end up on any object, and the table's channel is the only
    authority on which. Anything telling the designer where to point a driver
    must read the target back out rather than assume the empty.
    """
    empty = bpy.data.objects.get(str(collection.get("re_registration", "")))
    if empty is not None:
        return empty.name
    obj = getattr(context, "active_object", None)
    return obj.name if obj is not None else ""


def _seed_state_target(operator, context) -> None:
    """Fill the Add State Action dialog's target from the selection.

    Location/visibility/shape-key actions target an object, emission actions a
    material, and a driver value the element's registration empty — so the
    useful default flips with the chosen action, hence an update callback
    rather than a one-shot seed in ``invoke``. Typing over it always wins; only
    changing the action type reseeds.
    """
    obj = getattr(context, "active_object", None)
    if operator.action in _EMISSION_ACTIONS:
        material = obj.active_material if obj is not None else None
        operator.target = material.name if material is not None else ""
        if material is not None:
            # Reseed rather than keep the "Emission" default: on a material
            # whose emission comes from a Principled BSDF the default names a
            # node that isn't there, and the field's whole job is to name one
            # that is. A hand-typed node survives until the action changes.
            operator.node = _guess_emission_node(material)
        return
    if operator.action == "DRIVER_VALUE":
        collection = getattr(context, "collection", None)
        operator.target = (_default_value_owner(context, collection)
                           if collection is not None else "")
        if not operator.value_name:
            operator.value_name = state_tables.generate_value_name(
                _taken_value_names(context.scene))
        return
    operator.target = obj.name if obj is not None else ""
    if operator.action == "SHAPE_KEY" and obj is not None:
        shape_key = obj.active_shape_key
        operator.key_name = shape_key.name if shape_key is not None else ""


def _guess_emission_node(material) -> str:
    """The node most likely meant by "the emission node" of a material."""
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return "Emission"
    for node in tree.nodes:
        if node.type == "EMISSION":
            return node.name
    for node in tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node.name
    return "Emission"


def _taken_value_names(scene) -> set[str]:
    """Every driver-value name any element's table already uses.

    Names are only unique per owning object, but making them unique across the
    file keeps a driver's variable list readable when several elements park
    values on their own empties.
    """
    taken: set[str] = set()
    for collection in _element_collections(scene):
        raw = str(collection.get("re_states", ""))
        if not raw:
            continue
        try:
            table = state_tables.StateTable.from_json(raw)
        except ValueError:
            continue
        for channel in table.channels():
            name = state_tables.id_property_of(channel[2])
            if name:
                taken.add(name)
    return taken


class REBLEND_OT_add_state_action(bpy.types.Operator):
    """Add a state action to every state of the active element (§4.3).

    A named-but-empty default table (the "no actions yet" warning) has states
    but nothing that visibly changes between them. This adds one channel —
    visibility, emission, a transform, a shape key — to *all* states at once so
    the table stays total, seeding it with a neutral value the designer then
    differentiates per state with Set Value.
    """

    bl_idname = "reblend.add_state_action"
    bl_label = "Add State Action"
    bl_options = {"REGISTER", "UNDO"}

    action: bpy.props.EnumProperty(
        name="Action",
        items=(
            ("VISIBILITY", "Visibility", "Show or hide an object per state"),
            ("EMISSION_STRENGTH", "Emission Strength",
             "A material node's emission strength (lamps, glows)"),
            ("EMISSION_COLOR", "Emission Colour", "A material node's emission colour"),
            ("LOCATION", "Location", "One axis of an object's position (fader detents)"),
            ("SHAPE_KEY", "Shape Key", "A shape key's value on a mesh (pressed caps)"),
            ("DRIVER_VALUE", "Custom Property",
             "A named number on the target object that the states drive, for "
             "your own drivers to read. Not a driver itself"),
        ),
        default="VISIBILITY",
        update=_seed_state_target,
    )
    target: bpy.props.StringProperty(
        name="Target", description="Object name (visibility/location/shape key/"
                                   "driver value) or material name (emission)")
    node: bpy.props.StringProperty(
        name="Node", default="Emission",
        description="Emission shader node inside the material")
    axis: bpy.props.EnumProperty(
        name="Axis", items=(("0", "X", ""), ("1", "Y", ""), ("2", "Z", "")),
        default="0")
    key_name: bpy.props.StringProperty(name="Shape Key", description="Shape key name")
    value_name: bpy.props.StringProperty(
        name="Property Name",
        description="Custom-property name the states drive. It lives on the "
                    "target object; a driver reads it as a Single Property "
                    "variable naming that object and this name")

    def invoke(self, context, event):
        _seed_state_target(self, context)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "action")
        col.prop(self, "target")
        if self.action in _EMISSION_ACTIONS:
            col.prop(self, "node")
            self._draw_socket_hint(col)
        elif self.action == "LOCATION":
            col.prop(self, "axis")
        elif self.action == "SHAPE_KEY":
            col.prop(self, "key_name")
        elif self.action == "DRIVER_VALUE":
            col.prop(self, "value_name")
            # Spell out both halves of the address. A property name is not a
            # driver name and cannot be typed on its own anywhere: it only
            # means something paired with the object that carries it.
            col.label(text="A property on the target, not a driver.",
                      icon="DRIVER")
            col.label(text="In a driver: Single Property variable,")
            col.label(text=f'Object {self.target or "?"}, '
                           f'path ["{self.value_name}"]')

    def _draw_socket_hint(self, col) -> None:
        """Say up front which socket will be used, or that the node is missing.

        The alternative is finding out at Generate Rig time via an unresolvable
        RNA path, which names neither the node nor what it does have.
        """
        node = _emission_node(self.target.strip(), self.node.strip())
        if node is None:
            col.label(text="node not found in that material", icon="ERROR")
            return
        want = "Strength" if self.action == "EMISSION_STRENGTH" else "Color"
        socket = _resolve_socket(node, want)
        if socket is None:
            col.label(text=f"'{node.name}' has no {want.lower()} input",
                      icon="ERROR")
        else:
            col.label(text=f"input: {socket}", icon="NODE_SEL")

    def execute(self, context):
        collection, data = _require_states_element(self, context)
        if collection is None:
            return {"CANCELLED"}
        target = self.target.strip()
        if not target:
            self.report({"ERROR"}, "name the target object or material")
            return {"CANCELLED"}
        actions = self._build_actions(target)
        if actions is None:
            return {"CANCELLED"}
        try:
            table = _load_state_table(collection, data)
            table.add_actions(actions)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        collection["re_states"] = table.to_json()
        created = self._materialise_value(target)
        self.report(
            {"INFO"},
            f"added {self.action.replace('_', ' ').lower()} on '{target}' "
            f"to {table.frames} state(s){created}",
        )
        return {"FINISHED"}

    def _materialise_value(self, target: str) -> str:
        """Create a driver value's property now, so drivers can be wired at once.

        See :func:`reblend.model.rigs.ensure_value_property` for why deferring
        this to Generate Rig leaves a window that costs an hour to diagnose.
        """
        if self.action != "DRIVER_VALUE":
            return ""
        name = self.value_name.strip()
        try:
            created = rigs.ensure_value_property(target, name)
        except KeyError:
            # An object that does not exist yet is Generate Rig's error to
            # report, not a reason to refuse the declaration — the table is
            # still valid metadata. Say what did not happen rather than
            # leaving the missing property to be discovered through a driver.
            return (f' — no object {target!r} yet, so ["{name}"] is not '
                    f"there to point a driver at; it appears on Generate Rig")
        return f' — created ["{name}"] on {target!r}' if created else ""

    def _build_actions(self, target):
        if self.action == "VISIBILITY":
            return state_tables.visibility(target, True)
        if self.action in _EMISSION_ACTIONS:
            return self._build_emission(target)
        if self.action == "LOCATION":
            return (state_tables.location(target, int(self.axis), 0.0),)
        if self.action == "SHAPE_KEY":
            key = self.key_name.strip()
            if not key:
                self.report({"ERROR"}, "name the shape key")
                return None
            return (state_tables.shape_key_value(target, key, 0.0),)
        if self.action == "DRIVER_VALUE":
            try:
                return (state_tables.driver_value(
                    target, self.value_name.strip(), 0.0),)
            except ValueError as exc:
                self.report({"ERROR"}, str(exc))
                return None
        return None

    def _build_emission(self, target):
        """Bind to the socket the *actual* node has, not to a guessed name."""
        node_name = self.node.strip() or "Emission"
        node = _emission_node(target, node_name)
        if node is None:
            material = bpy.data.materials.get(target)
            if material is None:
                self.report({"ERROR"}, f"no material named '{target}'")
            else:
                self.report(
                    {"ERROR"},
                    f"material '{target}' has no shader node '{node_name}'",
                )
            return None
        want = "Strength" if self.action == "EMISSION_STRENGTH" else "Color"
        socket = _resolve_socket(node, want)
        if socket is None:
            inputs = ", ".join(f"'{s.name}'" for s in node.inputs)
            self.report(
                {"ERROR"},
                f"node '{node_name}' has no {want.lower()} input — its inputs "
                f"are: {inputs}",
            )
            return None
        if self.action == "EMISSION_STRENGTH":
            return (state_tables.emission_strength(target, 0.0, node_name, socket),)
        return (state_tables.emission_color(
            target, (0.0, 0.0, 0.0, 1.0), node_name, socket),)


class REBLEND_OT_remove_state_action(bpy.types.Operator):
    """Remove a state action (control) from every state of the active element."""

    bl_idname = "reblend.remove_state_action"
    bl_label = "Remove State Action"
    bl_options = {"REGISTER", "UNDO"}

    control: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        collection, data = _require_states_element(self, context)
        if collection is None:
            return {"CANCELLED"}
        try:
            table = _load_state_table(collection, data)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        controls = table.controls()
        if not 0 <= self.control < len(controls):
            self.report({"ERROR"}, "no such state action")
            return {"CANCELLED"}
        channels = controls[self.control]
        label = state_tables.describe_channel(channels[0])
        leftovers = self._leftovers(channels)
        for channel in channels:
            table.remove_channel(channel)
        collection["re_states"] = table.to_json()
        self.report({"INFO"}, f"removed {label}{leftovers}")
        return {"FINISHED"}

    def _leftovers(self, channels) -> str:
        """Name what removal deliberately leaves behind in the scene.

        The table is metadata: removing a control never touches the datablocks
        it drove. For a driver value that is the *right* call — the designer's
        own drivers point at the custom property, and deleting it would break
        them silently, at a distance, with no error. But nothing else will ever
        collect it either: :func:`~reblend.model.rigs.apply_state_table` prunes
        only channels the current table still touches, so the f-curve keeps
        animating a property no table declares. Saying so beats leaving it to
        be found.
        """
        name = state_tables.id_property_of(channels[0][2])
        if name is None:
            return ""
        return (f' — ["{name}"] and its keys stay on {channels[0][1]!r} so '
                f"existing drivers keep working; delete them by hand if unused")


class REBLEND_OT_set_state_value(bpy.types.Operator):
    """Set one state's value for one control on the active element (§4.3)."""

    bl_idname = "reblend.set_state_value"
    bl_label = "Set State Value"
    bl_options = {"REGISTER", "UNDO"}

    state: bpy.props.IntProperty(default=-1)
    control: bpy.props.IntProperty(default=-1)
    value_kind: bpy.props.StringProperty(default="FLOAT")
    bool_value: bpy.props.BoolProperty(name="Visible", default=True)
    float_value: bpy.props.FloatProperty(name="Value", default=0.0)
    color_value: bpy.props.FloatVectorProperty(
        name="Colour", size=4, subtype="COLOR", min=0.0, max=1.0,
        default=(0.0, 0.0, 0.0, 1.0))

    def invoke(self, context, event):
        collection, data = _require_states_element(self, context)
        if collection is None:
            return {"CANCELLED"}
        try:
            table = _load_state_table(collection, data)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        channel = self._channel(table)
        if channel is None:
            return {"CANCELLED"}
        current = table.value_in(self.state, channel)
        self.value_kind = _value_kind(channel)
        if self.value_kind == "BOOL":
            # The stored value is `hide` (1.0 = hidden); present it as Visible.
            self.bool_value = not bool(current)
        elif self.value_kind == "COLOR":
            self.color_value = tuple(current) if current else (0.0, 0.0, 0.0, 1.0)
        else:
            self.float_value = float(current) if current is not None else 0.0
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        if self.value_kind == "BOOL":
            col.prop(self, "bool_value")
        elif self.value_kind == "COLOR":
            col.prop(self, "color_value")
        else:
            col.prop(self, "float_value")

    def execute(self, context):
        collection, data = _require_states_element(self, context)
        if collection is None:
            return {"CANCELLED"}
        try:
            table = _load_state_table(collection, data)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        channel = self._channel(table)
        if channel is None:
            return {"CANCELLED"}
        for chan in table.controls()[self.control]:
            table.set_value(self.state, chan, self._value_for(chan))
        collection["re_states"] = table.to_json()
        self.report({"INFO"}, f"set '{table.states[self.state].name}' value")
        return {"FINISHED"}

    def _channel(self, table):
        controls = table.controls()
        if not (0 <= self.state < table.frames and 0 <= self.control < len(controls)):
            self.report({"ERROR"}, "no such state value")
            return None
        return controls[self.control][0]

    def _value_for(self, channel):
        kind = _value_kind(channel)
        if kind == "BOOL":
            return float(not self.bool_value)  # Visible -> `hide` value
        if kind == "COLOR":
            return tuple(self.color_value)
        return float(self.float_value)


class REBLEND_OT_spread_state_values(bpy.types.Operator):
    """Fill a control's in-between states by linear interpolation (§4.3).

    Set the two extremes and let RE-Blend compute the rest: an 8-position
    selector needs only its first and last handle position. For a
    ``sequence_fader`` this is the only way to *guarantee* the spec's constant
    travel between frames — typed-by-hand detents drift.
    """

    bl_idname = "reblend.spread_state_values"
    bl_label = "Spread Between Extremes"
    bl_options = {"REGISTER", "UNDO"}

    control: bpy.props.IntProperty(default=-1)
    value_kind: bpy.props.StringProperty(default="FLOAT")
    first_value: bpy.props.FloatProperty(name="First State")
    last_value: bpy.props.FloatProperty(name="Last State")
    first_color: bpy.props.FloatVectorProperty(
        name="First State", size=4, subtype="COLOR", min=0.0, max=1.0,
        default=(0.0, 0.0, 0.0, 1.0))
    last_color: bpy.props.FloatVectorProperty(
        name="Last State", size=4, subtype="COLOR", min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0, 1.0))

    def invoke(self, context, event):
        _collection, table, channel = self._resolve(context)
        if channel is None:
            return {"CANCELLED"}
        self.value_kind = _value_kind(channel)
        first = table.value_in(0, channel)
        last = table.value_in(table.frames - 1, channel)
        if self.value_kind == "COLOR":
            self.first_color = tuple(first) if first else (0.0, 0.0, 0.0, 1.0)
            self.last_color = tuple(last) if last else (1.0, 1.0, 1.0, 1.0)
        else:
            self.first_value = float(first) if first is not None else 0.0
            self.last_value = float(last) if last is not None else 0.0
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        if self.value_kind == "COLOR":
            col.prop(self, "first_color")
            col.prop(self, "last_color")
        else:
            col.prop(self, "first_value")
            col.prop(self, "last_value")

    def execute(self, context):
        collection, table, channel = self._resolve(context)
        if channel is None:
            return {"CANCELLED"}
        if self.value_kind == "COLOR":
            start, end = tuple(self.first_color), tuple(self.last_color)
        else:
            start, end = float(self.first_value), float(self.last_value)
        try:
            values = table.spread_channel(channel, start, end)
        except (ValueError, KeyError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        collection["re_states"] = table.to_json()
        self.report(
            {"INFO"},
            f"spread {state_tables.describe_channel(channel)} over "
            f"{len(values)} states",
        )
        return {"FINISHED"}

    def _resolve(self, context):
        """The element, its table and the addressed channel — or three Nones."""
        blank = (None, None, None)
        collection, data = _require_states_element(self, context)
        if collection is None:
            return blank
        try:
            table = _load_state_table(collection, data)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return blank
        controls = table.controls()
        if not 0 <= self.control < len(controls):
            self.report({"ERROR"}, "no such state action")
            return blank
        channel = controls[self.control][0]
        if not state_tables.is_interpolatable(channel):
            self.report(
                {"ERROR"},
                f"{state_tables.describe_channel(channel)} is a flag, not a "
                "quantity — nothing to interpolate",
            )
            return blank
        if table.frames < 2:
            self.report({"ERROR"}, "a spread needs at least 2 states")
            return blank
        return collection, table, channel


class REBLEND_OT_capture_state_value(bpy.types.Operator):
    """Capture one state's value for one control from the live scene (§4.3).

    Pose the handle where the detent belongs, press this, and the state stores
    where it actually is — the counterpart to typing a number into Set Value.
    """

    bl_idname = "reblend.capture_state_value"
    bl_label = "Capture From Scene"
    bl_options = {"REGISTER", "UNDO"}

    state: bpy.props.IntProperty(default=-1)
    control: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        collection, data = _require_states_element(self, context)
        if collection is None:
            return {"CANCELLED"}
        try:
            table = _load_state_table(collection, data)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        controls = table.controls()
        if not (0 <= self.state < table.frames and 0 <= self.control < len(controls)):
            self.report({"ERROR"}, "no such state value")
            return {"CANCELLED"}
        channels = controls[self.control]
        try:
            value = rigs.read_channel_value(channels[0])
        except KeyError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        for channel in channels:
            table.set_value(self.state, channel, value)
        collection["re_states"] = table.to_json()
        self.report(
            {"INFO"},
            f"captured {state_tables.describe_channel(channels[0])} into "
            f"'{table.states[self.state].name}'",
        )
        return {"FINISHED"}


class REBLEND_OT_rename_state(bpy.types.Operator):
    """Rename one state of the active element (§4.3).

    Default names (``state_0…state_7``) say what index a frame is, which the
    panel already shows; a name says what the position *means*.
    """

    bl_idname = "reblend.rename_state"
    bl_label = "Rename State"
    bl_options = {"REGISTER", "UNDO"}

    state: bpy.props.IntProperty(default=-1)
    name: bpy.props.StringProperty(name="Name")

    def invoke(self, context, event):
        collection, data = _require_states_element(self, context)
        if collection is None:
            return {"CANCELLED"}
        try:
            table = _load_state_table(collection, data)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not 0 <= self.state < table.frames:
            self.report({"ERROR"}, "no such state")
            return {"CANCELLED"}
        self.name = table.states[self.state].name
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        collection, data = _require_states_element(self, context)
        if collection is None:
            return {"CANCELLED"}
        try:
            table = _load_state_table(collection, data)
            table.rename_state(self.state, self.name)
        except (ValueError, IndexError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        collection["re_states"] = table.to_json()
        return {"FINISHED"}


class REBLEND_OT_reverse_states(bpy.types.Operator):
    """Mirror the active element's state table end to end (§4.3).

    For when a ``sequence_fader``'s ``inverted`` turns out the other way round:
    the art is right, the frame order is backwards, and retyping N detents is
    both tedious and a chance to fat-finger one.
    """

    bl_idname = "reblend.reverse_states"
    bl_label = "Reverse States"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        collection, data = _require_states_element(self, context)
        if collection is None:
            return {"CANCELLED"}
        try:
            table = _load_state_table(collection, data)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        table.reverse()
        collection["re_states"] = table.to_json()
        self.report({"INFO"}, f"reversed {table.frames} states — "
                              "Generate Rig to re-key")
        return {"FINISHED"}


class REBLEND_OT_repair_state_channels(bpy.types.Operator):
    """Re-bind shader channels whose socket name no longer resolves (§4.3).

    A channel authored against an *Emission* node's ``Strength`` does not
    resolve on a *Principled BSDF*, which calls it ``Emission Strength`` —
    swapping the material's shader is enough to break a table that was correct
    when it was written. This repoints each broken channel at the socket the
    node actually has, keeping every per-state value.
    """

    bl_idname = "reblend.repair_state_channels"
    bl_label = "Repair Channels"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        collection, data = _require_states_element(self, context)
        if collection is None:
            return {"CANCELLED"}
        try:
            table = _load_state_table(collection, data)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        repaired, unfixable = [], []
        for channel in list(table.channels()):
            outcome = self._repair(table, channel)
            if outcome is None:
                continue
            (repaired if outcome[0] else unfixable).append(outcome[1])

        if repaired:
            collection["re_states"] = table.to_json()
        if unfixable:
            self.report({"WARNING"}, "; ".join(unfixable))
        elif repaired:
            self.report({"INFO"}, "repaired " + "; ".join(repaired))
        else:
            self.report({"INFO"}, "every channel resolves — nothing to repair")
        return {"FINISHED"}

    def _repair(self, table, channel):
        """``(fixed, message)`` for a broken channel, or ``None`` if it's fine."""
        id_type, target, data_path, _index = channel
        node_name = state_tables.node_of(data_path)
        socket = state_tables.socket_of(data_path)
        if id_type != "materials" or node_name is None or socket is None:
            return None
        node = _emission_node(target, node_name)
        if node is None:
            return False, f"'{target}' has no node '{node_name}'"
        if socket in [s.name for s in node.inputs]:
            return None  # resolves fine

        want = "Color" if socket.lower().endswith(("color", "colour")) else "Strength"
        found = _resolve_socket(node, want)
        if found is None:
            return False, f"'{node_name}' has no input like '{socket}'"
        table.retarget_channel(
            channel,
            data_path=f'node_tree.nodes["{node_name}"].inputs["{found}"]'
                      ".default_value",
        )
        return True, f"'{socket}' -> '{found}' on {target}"


class REBLEND_OT_copy_driver_reference(bpy.types.Operator):
    """Copy a driver value's property path to the clipboard (§4.3).

    Wiring one up by hand means an object name and an ID-property path typed
    into a driver variable exactly right; this puts the path where it can be
    pasted and names the object to point the variable at. Both halves are
    needed: the path is relative to the object, so the name alone addresses
    nothing.
    """

    bl_idname = "reblend.copy_driver_reference"
    bl_label = "Copy Property Path"

    control: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        collection, data = _require_states_element(self, context)
        if collection is None:
            return {"CANCELLED"}
        try:
            table = _load_state_table(collection, data)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        controls = table.controls()
        if not 0 <= self.control < len(controls):
            self.report({"ERROR"}, "no such state action")
            return {"CANCELLED"}
        _id_type, target, data_path, _index = controls[self.control][0]
        name = state_tables.id_property_of(data_path)
        if name is None:
            self.report({"ERROR"}, "that action is not a driver value")
            return {"CANCELLED"}
        context.window_manager.clipboard = data_path

        # Copy either way — the path is correct regardless, and pre-wiring a
        # driver is legitimate. But a variable that evaluates once against an
        # absent property keeps Blender's stale red path error afterwards
        # (rigs.ensure_value_property), so name the cure while it is cheap.
        # Tables authored before the property was created at declaration time
        # are the case this still catches.
        owner = bpy.data.objects.get(target)
        if owner is None or name not in owner.keys():
            self.report(
                {"WARNING"},
                f"copied {data_path}, but it is not on '{target}' yet — run "
                f"Generate Rig, then step the frame once to clear Blender's "
                f"stale path error",
            )
            return {"FINISHED"}
        self.report(
            {"INFO"},
            f"copied {data_path} — add a driver, Single Property variable, "
            f"Object '{target}', paste as the path",
        )
        return {"FINISHED"}


class REBLEND_OT_show_state(bpy.types.Operator):
    """Jump the scene to the frame a state occupies (§4.3).

    Sprite frame N *is* scene frame N, so previewing a state is just setting
    the frame — but only once the rig has been generated, hence the hint.
    """

    bl_idname = "reblend.show_state"
    bl_label = "Show State"
    bl_options = {"REGISTER", "UNDO"}

    state: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        collection, data = _require_states_element(self, context)
        if collection is None:
            return {"CANCELLED"}
        if not 0 <= self.state < data.frames:
            self.report({"ERROR"}, "no such state")
            return {"CANCELLED"}
        context.scene.frame_set(self.state)
        if "re_preview_frame" in collection:
            collection["re_preview_frame"] = self.state
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# M2 — sync & patch-mode export (§6.1, §6.2)
# ---------------------------------------------------------------------------


def _derived_primary_placement(collection, data: schema.ElementData, settings):
    """The primary placement recomputed from the registration empty.

    The empty is how a control is *moved* in M2: drag it, export, and its
    world position converts back through the current calibration into the
    top-left panel-pixel offset the Lua stores. The inverse of what import
    does — centre when the frame size is known, raw point otherwise.
    """
    empty = bpy.data.objects.get(str(collection.get("re_registration", "")))
    if empty is None or not data.placements:
        return None
    primary = data.placements[0]
    origin = _origin_offset(settings, primary.panel)
    cx, cy = calibration.world_to_panel_px(
        tuple(empty.matrix_world.translation), settings.ppb, origin)
    if data.has_frame_size:
        cx, cy = calibration.element_offset_px(cx, cy, data.frame_w, data.frame_h)
    return schema.Placement(primary.panel, primary.node,
                            float(round(cx)), float(round(cy)))


def _store_placements(collection, data: schema.ElementData) -> None:
    """Write the placements (and their primary mirror) back onto the element."""
    placements = data.effective_placements
    collection["re_placements"] = json.dumps(
        [[p.panel, p.node, p.x, p.y] for p in placements])
    if placements:
        collection["re_offset_x"] = placements[0].x
        collection["re_offset_y"] = placements[0].y


def _describe_edit(edit) -> str:
    """One patch-mode edit in the panel's terms."""
    if isinstance(edit, lua_writer.OffsetEdit):
        return f"{edit.panel}/{edit.node}  offset -> {edit.x:.0f}, {edit.y:.0f}"
    return f"{edit.panel}/{edit.node}  {edit.path} frames -> {edit.frames}"


def _backup_file(path) -> None:
    """Copy a file next to itself as ``<name>.bak``, replacing any older one."""
    shutil.copy2(str(path), f"{path}.bak")


def _element_snapshot(collection, settings) -> schema.ElementData:
    """One element as the scene currently has it, drift included.

    ``placements`` stays what the element's properties (and so the Lua) say;
    ``derived_placements`` is what its registration empty says. Everything that
    compares scene against project — validate, sync, export — goes through
    here, so "I moved it and nothing noticed" cannot happen in one path and not
    another.
    """
    data = schema.props_to_data(collection)
    derived = _derived_primary_placement(collection, data, settings)
    if derived is not None:
        data.derived_placements = (derived,) + data.placements[1:]
    return data


def _scene_elements(context, measure_travel: bool = False
                    ) -> list[schema.ElementData]:
    """Every element as the scene has it.

    ``measure_travel`` additionally steps the timeline to measure how far each
    element's geometry moves across its frames (§5.1). Off by default because
    it re-evaluates the depsgraph once per frame: only validation consumes the
    answer, and sync and export should not pay for it.
    """
    settings = _settings(context)
    collections = _element_collections(context.scene)
    snapshots = [_element_snapshot(c, settings) for c in collections]
    if measure_travel:
        travel = _measure_frame_travel(context, collections, settings)
        for collection, data in zip(collections, snapshots):
            data.frame_travel = travel.get(collection.name, schema.Travel())
    return snapshots


#: Object types with an evaluated bounding box worth measuring. Empties (the
#: registration anchor) and lights have none, and contribute no shadow either.
_GEOMETRY_TYPES = frozenset({"MESH", "CURVE", "SURFACE", "META", "FONT"})


def _measure_frame_travel(context, collections, settings
                          ) -> dict[str, schema.Travel]:
    """How far each element's geometry moves across its own frames (§5.1).

    One pass over the frames the scene uses, measuring every element at each,
    rather than stepping the timeline once per element: the depsgraph
    evaluation is the expensive part and it is shared.

    Two details the result depends on. Movement is decomposed in the *render
    camera's* basis — taken through the registration empty, exactly as
    :func:`reblend.render.renderer._make_camera` does — because sliding across
    the camera plane and moving along the camera axis strand a baked shadow
    very differently. And travel is measured per object and maxed, not over
    the element's combined bounds: a static sibling in the same collection
    would otherwise average a moving part's travel down towards nothing.
    """
    scene = context.scene
    tracked = [
        (c, int(c.get("re_frames", 1)), _camera_basis(c, settings))
        for c in collections
        if int(c.get("re_frames", 1)) > 1
    ]
    if not tracked:
        return {}

    # collection name -> object name -> its world bbox centre at each frame,
    # already projected onto (depth, across-u, across-v).
    tracks: dict[str, dict[str, list[tuple[float, float, float]]]] = {}
    saved_frame = scene.frame_current
    try:
        for frame in range(max(frames for _c, frames, _basis in tracked)):
            scene.frame_set(frame)
            depsgraph = context.evaluated_depsgraph_get()
            for collection, frames, (axis, u, v) in tracked:
                if frame >= frames:
                    continue
                per_object = tracks.setdefault(collection.name, {})
                for obj in collection.all_objects:
                    if obj.type not in _GEOMETRY_TYPES or "re_guide" in obj:
                        continue
                    centre = _world_bbox_centre(obj.evaluated_get(depsgraph))
                    per_object.setdefault(obj.name, []).append(
                        (centre.dot(axis), centre.dot(u), centre.dot(v))
                    )
    finally:
        scene.frame_set(saved_frame)

    return {
        name: shadows.travel_from_samples(per_object, settings.ppb)
        for name, per_object in tracks.items()
    }


def _camera_basis(collection, settings) -> tuple[Vector, Vector, Vector]:
    """``(camera axis, in-plane u, in-plane v)`` for one element.

    The camera axis runs through the registration empty, so an element
    modelled at a tilt is measured against the view it actually renders from
    rather than the world axis.
    """
    axis = Vector(calibration.axis_vector(settings.camera_axis))
    empty = bpy.data.objects.get(str(collection.get("re_registration", "")))
    if empty is not None:
        axis = empty.matrix_world.to_quaternion() @ axis
    axis = axis.normalized()
    u = axis.orthogonal().normalized()
    return axis, u, axis.cross(u).normalized()


def _world_bbox_centre(obj) -> Vector:
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return sum(corners, Vector((0.0, 0.0, 0.0))) / len(corners)


class REBLEND_OT_export_patch(bpy.types.Operator):
    """Patch the scene's offsets and frame counts into device_2D.lua (§6.2).

    Patch mode rewrites only the offset/frames number literals of nodes it
    located via the interpreter read — comments and formatting survive
    byte-for-byte, edits are verified by re-parsing before the file is
    replaced, and any anchor ambiguity refuses the whole export (§10.2).
    """

    bl_idname = "reblend.export_patch"
    bl_label = "Export Layout (Patch Lua)"

    backup: bpy.props.BoolProperty(
        name="Keep a .bak copy",
        description="Copy device_2D.lua to device_2D.lua.bak before patching",
        default=False,
    )

    def invoke(self, context, event):
        """Show what will change before overwriting the user's Lua.

        Patch mode is careful — anchored edits, verified by re-parse, refused
        on any ambiguity — but it still rewrites a file the designer maintains
        by hand. Naming every value it will change turns "did that do what I
        meant?" into a question answered before the write, not after.
        """
        plan = self._plan(context)
        if plan is None:
            return {"CANCELLED"}
        _link, edits, notes = plan
        if not edits:
            self._report_no_op(notes)
            return {"FINISHED"}
        self._preview = [_describe_edit(edit) for edit in edits]
        self._skipped = len(notes)
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        col = self.layout.column()
        preview = getattr(self, "_preview", [])
        col.label(text=f"Overwrite device_2D.lua — {len(preview)} value(s):",
                  icon="ERROR")
        box = col.box().column(align=True)
        for line in preview[:10]:
            box.label(text=line)
        if len(preview) > 10:
            box.label(text=f"…and {len(preview) - 10} more (full list in the console)")
        if getattr(self, "_skipped", 0):
            col.label(text=f"{self._skipped} unknown node(s) skipped — run Sync",
                      icon="INFO")
        col.label(text="Comments and formatting are preserved; the result is")
        col.label(text="re-parsed before the file is replaced.")
        col.prop(self, "backup")

    def _plan(self, context):
        """``(link, edits, notes)`` for the current scene, or ``None`` on error."""
        try:
            link = load_project(_project_root(context))
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return None
        elements = _scene_elements(context)
        edits, notes = lua_writer.compute_device_edits(link.device, elements)
        return link, edits, notes

    def _report_no_op(self, notes) -> None:
        skipped = f" ({len(notes)} unknown node(s) skipped)" if notes else ""
        self.report(
            {"INFO"},
            "nothing to patch: the offsets and frame counts in device_2D.lua "
            f"already match the scene{skipped}",
        )

    def execute(self, context):
        settings = _settings(context)
        plan = self._plan(context)
        if plan is None:
            return {"CANCELLED"}
        link, edits, notes = plan
        snapshots = [(c, _element_snapshot(c, settings))
                     for c in _element_collections(context.scene)]

        for note in notes:
            print(f"[RE-Blend] export: {note}")
        if not edits:
            self._report_no_op(notes)
            return {"FINISHED"}

        if self.backup:
            try:
                _backup_file(link.device.source_path)
            except OSError as exc:
                self.report({"ERROR"}, f"could not write the .bak copy: {exc}")
                return {"CANCELLED"}

        try:
            result = lua_writer.patch_device_2d_file(link.device.source_path, edits)
        except PatchError as exc:
            for reason in exc.reasons:
                print(f"[RE-Blend] refused: {reason}")
            shown = "; ".join(exc.reasons[:2])
            more = f" (+{len(exc.reasons) - 2} more, see console)" if len(exc.reasons) > 2 else ""
            self.report({"ERROR"}, f"refused, nothing written: {shown}{more}")
            return {"CANCELLED"}
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        # The file now agrees with the scene: keep the re_* mirror true too —
        # but only for elements whose node the patch could actually reach. An
        # element skipped as unknown ("run Sync") exported nothing, and
        # overwriting its mirror would silently desync it from the Lua.
        for collection, data in snapshots:
            placements = data.effective_placements
            primary = placements[0] if placements else None
            if primary is not None and link.device.node(
                    primary.panel, primary.node) is not None:
                _store_placements(collection, data)
        for change in result.applied:
            print(f"[RE-Blend] patched: {change}")
        self.report(
            {"INFO"},
            f"patched {len(result.applied)} value(s) in "
            f"{link.device.source_path.name} — verified by re-parse",
        )
        return {"FINISHED"}


class REBLEND_OT_sync_project(bpy.types.Operator):
    """Diff the project's Lua against the scene without changing either (§6.1).

    New nodes, removed nodes, and changed values land in the Sync list for
    per-item accept-theirs/keep-mine resolution; Apply Resolutions acts on it.
    """

    bl_idname = "reblend.sync_project"
    bl_label = "Sync With Project"

    def execute(self, context):
        try:
            link = load_project(_project_root(context))
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        elements = _scene_elements(context)
        items = merge.diff_link(link.specs, elements)
        props.store_merge_items(_settings(context), items)

        if not items:
            self.report({"INFO"}, "scene and project are in sync")
        else:
            counts = {status: sum(1 for i in items if i.status == status)
                      for status in (merge.ADDED, merge.REMOVED, merge.CHANGED)}
            self.report(
                {"INFO"},
                f"sync: {counts[merge.ADDED]} new, {counts[merge.REMOVED]} "
                f"removed, {counts[merge.CHANGED]} changed — resolve in the "
                "RE-Blend panel",
            )
        return {"FINISHED"}


class REBLEND_OT_apply_sync(bpy.types.Operator):
    """Apply the per-item Sync resolutions (§6.1).

    Accept-theirs materialises new elements and snaps changed ones onto the
    file's values through the same path a full import uses; keep-mine leaves
    the scene's value in place (patch-mode export writes it back). Removed
    nodes are never deleted automatically — an item the designer resolved as
    Delete is swept only after a dialog names it.
    """

    bl_idname = "reblend.apply_sync"
    bl_label = "Apply Resolutions"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        """Confirm before honouring any Delete resolutions.

        Accept/keep are cheap to reverse; deleting a collection, its objects
        and its rig is not. Naming every element about to go keeps "which ones
        did that remove?" a question answered before the sweep, not after.
        """
        doomed = self._deletions(context)
        if not doomed:
            return self.execute(context)
        self._doomed = doomed
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        col = self.layout.column()
        doomed = getattr(self, "_doomed", [])
        col.label(text=f"Delete {len(doomed)} element(s) from the scene:",
                  icon="TRASH")
        box = col.box().column(align=True)
        for path in doomed[:10]:
            box.label(text=path)
        if len(doomed) > 10:
            box.label(text=f"…and {len(doomed) - 10} more")
        col.label(text="Removes their collections, objects, registration")
        col.label(text="empties, guide boxes and rigs. The Lua files and")
        col.label(text="rendered PNGs on disk are not touched.")

    def _deletions(self, context) -> list[str]:
        """Removed items currently resolved as Delete (fresh diff, so a node
        that returned to the Lua since the stored Sync is never swept)."""
        try:
            link = load_project(_project_root(context))
        except LuaConfigError:
            return []   # execute() will report the same failure properly
        current = {item.path: item
                   for item in merge.diff_link(link.specs, _scene_elements(context))}
        doomed = []
        for row in _settings(context).merge_items:
            item = current.get(row.path)
            if (row.status == merge.REMOVED and row.resolution == "DELETE"
                    and item is not None and item.status == merge.REMOVED):
                doomed.append(row.path)
        return doomed

    def execute(self, context):
        settings = _settings(context)
        try:
            link = load_project(_project_root(context))
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        elements = _scene_elements(context)
        items = {item.path: item for item in merge.diff_link(link.specs, elements)}

        accepted = kept = flagged = deleted = 0
        rig_stale: list[str] = []
        for row in settings.merge_items:
            item = items.get(row.path)
            if item is None:
                continue  # resolved since the diff was stored
            if item.status == merge.REMOVED:
                if row.status == merge.REMOVED and row.resolution == "DELETE":
                    collection = _collection_by_path(context.scene, row.path)
                    if collection is not None:
                        for note in _delete_element(context, collection):
                            print(f"[RE-Blend] deleted {row.path}: {note}")
                    deleted += 1
                else:
                    flagged += 1
                continue
            if row.resolution != "THEIRS":
                kept += 1
                continue
            # Reposition only when the accepted change is positional: snapping
            # the empty on a frames-only accept would silently destroy a
            # pending, not-yet-exported drag of the registration empty.
            positional = any(change.field in ("placements", "frame size")
                             for change in item.changes)
            _materialise(context, item.spec, settings, reposition=positional)
            if any(change.field == "frames" for change in item.changes):
                rig_stale.append(item.path)
            accepted += 1

        elements = _scene_elements(context)
        props.store_merge_items(settings, merge.diff_link(link.specs, elements))

        parts = [f"accepted {accepted} from Lua", f"kept {kept} scene value(s)"]
        if deleted:
            parts.append(f"deleted {deleted} removed element(s)")
        if flagged:
            parts.append(f"{flagged} removed node(s) kept — resolve as "
                         "Delete to remove them")
        if rig_stale:
            # The rig still encodes the old frame count (§4.3) — art and Lua
            # agree again, but the driver/keyframes must be rebuilt.
            parts.append("frame count changed, re-run Generate Rig for: "
                         + ", ".join(sorted(rig_stale)))
        self.report({"WARNING"} if rig_stale else {"INFO"}, "; ".join(parts))
        return {"FINISHED"}


class REBLEND_OT_delete_element(bpy.types.Operator):
    """Delete one RE Element and everything RE-Blend created for it (§6.1).

    An Outliner delete cannot reach the whole element: the registration
    empty, guide boxes, knob driver and state keyframes (which may key
    datablocks outside the collection) are only findable through the
    element's own records. This confirms, then sweeps all of it. The Lua
    files and any rendered PNG on disk are never touched.
    """

    bl_idname = "reblend.delete_element"
    bl_label = "Delete RE Element"
    bl_options = {"REGISTER", "UNDO"}

    path: bpy.props.StringProperty(
        name="Sprite Path",
        description="The re_path of the element to delete",
    )

    def invoke(self, context, event):
        if _collection_by_path(context.scene, self.path) is None:
            self.report({"ERROR"}, f"no RE Element '{self.path}' in the scene")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        col = self.layout.column()
        col.label(text=f"Delete element '{self.path}' from the scene?",
                  icon="TRASH")
        col.label(text="Removes its collection, objects, registration empty,")
        col.label(text="guide boxes, driver and state keyframes. The Lua")
        col.label(text="files and rendered PNGs on disk are not touched.")

    def execute(self, context):
        collection = _collection_by_path(context.scene, self.path)
        if collection is None:
            self.report({"ERROR"}, f"no RE Element '{self.path}' in the scene")
            return {"CANCELLED"}
        notes = _delete_element(context, collection)
        summary = "; ".join(notes) if notes else "nothing else to sweep"
        self.report({"INFO"}, f"deleted '{self.path}' — {summary}")
        return {"FINISHED"}


class REBLEND_OT_purge_removed(bpy.types.Operator):
    """Delete every element whose node is gone from the Lua, in one pass (§6.1).

    The bulk form of resolving removed Sync items as Delete: re-reads the
    project, finds every scene element no longer named in device_2D.lua,
    confirms the full list, then sweeps each one the same way Delete RE
    Element does. Elements not yet exported (no node *yet*) look identical to
    removed ones, so check the list before confirming.
    """

    bl_idname = "reblend.purge_removed"
    bl_label = "Clean Up Removed Elements"
    bl_options = {"REGISTER", "UNDO"}

    def _removed_paths(self, context) -> list[str] | None:
        try:
            link = load_project(_project_root(context))
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return None
        return [item.path
                for item in merge.diff_link(link.specs, _scene_elements(context))
                if item.status == merge.REMOVED]

    def invoke(self, context, event):
        removed = self._removed_paths(context)
        if removed is None:
            return {"CANCELLED"}
        if not removed:
            self.report({"INFO"}, "every element still has its node in the Lua")
            return {"FINISHED"}
        self._doomed = removed
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        col = self.layout.column()
        doomed = getattr(self, "_doomed", [])
        col.label(text=f"Delete {len(doomed)} element(s) with no Lua node:",
                  icon="TRASH")
        box = col.box().column(align=True)
        for path in doomed[:10]:
            box.label(text=path)
        if len(doomed) > 10:
            box.label(text=f"…and {len(doomed) - 10} more")
        col.label(text="Removes their collections, objects, registration")
        col.label(text="empties, guide boxes and rigs. The Lua files and")
        col.label(text="rendered PNGs on disk are not touched.")

    def execute(self, context):
        removed = self._removed_paths(context)
        if removed is None:
            return {"CANCELLED"}
        deleted = 0
        for path in removed:
            collection = _collection_by_path(context.scene, path)
            if collection is None:
                continue
            for note in _delete_element(context, collection):
                print(f"[RE-Blend] deleted {path}: {note}")
            deleted += 1
        # Keep the stored Sync list honest: the swept items must not linger
        # as resolvable rows pointing at elements that no longer exist.
        settings = _settings(context)
        if len(settings.merge_items):
            try:
                link = load_project(_project_root(context))
                props.store_merge_items(
                    settings, merge.diff_link(link.specs, _scene_elements(context)))
            except LuaConfigError:
                pass
        self.report({"INFO"}, f"deleted {deleted} removed element(s)")
        return {"FINISHED"}


class REBLEND_OT_select_element(bpy.types.Operator):
    """Make an element the active collection and select its objects (§6.3).

    The jump from a validation finding or a list row to the thing itself:
    sets the element's collection active (so the Active Element panel and
    ACTIVE-scoped operators point at it) and selects its selectable objects,
    with the registration empty as the active object when it exists.
    """

    bl_idname = "reblend.select_element"
    bl_label = "Select Element"

    path: bpy.props.StringProperty(
        name="Sprite Path",
        description="The re_path of the element to select",
    )

    def execute(self, context):
        collection = _collection_by_path(context.scene, self.path)
        if collection is None:
            self.report({"ERROR"}, f"no RE Element '{self.path}' in the scene")
            return {"CANCELLED"}

        layer = _find_layer_collection(context.view_layer.layer_collection,
                                       collection)
        if layer is not None:
            context.view_layer.active_layer_collection = layer

        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in collection.all_objects:
            with contextlib.suppress(RuntimeError):
                obj.select_set(True)
        empty = bpy.data.objects.get(str(collection.get("re_registration", "")))
        if empty is not None:
            context.view_layer.objects.active = empty
        return {"FINISHED"}


def _find_layer_collection(layer, collection):
    """The view layer's wrapper for ``collection`` (first hit), or None."""
    if layer.collection == collection:
        return layer
    for child in layer.children:
        found = _find_layer_collection(child, collection)
        if found is not None:
            return found
    return None


class REBLEND_OT_save_report(bpy.types.Operator):
    """Save the last validation/render report or Sync diff to a file (§6.3).

    The panels show these transiently and each run overwrites the last; this
    writes the same rows out as a dated text log (or JSON for diffing in
    review) so a validation state can be kept, shared, or attached to a bug.
    """

    bl_idname = "reblend.save_report"
    bl_label = "Save Report"

    source: bpy.props.EnumProperty(
        name="Source",
        items=(
            ("FINDINGS", "Validation / Render Report",
             "The findings list shown in the Validation Report panel"),
            ("SYNC", "Sync Log",
             "The last Sync diff with its per-item resolutions"),
        ),
        default="FINDINGS",
    )
    fmt: bpy.props.EnumProperty(
        name="Format",
        items=(
            ("TEXT", "Text", "Readable log (.txt)"),
            ("JSON", "JSON", "Structured document (.json), diffable in review"),
        ),
        default="TEXT",
    )
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.txt;*.json",
                                          options={"HIDDEN"})

    def invoke(self, context, event):
        settings = _settings(context)
        if self.source == "SYNC" and not len(settings.merge_items):
            self.report({"ERROR"}, "no Sync diff recorded — run Sync first")
            return {"CANCELLED"}
        if self.source == "FINDINGS" and not len(settings.findings):
            self.report({"ERROR"}, "no report recorded — run Validate first")
            return {"CANCELLED"}
        if not self.filepath:
            stem = ("reblend-sync" if self.source == "SYNC"
                    else f"reblend-{settings.findings_source or 'validation'}")
            ext = "json" if self.fmt == "JSON" else "txt"
            from datetime import datetime
            self.filepath = f"//{stem}-{datetime.now():%Y%m%d}.{ext}"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        settings = _settings(context)
        try:
            root = str(_project_root(context))
        except LuaConfigError:
            root = settings.project_root
        if self.source == "SYNC":
            rows, timestamp = settings.merge_items, settings.sync_time
            render = (reporting.merge_json if self.fmt == "JSON"
                      else reporting.format_merge)
            content = render(rows, project_root=root,
                             addon_version=__version__, timestamp=timestamp)
        else:
            rows, timestamp = settings.findings, settings.findings_time
            render = (reporting.findings_json if self.fmt == "JSON"
                      else reporting.format_findings)
            content = render(rows, kind=settings.findings_source or "validation",
                             project_root=root, addon_version=__version__,
                             timestamp=timestamp)
        if not rows:
            self.report({"ERROR"}, "nothing recorded to save")
            return {"CANCELLED"}

        path = Path(bpy.path.abspath(self.filepath))
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            self.report({"ERROR"}, f"could not write {path}: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"saved to {path}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# M2 — panel preview, state playground, flipbook, contact sheet (§5.3, §5.4)
# ---------------------------------------------------------------------------


def _show_image(context, name: str, pixels) -> "bpy.types.Image":
    """Put a top-down RGBA array into an image datablock, replacing any prior
    result, and point an open Image Editor at it (best effort)."""
    height, width = pixels.shape[0], pixels.shape[1]
    image = bpy.data.images.get(name)
    if image is not None and tuple(image.size) != (width, height):
        bpy.data.images.remove(image)
        image = None
    if image is None:
        image = bpy.data.images.new(name, width=width, height=height, alpha=True)
    image.alpha_mode = "STRAIGHT"
    # Data colorspace: the composited values are display-referred already
    # (they came out of finished sheets); Blender must not re-transform them.
    bpy_io.set_data_colorspace(image.colorspace_settings)
    bpy_io.write_pixels(image, pixels)
    _point_image_editor_at(context, image)
    return image


def _point_image_editor_at(context, image):
    """Show the image in the first open Image Editor; returns that space (its
    ``image_user`` drives sequence playback) or None headless/without one."""
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.spaces.active.image = image
                return area.spaces.active
    return None


def _active_element_sheet(op, context):
    """(data, strip pixels, frame_h) of the active element's rendered sheet,
    or None with the error already reported."""
    collection = context.collection
    if collection is None or not schema.is_element(collection):
        op.report({"ERROR"}, "active collection is not an RE Element")
        return None
    data = schema.props_to_data(collection)
    try:
        png = _project_root(context) / "GUI2D" / f"{data.path}.png"
    except LuaConfigError as exc:
        op.report({"ERROR"}, str(exc))
        return None
    if not png.is_file():
        op.report({"ERROR"}, f"no rendered sheet at {png} — render the element first")
        return None
    pixels = bpy_io.load_raw_pixels(png)
    frame_h = stitcher.frame_height(pixels.shape[0], data.frames)
    if frame_h is None:
        op.report(
            {"ERROR"},
            f"'{data.path}': sheet height {pixels.shape[0]} does not split "
            f"into re_frames ({data.frames}) equal slices — re-render or fix "
            "re_frames",
        )
        return None
    return data, pixels, frame_h


class REBLEND_OT_preview_panel(bpy.types.Operator):
    """Composite the rendered sheets into a full-panel preview image (§5.3).

    Each element shows its Preview Frame (the state playground sliders in the
    panel), so state combinations are checked before anything reaches the
    SDK. Mirrors RE2DPreview, but pre-export and per-state.
    """

    bl_idname = "reblend.preview_panel"
    bl_label = "Preview Panel"

    def execute(self, context):
        settings = _settings(context)
        panel = settings.preview_panel
        try:
            gui2d = _project_root(context) / "GUI2D"
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        entries, skipped = [], []
        for collection in _element_collections(context.scene):
            data = schema.props_to_data(collection)
            placements = [p for p in data.placements if p.panel == panel]
            if not placements:
                continue
            png = gui2d / f"{data.path}.png"
            if not png.is_file():
                skipped.append(f"{data.path} (not rendered)")
                continue
            preview_frame = int(collection.get("re_preview_frame", 0))
            entries.append((data, placements, png, preview_frame))
        if not entries:
            self.report(
                {"ERROR"},
                f"nothing to composite on '{panel}' — render sheets first",
            )
            return {"CANCELLED"}

        # Backdrop lowest, exactly as it sits in Reason; the rest by name for
        # a deterministic (and irrelevant — they don't overlap) paint order.
        entries.sort(key=lambda e: (e[0].kind != kinds.BACKDROP, e[0].path))
        layers = []
        for data, placements, png, preview_frame in entries:
            pixels = bpy_io.load_raw_pixels(png)
            frame_h = stitcher.frame_height(pixels.shape[0], data.frames)
            if frame_h is None:
                skipped.append(f"{data.path} (height not {data.frames} slices)")
                continue
            frame = min(max(preview_frame, 0), data.frames - 1)
            for placement in placements:
                layers.append(compositor.CompositeLayer(
                    pixels, frame_h, frame, placement.x, placement.y))

        size = calibration.panel_size_px(panel, settings.rack_units)
        canvas = compositor.composite_panel(size.width, size.height, layers)
        image = _show_image(context, f"RE Preview {panel}", canvas)

        message = f"composited {len(layers)} layer(s) into '{image.name}'"
        if skipped:
            message += f"; skipped {', '.join(skipped)}"
        self.report({"WARNING"} if skipped else {"INFO"}, message)
        return {"FINISHED"}


class REBLEND_OT_contact_sheet(bpy.types.Operator):
    """Grid of every frame of the active element's rendered sheet (§5.4) —
    at-a-glance QA for multi-state controls and sweep consistency."""

    bl_idname = "reblend.contact_sheet"
    bl_label = "Contact Sheet"

    columns: bpy.props.IntProperty(
        name="Columns",
        description="Grid columns; 0 picks a near-square layout",
        default=0,
        min=0,
    )

    def execute(self, context):
        sheet_source = _active_element_sheet(self, context)
        if sheet_source is None:
            return {"CANCELLED"}
        data, pixels, frame_h = sheet_source
        sheet = compositor.contact_sheet(pixels, frame_h, columns=self.columns)
        image = _show_image(context, f"RE Contact {data.path}", sheet)
        self.report(
            {"INFO"},
            f"contact sheet of {data.frames} frame(s) in '{image.name}'",
        )
        return {"FINISHED"}


class REBLEND_OT_flipbook(bpy.types.Operator):
    """Load the active element's sheet as a playable frame sequence (§5.4),
    so 61-frame smoothness is checked in the Image Editor before the SDK
    ever sees the file."""

    bl_idname = "reblend.flipbook"
    bl_label = "Flipbook"

    def execute(self, context):
        sheet_source = _active_element_sheet(self, context)
        if sheet_source is None:
            return {"CANCELLED"}
        data, pixels, frame_h = sheet_source
        if data.frames < 2:
            self.report({"INFO"}, f"'{data.path}' has 1 frame — nothing to play")
            return {"FINISHED"}

        # Drop the previous sequence datablock *before* rewriting its files
        # (a loaded sequence can pin them on Windows), then reuse one stable
        # per-sheet scratch dir so repeated flipbooks never pile up in temp.
        name = f"RE Flipbook {data.path}"
        existing = bpy.data.images.get(name)
        if existing is not None:
            bpy.data.images.remove(existing)
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in data.path)
        scratch = Path(tempfile.gettempdir()) / "reblend_flipbook" / safe
        scratch.mkdir(parents=True, exist_ok=True)
        for stale in scratch.glob("frame_*.png"):
            with contextlib.suppress(OSError):
                stale.unlink()
        for index, frame in enumerate(stitcher.split_strip(pixels, frame_h)):
            bpy_io.save_strip(frame, scratch / f"frame_{index + 1:04d}.png",
                              name=f"reblend_flip_{data.path}_{index}")

        image = bpy.data.images.load(str(scratch / "frame_0001.png"))
        image.name = name
        image.source = "SEQUENCE"
        bpy_io.set_data_colorspace(image.colorspace_settings)

        space = _point_image_editor_at(context, image)
        if space is not None:
            user = space.image_user
            user.frame_duration = data.frames
            user.frame_start = context.scene.frame_start
            user.use_cyclic = True
        self.report(
            {"INFO"},
            f"flipbook: {data.frames} frames in '{image.name}' — play or "
            "scrub the timeline in the Image Editor",
        )
        return {"FINISHED"}


class REBLEND_OT_launch_tool(bpy.types.Operator):
    """One-click RE2DRender / RE2DPreview on the linked project (§5.3).

    Tool paths are per-machine add-on preferences, never project data. The
    render output goes to RE2DRender_Output/ beside GUI2D/ so the generated
    build files never mix into the source sheets.
    """

    bl_idname = "reblend.launch_tool"
    bl_label = "Launch SDK Tool"

    tool: bpy.props.EnumProperty(
        name="Tool",
        items=(
            ("RENDER", "RE2DRender", "Compile GUI2D/ into the build format"),
            ("PREVIEW", "RE2DPreview", "Render the panels for a quick look"),
        ),
        default="RENDER",
    )
    #: RE2DRender's third argument. Left off entirely, the tool renders *only*
    #: the legacy lo-res (0.5x) form — which Reason/Recon 12 and later do not
    #: use — so RE-Blend never omits it. "hi-res-only" skips the lo-res pass
    #: and is the fast choice while iterating; "hi-res" produces both and is
    #: what a submission build wants.
    resolution: bpy.props.EnumProperty(
        name="Resolution",
        items=(
            ("hi-res-only", "Hi-res only",
             "Hi-res form only (Reason/Recon 12+); skips the lo-res pass"),
            ("hi-res", "Hi-res + lo-res",
             "Both forms — what a submission build needs"),
            ("lo-res", "Lo-res only (legacy)",
             "Omit the argument entirely: legacy lo-res form only"),
        ),
        default="hi-res-only",
    )

    def execute(self, context):
        preferences = props.tool_preferences(context)
        raw = ""
        if preferences is not None:
            raw = (preferences.re2drender_path if self.tool == "RENDER"
                   else preferences.re2dpreview_path)
        exe = Path(bpy.path.abspath(raw)) if raw else None
        if exe is None or not exe.is_file():
            self.report(
                {"ERROR"},
                "tool path not set — configure it per machine in "
                "Preferences > Add-ons > RE-Blend",
            )
            return {"CANCELLED"}

        try:
            root = _project_root(context)
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        gui2d = root / "GUI2D"
        if self.tool == "RENDER":
            out_dir = root / "RE2DRender_Output"
            out_dir.mkdir(parents=True, exist_ok=True)
            args = [str(exe), str(gui2d), str(out_dir)]
            if self.resolution != "lo-res":
                args.append(self.resolution)
        else:
            args = [str(exe), str(gui2d)]

        try:
            subprocess.Popen(args)
        except OSError as exc:
            self.report({"ERROR"}, f"failed to launch {exe.name}: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, "launched: " + " ".join(args))
        return {"FINISHED"}


class REBLEND_OT_install_sdk_parts(bpy.types.Operator):
    """Copy the SDK's stock 2D parts into the linked project's GUI2D.

    Sockets, the device-name tape, the back-panel placeholder, CV trim knobs
    and the patch/sample browse groups have a fixed appearance: the scripting
    specification says "you cannot change the appearance of this widget" and
    the GUI design guidelines make using the Reason Studios-supplied image a
    requirement. RE-Blend therefore never renders them — it installs them from
    the SDK the user points at in the add-on preferences, under whatever
    sprite name device_2D.lua already uses.
    """

    bl_idname = "reblend.install_sdk_parts"
    bl_label = "Install SDK Parts"
    bl_options = {"REGISTER", "UNDO"}

    overwrite: bpy.props.BoolProperty(
        name="Overwrite Existing",
        description="Replace sheets already present in GUI2D",
        default=False,
    )

    def execute(self, context):
        preferences = props.tool_preferences(context)
        raw = preferences.sdk_root if preferences is not None else ""
        sdk_root = Path(bpy.path.abspath(raw)) if raw else None
        if sdk_root is None or not sdk_root.is_dir():
            self.report(
                {"ERROR"},
                "SDK root not set — configure it per machine in "
                "Preferences > Add-ons > RE-Blend",
            )
            return {"CANCELLED"}

        try:
            root = _project_root(context)
            link = load_project(root)
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        installed, skipped, missing = [], [], []
        for panel_name, panel in link.hdgui.panels.items():
            for widget in panel.widgets:
                part = sdk_parts.stock_part_for_widget(widget.kind)
                if part is None or not widget.node:
                    continue
                node = link.device.node(panel_name, widget.node)
                if node is None or not node.graphics:
                    continue
                sprite = node.graphics[0].path
                if sprite in installed or sprite in skipped or sprite in missing:
                    continue  # one sheet may back several nodes
                if not self.overwrite and (link.gui2d_dir / f"{sprite}.png").is_file():
                    skipped.append(sprite)
                    continue
                try:
                    sdk_parts.install_stock_part(
                        part, sdk_root, link.gui2d_dir, sprite
                    )
                except (FileNotFoundError, OSError) as exc:
                    missing.append(sprite)
                    self.report({"WARNING"}, str(exc))
                else:
                    installed.append(sprite)

        if not installed and not skipped and not missing:
            self.report({"INFO"}, "no SDK-supplied parts in this project")
            return {"FINISHED"}
        summary = f"installed {len(installed)} SDK part(s)"
        if skipped:
            summary += f"; {len(skipped)} already present (enable Overwrite to replace)"
        if missing:
            severity = "ERROR" if not installed else "WARNING"
            self.report(
                {severity},
                summary + f"; not found under the SDK root: {', '.join(missing)}",
            )
            return {"FINISHED"}
        self.report({"INFO"}, summary)
        return {"FINISHED"}


def _refresh_reference_image(collection, data: schema.ElementData, settings,
                             image) -> bool:
    """Create or update the element's reference image empties; True when new.

    One image empty per placement, marked ``re_guide = "ref"`` so element
    deletion sweeps it and the guide-box logic ignores it. Positions are
    snapped absolute from the current effective placement on every run, so
    re-running the operator is also the repair path. The image displays at
    its native panel scale (pixel size over Pixels/Unit); the empty sits at
    the frame centre, or the image's own centre when the element is unsized.
    """
    refs = sorted((o for o in collection.objects if o.get("re_guide") == "ref"),
                  key=lambda o: o.name)
    placements = data.effective_placements
    for surplus in refs[len(placements):]:
        bpy.data.objects.remove(surplus, do_unlink=True)
    refs = refs[:len(placements)]

    created = False
    iw, ih = int(image.size[0]), int(image.size[1])
    for index, placement in enumerate(placements):
        if index < len(refs):
            obj = refs[index]
        else:
            obj = bpy.data.objects.new(f"ref_{data.path}_{index}", None)
            obj.empty_display_type = "IMAGE"
            obj.empty_image_offset = (-0.5, -0.5)   # centred on the empty
            obj.hide_render = True
            obj.hide_select = True
            obj["re_guide"] = "ref"
            # Panels live in the world XZ plane (calibration): stand the
            # image up to match, facing the front camera on -Y.
            obj.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
            collection.objects.link(obj)
            created = True
        obj.data = image
        # Blender draws an image empty with its larger side spanning the
        # display size, aspect preserved — exact for the one-frame sheets
        # SDK parts use, since the sheet then *is* the frame.
        obj.empty_display_size = max(iw, ih) / settings.ppb
        if data.has_frame_size:
            cx, cy = calibration.element_center_px(
                placement.x, placement.y, data.frame_w, data.frame_h)
        else:
            cx, cy = (placement.x + iw / 2.0, placement.y + ih / 2.0)
        origin = _origin_offset(settings, placement.panel)
        world = Vector(
            calibration.panel_px_to_world(cx, cy, settings.ppb, origin))
        # The camera axis is free: panel-pixel math only uses X/Z, so a user
        # depth offset (e.g. pulling the image in front of the backdrop to
        # avoid z-fighting) is theirs to keep across refreshes. New empties
        # start on the panel plane.
        world.y = obj.location.y
        obj.location = world
    return created


class REBLEND_OT_reference_images(bpy.types.Operator):
    """Show the project's fixed art in the viewport as image empties (§5.3).

    SDK-supplied parts — sockets, the device-name tape, the back-panel
    placeholder, the browse groups — are never rendered by RE-Blend, so
    their elements normally sit in the scene as bare wireframe boxes. This
    drops each such part's ``GUI2D/<path>.png`` into its collection as a
    non-rendering image empty at true panel scale, so the complete panel
    picture is visible while placing the tape, jacks and the rest.

    Image empties rather than textured planes, deliberately: they need no
    material, display in every shading mode, respect the PNG's alpha, and
    cannot leak into a render. Run Install SDK Parts first so the stock art
    exists in GUI2D; re-running this refreshes both images and positions.
    """

    bl_idname = "reblend.reference_images"
    bl_label = "Add Reference Images"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            root = _project_root(context)
        except LuaConfigError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        settings = _settings(context)
        added = updated = 0
        missing: list[str] = []
        for collection in _element_collections(context.scene):
            data = _element_snapshot(collection, settings)
            if kinds.renders_art(data.kind) or not data.path:
                continue
            png = root / "GUI2D" / f"{data.path}.png"
            if not png.is_file():
                missing.append(data.path)
                continue
            image = bpy.data.images.load(str(png), check_existing=True)
            image.reload()  # pick up art replaced on disk since the last run
            if not image.size[0] or not image.size[1]:
                missing.append(data.path)
                continue
            was_new = _refresh_reference_image(collection, data, settings, image)
            added += was_new
            updated += not was_new

        if not added and not updated and not missing:
            self.report({"INFO"}, "no SDK-supplied elements in the scene")
            return {"FINISHED"}
        summary = f"{added} reference image(s) added, {updated} refreshed"
        if missing:
            self.report(
                {"WARNING"},
                summary + f"; no usable PNG in GUI2D for: {', '.join(missing)}"
                          " — Install SDK Parts provides the stock art",
            )
        else:
            self.report({"INFO"}, summary)
        return {"FINISHED"}


CLASSES = (
    REBLEND_OT_import_project,
    REBLEND_OT_validate,
    REBLEND_OT_set_frame_size,
    REBLEND_OT_scale_to_bounds,
    REBLEND_OT_render_elements,
    REBLEND_OT_generate_rig,
    REBLEND_OT_add_state_action,
    REBLEND_OT_remove_state_action,
    REBLEND_OT_set_state_value,
    REBLEND_OT_spread_state_values,
    REBLEND_OT_capture_state_value,
    REBLEND_OT_rename_state,
    REBLEND_OT_reverse_states,
    REBLEND_OT_repair_state_channels,
    REBLEND_OT_copy_driver_reference,
    REBLEND_OT_show_state,
    REBLEND_OT_generate_all_rigs,
    REBLEND_OT_export_patch,
    REBLEND_OT_sync_project,
    REBLEND_OT_apply_sync,
    REBLEND_OT_delete_element,
    REBLEND_OT_purge_removed,
    REBLEND_OT_select_element,
    REBLEND_OT_save_report,
    REBLEND_OT_preview_panel,
    REBLEND_OT_contact_sheet,
    REBLEND_OT_flipbook,
    REBLEND_OT_launch_tool,
    REBLEND_OT_install_sdk_parts,
    REBLEND_OT_reference_images,
)
