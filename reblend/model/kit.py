"""The palette and material kit (design §5.7), built in Blender.

- :func:`palette_group`: the project palette as a shader node group,
  ``RE Palette``, with one colour output per swatch, so an accent is picked
  from the group instead of retyped;
- :func:`material_starter`: brushed and anodised metal, dark glass, plastic
  cap and machined knurl, each a node group (``RE <Starter>``) with a few
  named inputs, used by a new material;
- :func:`lighting_rig`: the studio-hardware panel light (a soft key at about
  35 degrees elevation from the upper left, a low cool fill, a faint rim) in a
  collection of its own, laid out around the camera axis; a project can save
  its own rig as a template .blend and add that instead.

Imports ``bpy``; only the UI operators call it.
"""

from __future__ import annotations

import math
from typing import Sequence

import bpy
from mathutils import Vector

from . import calibration

__all__ = ["STARTERS", "palette_group", "material_starter", "lighting_rig",
           "RIG_COLLECTION", "PALETTE_GROUP"]

PALETTE_GROUP = "RE Palette"
RIG_COLLECTION = "RE Lighting Rig"

#: key -> (label, description). Defaults live in the builders below.
STARTERS = {
    "BRUSHED": ("Brushed Metal", "Anisotropic metal with a directional grain"),
    "ANODISED": ("Anodised Metal", "Coloured metal with a thin clear coat"),
    "GLASS": ("Dark Glass", "Glossy dark glass for meter and display windows"),
    "PLASTIC": ("Plastic Cap", "Satin plastic for knob caps and buttons"),
    "KNURL": ("Machined Knurl", "Metal with a diamond knurl, for knob grips"),
}


# --- palette ---------------------------------------------------------------

def palette_group(swatches: Sequence) -> bpy.types.ShaderNodeTree:
    """Create or refresh ``RE Palette``: one Color output per swatch, in order."""
    group = bpy.data.node_groups.get(PALETTE_GROUP)
    if group is None:
        group = bpy.data.node_groups.new(PALETTE_GROUP, "ShaderNodeTree")
    group.nodes.clear()
    group.interface.clear()
    out = group.nodes.new("NodeGroupOutput")
    out.location = (400, 0)
    for i, swatch in enumerate(swatches):
        group.interface.new_socket(swatch.name, in_out="OUTPUT", socket_type="NodeSocketColor")
        rgb = group.nodes.new("ShaderNodeRGB")
        rgb.label = rgb.name = f"{swatch.name} {swatch.hex}"
        rgb.location = (0, -i * 200)
        rgb.outputs[0].default_value = (*swatch.linear, 1.0)
        group.links.new(rgb.outputs[0], out.inputs[i])
    group["re_palette"] = {s.name: s.hex for s in swatches}
    return group


# --- material starters -----------------------------------------------------

def _group_io(group, inputs) -> tuple:
    group.interface.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    for name, kind, default, lo, hi in inputs:
        sock = group.interface.new_socket(name, in_out="INPUT", socket_type=kind)
        sock.default_value = default
        if lo is not None:
            sock.min_value, sock.max_value = lo, hi
    gin = group.nodes.new("NodeGroupInput")
    gin.location = (-900, 0)
    gout = group.nodes.new("NodeGroupOutput")
    gout.location = (500, 0)
    bsdf = group.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (100, 0)
    group.links.new(bsdf.outputs["BSDF"], gout.inputs["Shader"])
    return gin, bsdf


def _bump(group, height_socket, strength_socket, bsdf, location=(-150, -400)):
    bump = group.nodes.new("ShaderNodeBump")
    bump.location = location
    group.links.new(height_socket, bump.inputs["Height"])
    group.links.new(strength_socket, bump.inputs["Strength"])
    group.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return bump


