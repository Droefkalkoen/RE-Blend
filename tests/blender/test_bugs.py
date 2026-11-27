"""Regression tests for the Skou feedback bugs that need a live Blender."""

import numpy as np
import pytest

bpy = pytest.importorskip("bpy")

from reblend.model import rigs  # noqa: E402
from reblend.model.state_tables import State, StateTable, location  # noqa: E402
from reblend.ui import operators, props  # noqa: E402


def test_preview_image_refills_after_save(tmp_path):
    """#2: a saved (file-backed) preview image is recreated, not refilled."""
    pixels = np.zeros((20, 30, 4), dtype=np.float32)
    image = operators._show_image(bpy.context, "RE Preview front", pixels)
    image.filepath_raw = str(tmp_path / "preview.png")
    image.file_format = "PNG"
    image.save()
    pixels[..., 3] = 1.0
    again = operators._show_image(bpy.context, "RE Preview front", pixels)
    assert again.source == "GENERATED"
    assert len(again.pixels) == 20 * 30 * 4


def test_merge_resolution_is_scriptable(imported):
    """#4: a script can read and set every diff row's choice."""
    from reblend.project import merge

    settings = bpy.context.scene.reblend
    props.store_merge_items(settings, [
        merge.MergeItem("a", merge.CHANGED, "moved"),
        merge.MergeItem("b", merge.REMOVED, "gone"),
    ])
    rows = {row.path: row for row in settings.merge_items}
    assert rows["a"].resolution == "THEIRS"
    assert rows["b"].resolution == "MINE"
    rows["a"].resolution = "MINE"
    rows["b"].resolution = "DELETE"
    assert rows["a"].resolution_ui == "MINE"
    assert rows["b"].resolution_ui == "DELETE"
    # Re-running the diff keeps the choices made.
    props.store_merge_items(settings, [merge.MergeItem("a", merge.CHANGED, "moved")])
    assert settings.merge_items[0].resolution == "MINE"


def test_apply_sync_refuses_unknown_choice(imported):
    from reblend.project import merge

    settings = bpy.context.scene.reblend
    props.store_merge_items(settings, [merge.MergeItem("a", merge.CHANGED, "moved")])
    settings.merge_items[0].resolution = "BOTH"
    with pytest.raises(RuntimeError, match="unknown choice"):
        bpy.ops.reblend.apply_sync("EXEC_DEFAULT")


