"""Render inputs as a hash, and the render manifest (§5.5, §7).

Dirty-only rendering needs to know whether anything that reaches an element's
pixels changed since its sheet was written. :func:`element_fingerprint` hashes
what a render of one element reads:

- the element's own ``re_*`` properties (frame size, frames, states, owner…);
- every object in the element and its ``<element> context`` collection:
  transform, visibility, modifiers, object data (mesh vertices, curve and
  text settings, light settings), materials with their node trees and the
  files and mtimes of the images they use, and keyframes and drivers;
- scene-wide inputs: render engine and samples, view settings, the world,
  every light and camera-visible object outside the elements, and the
  RE-Blend render settings;
- in Cast Shadows isolation, the neighbouring elements whose geometry falls
  inside this element's frame (they shadow it); a neighbour elsewhere on the
  panel does not dirty it.

It errs towards "dirty": anything it cannot read is hashed by type and name,
so the worst case is an unneeded render, never a stale sheet. ``render --all``
is always available when in doubt.

The manifest (``GUI2D/reblend-manifest.json``) records, per sheet, the input
fingerprint it was rendered from and the SHA-256 of the PNG written. A sheet is
dirty when it is missing from the manifest, its PNG is missing or no longer
matches the recorded hash (someone overwrote it), or the fingerprint changed.

Only :func:`element_fingerprint` and its helpers need ``bpy``; the manifest
half is pure Python and tested without Blender.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = [
    "MANIFEST_FILENAME",
    "SheetRecord",
    "Manifest",
    "file_sha256",
    "element_fingerprint",
    "is_dirty",
]

MANIFEST_FILENAME = "reblend-manifest.json"
MANIFEST_VERSION = 1


# ---------------------------------------------------------------------------
# Manifest (pure Python)
# ---------------------------------------------------------------------------


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class SheetRecord:
    element: str
    fingerprint: str
    sha256: str
    frames: int
    frame_w: int
    frame_h: int
    rendered: str
    #: The derived sheets written from this element's frames, path -> sha256.
    derived: dict = field(default_factory=dict)


@dataclass
class Manifest:
    sheets: dict[str, SheetRecord] = field(default_factory=dict)
    reblend: str = ""
    blend: str = ""
    version: int = MANIFEST_VERSION

    @classmethod
    def path_for(cls, gui2d_dir: Path | str) -> Path:
        return Path(gui2d_dir) / MANIFEST_FILENAME

    @classmethod
    def load(cls, gui2d_dir: Path | str) -> "Manifest":
        path = cls.path_for(gui2d_dir)
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()  # unreadable: everything is dirty, which is safe
        if int(raw.get("version", 0)) > MANIFEST_VERSION:
            return cls()
        known = set(SheetRecord.__dataclass_fields__)
        sheets = {
            path: SheetRecord(**{k: v for k, v in rec.items() if k in known})
            for path, rec in (raw.get("sheets") or {}).items()
        }
        return cls(sheets=sheets, reblend=str(raw.get("reblend", "")),
                   blend=str(raw.get("blend", "")))

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "reblend": self.reblend,
            "blend": self.blend,
            "sheets": {path: asdict(rec) for path, rec in sorted(self.sheets.items())},
        }

    def save(self, gui2d_dir: Path | str) -> Path:
        path = self.path_for(gui2d_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return path

    def record(self, path: str, rec: SheetRecord) -> None:
        self.sheets[path] = rec


def is_dirty(manifest: Manifest, path: str, fingerprint: str,
             gui2d_dir: Path | str) -> str | None:
    """Why the sheet at ``path`` needs rendering, or None when it is current."""
    rec = manifest.sheets.get(path)
    if rec is None:
        return "not in the manifest"
    png = Path(gui2d_dir) / f"{path}.png"
    if not png.is_file():
        return "no sheet on disk"
    if file_sha256(png) != rec.sha256:
        return "the sheet on disk changed since it was rendered"
    for derived, digest in rec.derived.items():
        derived_png = Path(gui2d_dir) / f"{derived}.png"
        if not derived_png.is_file() or file_sha256(derived_png) != digest:
            return f"derived sheet '{derived}' is missing or changed"
    if rec.fingerprint != fingerprint:
        return "its inputs changed"
    return None


# ---------------------------------------------------------------------------
# Fingerprint (needs bpy)
# ---------------------------------------------------------------------------

_SIMPLE = {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}
#: RNA properties that change without affecting a render (selection, UI).
_IGNORED = {
    "rna_type", "name_full", "select", "is_evaluated", "original", "users",
    "use_fake_user", "is_runtime_data", "session_uid", "tag", "is_editmode",
    "show_expanded", "show_viewport", "show_in_editmode", "show_on_cage",
    "is_override_data", "library_weak_reference", "preview", "location_ui",
    "hide_select", "is_active", "select_head", "select_tail",
}


def _rna_values(struct, depth: int = 0) -> list:
    """Every simple RNA property of ``struct`` (and one level of nested
    structs), as a stable list of (name, value)."""
    out = []
    try:
        props = struct.bl_rna.properties
    except AttributeError:
        return [repr(struct)]
    for prop in props:
        ident = prop.identifier
        if ident in _IGNORED:
            continue
        try:
            value = getattr(struct, ident)
        except (AttributeError, RuntimeError, TypeError):
            continue
        if prop.type in _SIMPLE:
            if getattr(prop, "is_array", False) or (
                    prop.type in {"INT", "FLOAT", "BOOLEAN"} and getattr(prop, "array_length", 0)):
                try:
                    value = [round(v, 6) if isinstance(v, float) else v for v in value]
                except TypeError:
                    pass
            elif isinstance(value, float):
                value = round(value, 6)
            elif isinstance(value, set):
                value = sorted(value)
            out.append((ident, value))
        elif prop.type == "POINTER" and depth == 0 and value is not None:
            if hasattr(value, "name_full"):
                out.append((ident, value.name_full))  # an ID: by name only
            elif not ident.startswith(("bl_", "id_")):
                out.append((ident, _rna_values(value, depth + 1)))
    return out


def _socket_value(socket):
    value = getattr(socket, "default_value", None)
    if value is None:
        return None
    try:
        return [round(float(v), 6) for v in value]
    except TypeError:
        return round(value, 6) if isinstance(value, float) else value


def _node_tree(tree, seen: set) -> list:
    if tree is None or tree.name_full in seen:
        return []
    seen.add(tree.name_full)
    out = [("tree", tree.name_full)]
    for node in sorted(tree.nodes, key=lambda n: n.name):
        entry = [node.bl_idname, node.name, _rna_values(node)]
        entry.append([(s.identifier, _socket_value(s)) for s in node.inputs])
        image = getattr(node, "image", None)
        if image is not None:
            entry.append(_image(image))
        group = getattr(node, "node_tree", None)
        if group is not None:
            entry.append(_node_tree(group, seen))
        out.append(entry)
    out.append(sorted((l.from_node.name, l.from_socket.identifier,
                       l.to_node.name, l.to_socket.identifier) for l in tree.links))
    out.append(_animation(tree))
    return out


def _image(image) -> list:
    import bpy

    entry = ["image", image.name_full, image.source, image.filepath]
    if image.packed_file is not None:
        entry.append(image.packed_file.size)
    else:
        try:
            entry.append(os.path.getmtime(bpy.path.abspath(image.filepath)))
        except OSError:
            entry.append("missing")
    return entry


def _animation(block) -> list:
    anim = getattr(block, "animation_data", None)
    if anim is None:
        return []
    out = []
    action = anim.action
    if action is not None:
        from ..model.rigs import _fcurves

        for fcurve in _fcurves(action):
            out.append((fcurve.data_path, fcurve.array_index,
                        [(round(p.co[0], 4), round(p.co[1], 6), p.interpolation)
                         for p in fcurve.keyframe_points]))
    for fcurve in anim.drivers:
        driver = fcurve.driver
        out.append(("driver", fcurve.data_path, fcurve.array_index, driver.type,
                    driver.expression,
                    [(v.name, v.type, [(t.id.name_full if t.id else "", t.data_path,
                                        t.transform_type)
                                       for t in v.targets]) for v in driver.variables]))
    return out


def _mesh_hash(mesh) -> str:
    import numpy as np

    digest = hashlib.sha256()
    count = len(mesh.vertices)
    co = np.empty(count * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    digest.update(co.tobytes())
    loops = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loops)
    digest.update(loops.tobytes())
    digest.update(str([m.name_full if m else "" for m in mesh.materials]).encode())
    if mesh.shape_keys is not None:
        for key in mesh.shape_keys.key_blocks:
            kco = np.empty(count * 3, dtype=np.float32)
            key.data.foreach_get("co", kco)
            digest.update(kco.tobytes())
            digest.update(str(round(key.value, 6)).encode())
        digest.update(str(_animation(mesh.shape_keys)).encode())
    return digest.hexdigest()


def _object(obj, seen_trees: set) -> list:
    entry = [obj.name_full, obj.type,
             [round(v, 6) for row in obj.matrix_world for v in row],
             obj.hide_render,
             [(a, getattr(obj, a, None)) for a in (
                 "visible_camera", "visible_diffuse", "visible_glossy",
                 "visible_transmission", "visible_volume_scatter", "visible_shadow",
                 "is_shadow_catcher", "is_holdout")],
             [(m.type, _rna_values(m)) for m in obj.modifiers],
             _animation(obj),
             sorted((k, str(v)) for k, v in obj.items())]
    data = obj.data
    if data is not None:
        if obj.type == "MESH":
            entry.append(_mesh_hash(data))
        else:
            entry.append(_rna_values(data))
            entry.append(_animation(data))
        if obj.type == "LIGHT":
            entry.append(_node_tree(getattr(data, "node_tree", None), seen_trees))
    for slot in obj.material_slots:
        material = slot.material
        if material is None:
            continue
        entry.append([material.name_full, _rna_values(material), _animation(material),
                      _node_tree(material.node_tree, seen_trees)])
    return entry


def _scene(scene, settings) -> list:
    out = [scene.render.engine, _rna_values(scene.view_settings),
           [(a, getattr(settings, a, None)) for a in (
               "ppb", "camera_axis", "inactive_render", "caster_bounce",
               "panel_isolation", "alpha_floor")]]
    cycles = getattr(scene, "cycles", None)
    if cycles is not None:
        out.append([(a, getattr(cycles, a, None)) for a in (
            "samples", "use_denoising", "max_bounces", "film_exposure",
            "pixel_filter_type", "filter_width", "device", "seed")])
    eevee = getattr(scene, "eevee", None)
    if eevee is not None:
        out.append([(a, getattr(eevee, a, None)) for a in (
            "taa_render_samples", "use_shadows", "use_raytracing")])
    world = scene.world
    if world is not None:
        out.append([world.name_full, _rna_values(world), _node_tree(world.node_tree, set())])
    return out


def _element_objects(collection) -> list:
    import bpy

    objs = list(collection.all_objects)
    context = bpy.data.collections.get(f"{collection.name} context")
    if context is not None:
        objs += list(context.all_objects)
    return objs


def element_fingerprint(scene, collection, settings, *, element_objects: set,
                        neighbour_objects=()) -> str:
    """SHA-256 over everything a render of ``collection`` reads.

    ``element_objects`` is the set of object names that belong to *any* RE
    Element (or a context collection): objects outside it are scene objects
    (lights, a room, a backdrop prop) that light every element.
    ``neighbour_objects`` are other elements' objects that can shadow this
    one; the caller decides which (see :func:`neighbours_in_frame`).
    """
    seen_trees: set = set()
    parts: list = [
        sorted((k, str(v)) for k, v in collection.items()
               if k.startswith("re_") and k != "re_preview_frame"),
        [_object(o, seen_trees) for o in sorted(_element_objects(collection),
                                                 key=lambda o: o.name_full)],
        _scene(scene, settings),
        [_object(o, seen_trees) for o in sorted(scene.objects, key=lambda o: o.name_full)
         if o.name not in element_objects and not o.name.startswith("reblend_cam_")
         and (o.type == "LIGHT" or not o.hide_render)],
        [_object(o, seen_trees) for o in sorted(neighbour_objects, key=lambda o: o.name_full)],
    ]
    text = json.dumps(parts, default=str, sort_keys=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