def _build_starter(key: str) -> bpy.types.ShaderNodeTree:
    label = STARTERS[key][0]
    group = bpy.data.node_groups.new(f"RE {label}", "ShaderNodeTree")
    col = "NodeSocketColor"
    flt = "NodeSocketFloat"
    link = group.links.new
    if key == "BRUSHED":
        gin, bsdf = _group_io(group, [
            ("Color", col, (0.38, 0.42, 0.46, 1.0), None, None),
            ("Roughness", flt, 0.3, 0.0, 1.0),
            ("Grain Direction", flt, 0.0, 0.0, 1.0),
            ("Grain Scale", flt, 400.0, 1.0, 10000.0),
            ("Grain Depth", flt, 0.05, 0.0, 1.0),
        ])
        bsdf.inputs["Metallic"].default_value = 1.0
        bsdf.inputs["Anisotropic"].default_value = 0.8
        link(gin.outputs["Grain Direction"], bsdf.inputs["Anisotropic Rotation"])
        coords = group.nodes.new("ShaderNodeTexCoord")
        coords.location = (-900, -400)
        stretch = group.nodes.new("ShaderNodeMapping")
        stretch.location = (-700, -400)
        stretch.inputs["Scale"].default_value = (1.0, 0.01, 1.0)  # long streaks along X
        link(coords.outputs["Object"], stretch.inputs["Vector"])
        noise = group.nodes.new("ShaderNodeTexNoise")
        noise.location = (-450, -400)
        link(stretch.outputs["Vector"], noise.inputs["Vector"])
        link(gin.outputs["Grain Scale"], noise.inputs["Scale"])
        _bump(group, noise.outputs["Fac"], gin.outputs["Grain Depth"], bsdf)
    elif key == "ANODISED":
        gin, bsdf = _group_io(group, [
            ("Color", col, (0.05, 0.09, 0.16, 1.0), None, None),
            ("Roughness", flt, 0.35, 0.0, 1.0),
            ("Coat", flt, 0.3, 0.0, 1.0),
        ])
        bsdf.inputs["Metallic"].default_value = 1.0
        link(gin.outputs["Coat"], bsdf.inputs["Coat Weight"])
    elif key == "GLASS":
        gin, bsdf = _group_io(group, [
            ("Color", col, (0.01, 0.012, 0.015, 1.0), None, None),
            ("Roughness", flt, 0.03, 0.0, 1.0),
            ("Reflection", flt, 0.5, 0.0, 1.0),
        ])
        bsdf.inputs["Metallic"].default_value = 0.0
        bsdf.inputs["Coat Weight"].default_value = 1.0
        link(gin.outputs["Reflection"], bsdf.inputs["Specular IOR Level"])
    elif key == "PLASTIC":
        gin, bsdf = _group_io(group, [
            ("Color", col, (0.02, 0.022, 0.025, 1.0), None, None),
            ("Roughness", flt, 0.45, 0.0, 1.0),
            ("Coat", flt, 0.0, 0.0, 1.0),
        ])
        bsdf.inputs["Metallic"].default_value = 0.0
        link(gin.outputs["Coat"], bsdf.inputs["Coat Weight"])
    elif key == "KNURL":
        gin, bsdf = _group_io(group, [
            ("Color", col, (0.42, 0.45, 0.48, 1.0), None, None),
            ("Roughness", flt, 0.35, 0.0, 1.0),
            ("Knurl Scale", flt, 60.0, 1.0, 1000.0),
            ("Knurl Depth", flt, 0.3, 0.0, 1.0),
        ])
        bsdf.inputs["Metallic"].default_value = 1.0
        coords = group.nodes.new("ShaderNodeTexCoord")
        coords.location = (-900, -400)
        waves = []
        for i, angle in enumerate((45.0, -45.0)):
            rot = group.nodes.new("ShaderNodeMapping")
            rot.location = (-700, -300 - i * 300)
            rot.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(angle))
            link(coords.outputs["Object"], rot.inputs["Vector"])
            wave = group.nodes.new("ShaderNodeTexWave")
            wave.location = (-450, -300 - i * 300)
            wave.wave_profile = "TRI"
            link(rot.outputs["Vector"], wave.inputs["Vector"])
            link(gin.outputs["Knurl Scale"], wave.inputs["Scale"])
            waves.append(wave)
        peak = group.nodes.new("ShaderNodeMath")
        peak.operation = "MAXIMUM"
        peak.location = (-250, -450)
        link(waves[0].outputs["Fac"], peak.inputs[0])
        link(waves[1].outputs["Fac"], peak.inputs[1])
        _bump(group, peak.outputs["Value"], gin.outputs["Knurl Depth"], bsdf,
              location=(-50, -450))
    link(gin.outputs["Color"], bsdf.inputs["Base Color"])
    link(gin.outputs["Roughness"], bsdf.inputs["Roughness"])
    group["re_starter"] = key
    return group


def starter_group(key: str) -> bpy.types.ShaderNodeTree:
    """The starter's node group, built once per file."""
    for group in bpy.data.node_groups:
        if group.get("re_starter") == key:
            return group
    return _build_starter(key)