def _keyed_object(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def test_generate_rig_does_not_rewrite_a_copy_s_original():
    """#22/#34: keying a copy that shares the original's action leaves the
    original's keys alone."""
    original = _keyed_object("dim_key")
    original.location = (0, 0, 0)
    original.keyframe_insert("location", index=2, frame=0)
    original.location = (0, 0, -1)
    original.keyframe_insert("location", index=2, frame=1)
    copy = _keyed_object("pol_key")
    copy.animation_data_create()
    copy.animation_data.action = original.animation_data.action
    if hasattr(original.animation_data, "action_slot"):
        copy.animation_data.action_slot = original.animation_data.action_slot

    table = StateTable(states=[
        State("off", (location("pol_key", axis=2, value=0.5),)),
        State("on", (location("pol_key", axis=2, value=0.7),)),
        State("x", (location("pol_key", axis=2, value=0.9),)),
    ])
    rigs.apply_state_table(table)

    assert copy.animation_data.action != original.animation_data.action
    scene = bpy.context.scene
    scene.frame_set(1)
    assert original.location.z == pytest.approx(-1.0)
    assert copy.location.z == pytest.approx(0.7)
    scene.frame_set(2)
    assert copy.location.z == pytest.approx(0.9)


def test_revalidate_drivers_reenables_invalid_ones():
    """#6: a driver Blender marked invalid is re-enabled."""
    obj = _keyed_object("lamp")
    fcurve = obj.driver_add("location", 0)
    fcurve.driver.expression = "1.0"
    fcurve.driver.is_valid = False
    fixed = rigs.revalidate_drivers([obj])
    assert fixed == ["lamp: location"]
    assert fcurve.driver.is_valid


def test_validate_runs_the_layout_rules_too(imported, empty_scene):
    """One rule engine: Blender's Validate reports the bench's layout rules
    (fold arrow, routing icons, screws ...), not only the art/rig checks."""
    import json

    from reblend.project import catalogue, layout

    (imported / "GUI2D" / "rebench.json").write_text(json.dumps({"version": 3, "art": [
        {"panel": "back", "kind": "screw", "name": "bad", "x": 80, "y": 20}]}))
    assert bpy.ops.reblend.validate("EXEC_DEFAULT") == {"FINISHED"}
    codes = {f.code for f in empty_scene.reblend.findings}
    assert "rack-screws" in codes
    assert all(c in catalogue.RULES for c in codes), codes - set(catalogue.RULES)
    assert layout.check_rules  # same module the bench runs


def test_centre_selected_measures_hidden_text_shown(imported, empty_scene):
    """#35: a hidden text object's bounds are read shown, so a changed body
    centres correctly (its stale bounds put it off-panel)."""
    from mathutils import Vector

    from reblend.model import schema

    collection = next(c for c in bpy.data.collections if schema.is_element(c)
                      and c.get("re_registration"))
    empty = bpy.data.objects[collection["re_registration"]]
    curve = bpy.data.curves.new("legend", "FONT")
    curve.body = "A"
    text = bpy.data.objects.new("legend", curve)
    collection.objects.link(text)
    text.location = empty.matrix_world.translation + Vector((0.3, 0.2, 0.0))
    bpy.context.view_layer.update()
    text.hide_viewport = True
    curve.body = "A MUCH LONGER LEGEND"  # bounds go stale while hidden

    with bpy.context.temp_override(collection=collection, selected_objects=[text]):
        assert bpy.ops.reblend.centre_selected("EXEC_DEFAULT") == {"FINISHED"}
    assert text.hide_viewport  # restored

    from reblend.ui import operators
    _axis, u, v = operators._camera_basis(collection, empty_scene.reblend)
    corners = operators.world_bounds_unhidden(bpy.context, [text])["legend"]
    target = empty.matrix_world.translation
    us = [c.dot(u) for c in corners]
    vs = [c.dot(v) for c in corners]
    assert abs((min(us) + max(us)) / 2 - target.dot(u)) < 1e-4
    assert abs((min(vs) + max(vs)) / 2 - target.dot(v)) < 1e-4


def test_art_variants_switch_as_a_group(imported, empty_scene):
    """#16/#20: alternative art in child collections, one in use at a time."""
    from reblend.model import schema
    from reblend.ui import operators

    collection = next(c for c in bpy.data.collections if schema.is_element(c))
    objs = []
    for name in ("silk_3d", "silk_overlay"):
        obj = bpy.data.objects.new(name, None)
        collection.objects.link(obj)
        objs.append(obj)
    with bpy.context.temp_override(collection=collection, selected_objects=[objs[0]]):
        assert bpy.ops.reblend.add_variant("EXEC_DEFAULT", name="3D") == {"FINISHED"}
    with bpy.context.temp_override(collection=collection, selected_objects=[objs[1]]):
        assert bpy.ops.reblend.add_variant("EXEC_DEFAULT", name="Overlay") == {"FINISHED"}
        with pytest.raises(RuntimeError, match="already used"):
            bpy.ops.reblend.add_variant("EXEC_DEFAULT", name="Overlay")
    variants = {c["re_variant"]: c for c in operators.element_variants(collection)}
    assert set(variants) == {"3D", "Overlay"}
    assert collection["re_variant"] == "Overlay"
    assert variants["3D"].hide_render and not variants["Overlay"].hide_render
    with bpy.context.temp_override(collection=collection):
        assert bpy.ops.reblend.use_variant("EXEC_DEFAULT", name="3D") == {"FINISHED"}
    assert not variants["3D"].hide_render and variants["Overlay"].hide_render
    assert objs[0].name in collection.all_objects  # still the element's objects


def test_live_overlap_hint_while_moving(imported, empty_scene):
    """#33: moving an element's registration empty onto another element
    reports the overlap straight away, before any Validate."""
    from reblend.model import kinds, schema
    from reblend.ui import operators

    elements = [c for c in bpy.data.collections if schema.is_element(c)
                and c.get("re_kind") != kinds.BACKDROP and c.get("re_registration")]
    by_panel = {}
    for c in elements:
        by_panel.setdefault(schema.props_to_data(c).placements[0].panel, []).append(c)
    a, b = next(group for group in by_panel.values() if len(group) >= 2)[:2]
    for c in (a, b):
        c["re_frame_w"], c["re_frame_h"] = 50, 50
    ea = bpy.data.objects[a["re_registration"]]
    eb = bpy.data.objects[b["re_registration"]]
    ea.matrix_world = eb.matrix_world.copy()
    bpy.context.view_layer.update()
    assert operators.overlap_message(bpy.context, a) == \
        f"overlaps {schema.props_to_data(b).node}"

    # The handler fills the hint for the active registration empty. (The
    # module build of Blender runs no depsgraph handlers, so call it with
    # the update Blender would pass.)
    from types import SimpleNamespace

    bpy.context.view_layer.objects.active = ea
    update = SimpleNamespace(id=SimpleNamespace(original=ea), is_updated_transform=True)
    operators.live_overlap_handler(empty_scene, SimpleNamespace(updates=[update]))
    assert operators.LIVE_OVERLAP["element"] == a.name
    assert operators.LIVE_OVERLAP["message"].startswith("overlaps")
    assert operators.live_overlap_handler in bpy.app.handlers.depsgraph_update_post
