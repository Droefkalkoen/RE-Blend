"""Palette, material starters and lighting rig (design §5.7)."""

import bpy
import pytest


def test_load_palette_builds_the_group(imported, empty_scene):
    (imported / "Design").mkdir()
    (imported / "Design" / "palette.json").write_text(
        '{"Amber Signal": "#E0A24B", "Graphite": "#1A2026"}')
    assert bpy.ops.reblend.load_palette("EXEC_DEFAULT") == {"FINISHED"}
    group = bpy.data.node_groups["RE Palette"]
    names = [s.name for s in group.interface.items_tree if s.in_out == "OUTPUT"]
    assert names == ["Amber Signal", "Graphite"]
    rgb = next(n for n in group.nodes if n.type == "RGB" and n.name.startswith("Amber"))
    assert rgb.outputs[0].default_value[0] == pytest.approx(0.7454, abs=1e-3)
    # Reloading replaces, not duplicates.
    assert bpy.ops.reblend.load_palette("EXEC_DEFAULT") == {"FINISHED"}
    assert len([g for g in bpy.data.node_groups if g.name.startswith("RE Palette")]) == 1


@pytest.mark.parametrize("key", ["BRUSHED", "ANODISED", "GLASS", "PLASTIC", "KNURL"])
def test_material_starters_build_and_assign(empty_scene, key):
    from reblend.model import kit

    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    empty_scene.reblend.material_starter = key
    assert bpy.ops.reblend.add_material_starter("EXEC_DEFAULT") == {"FINISHED"}
    material = cube.active_material
    assert material["re_starter"] == key
    group_node = next(n for n in material.node_tree.nodes if n.type == "GROUP")
    assert group_node.node_tree is kit.starter_group(key)  # built once, shared
    assert "Color" in group_node.inputs and "Roughness" in group_node.inputs


def test_lighting_rig_lights_the_panel_from_the_viewer_side(empty_scene, tmp_path):
    from mathutils import Vector

    from reblend.model import calibration

    empty_scene.reblend.camera_axis = "neg_y"
    assert bpy.ops.reblend.add_lighting_rig("EXEC_DEFAULT") == {"FINISHED"}
    rig = bpy.data.collections["RE Lighting Rig"]
    assert {o.name for o in rig.objects} == {"RE Key", "RE Fill", "RE Rim"}
    toward = Vector(calibration.axis_vector("neg_y"))
    key = rig.objects["RE Key"]
    assert key.location.normalized().dot(toward) > 0.5          # in front of the panel
    assert key.location.z > 0 and key.location.x < 0           # upper left
    aim = key.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    assert aim.dot(-key.location.normalized()) > 0.99          # pointing at the centre
    # Rebuilding keeps one rig.
    assert bpy.ops.reblend.add_lighting_rig("EXEC_DEFAULT") == {"FINISHED"}
    assert len(rig.objects) == 3

    # Save as a template, then append it into a fresh scene.
    path = str(tmp_path / "rig.blend")
    assert bpy.ops.reblend.save_rig_template("EXEC_DEFAULT", filepath=path) == {"FINISHED"}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.reblend.rig_template = path
    assert bpy.ops.reblend.add_lighting_rig("EXEC_DEFAULT") == {"FINISHED"}
    assert {o.name for o in bpy.data.collections["RE Lighting Rig"].objects} == {
        "RE Key", "RE Fill", "RE Rim"}