def material_starter(key: str, name: str | None = None) -> bpy.types.Material:
    """A new material that is the starter's group wired to the output."""
    if key not in STARTERS:
        raise ValueError(f"unknown material starter {key!r}")
    group = starter_group(key)
    material = bpy.data.materials.new(name or STARTERS[key][0])
    if material.node_tree is None:
        material.use_nodes = True  # Blender 4.x; 5.x materials always use nodes
    nodes = material.node_tree.nodes
    nodes.clear()
    node = nodes.new("ShaderNodeGroup")
    node.node_tree = group
    node.location = (0, 0)
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    material.node_tree.links.new(node.outputs["Shader"], out.inputs["Surface"])
    material["re_starter"] = key
    return material


# --- lighting rig ----------------------------------------------------------

def _frame(camera_axis: str) -> tuple[Vector, Vector, Vector]:
    """(towards viewer, screen right, screen up) for a camera axis."""
    d = Vector(calibration.axis_vector(camera_axis)).normalized()
    world_up = Vector((0.0, 1.0, 0.0)) if abs(d.z) > 0.9 else Vector((0.0, 0.0, 1.0))
    quat = d.to_track_quat("Z", "Y")
    right = quat @ Vector((1.0, 0.0, 0.0))
    up = quat @ Vector((0.0, 1.0, 0.0))
    if up.dot(world_up) < 0:
        right, up = -right, -up
    return d, right, up


def _light(collection, name, kind, colour, energy, size, position, target):
    data = bpy.data.lights.new(name, kind)
    data.color = colour
    data.energy = energy
    if kind == "AREA":
        data.shape = "DISK"
        data.size = size
    obj = bpy.data.objects.new(name, data)
    obj.location = position
    obj.rotation_euler = (target - position).to_track_quat("-Z", "Y").to_euler()
    collection.objects.link(obj)
    return obj


def lighting_rig(scene, camera_axis: str, ppb: float,
                 centre: Sequence[float] = (0.0, 0.0, 0.0)) -> bpy.types.Collection:
    """Create (or rebuild) the ``RE Lighting Rig`` collection around ``centre``."""
    old = bpy.data.collections.get(RIG_COLLECTION)
    if old is not None:
        for obj in list(old.objects):
            data = obj.data
            bpy.data.objects.remove(obj)
            if data is not None and data.users == 0:
                bpy.data.lights.remove(data)
        collection = old
    else:
        collection = bpy.data.collections.new(RIG_COLLECTION)
        scene.collection.children.link(collection)
    toward, right, up = _frame(camera_axis)
    target = Vector(centre)
    width = calibration.PANEL_WIDTH_PX / max(ppb, 1e-6)
    distance = 0.8 * width
    energy = 40.0 * distance * distance

    def direction(elevation_deg, azimuth_deg):
        el, az = math.radians(elevation_deg), math.radians(azimuth_deg)
        across = (up * math.cos(az) + right * math.sin(az)).normalized()
        return (toward * math.sin(el) + across * math.cos(el)).normalized()

    # Key: upper left, 35 degrees above the panel plane, large and soft.
    _light(collection, "RE Key", "AREA", (1.0, 0.98, 0.95), energy, 0.6 * width,
           target + direction(35.0, -40.0) * distance, target)
    # Fill: lower right, low and cool, about a fifth of the key.
    _light(collection, "RE Fill", "AREA", (0.82, 0.89, 1.0), 0.2 * energy, 0.8 * width,
           target + direction(20.0, 150.0) * distance, target)
    # Rim: grazing from the top edge, faint, to pick out bevels.
    _light(collection, "RE Rim", "AREA", (1.0, 1.0, 1.0), 0.1 * energy, 0.3 * width,
           target + direction(8.0, 0.0) * distance, target)
    collection["re_rig"] = camera_axis
    return collection


def append_rig_template(path: str, name: str = RIG_COLLECTION) -> bpy.types.Collection:
    """Append a saved rig collection from a .blend and link it to the scene."""
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        if name not in src.collections:
            raise ValueError(f"{path} has no collection '{name}'")
        dst.collections = [name]
    collection = dst.collections[0]
    bpy.context.scene.collection.children.link(collection)
    return collection


def save_rig_template(path: str, collection: bpy.types.Collection) -> None:
    """Write the rig collection (and its lights) to a .blend of its own."""
    bpy.data.libraries.write(path, {collection}, fake_user=True)
