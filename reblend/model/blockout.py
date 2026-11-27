"""Blockout geometry from RE Bench's sidecar shapes (pure Python, no ``bpy``).

When RE Bench laid a device out first, ``GUI2D/rebench.json`` carries a frame
size and a rough shape for each control (``{"type": "knob", "diameter_px":
110}``). Import turns that into a simple mesh at the element's registration
empty, so modelling starts from the agreed size instead of an empty guide box.

The mesh is centred on the registration empty, lies on the panel plane (world
X wide, Z tall, see :func:`reblend.model.calibration.panel_px_to_world`) and
stands out towards the camera. Sizes are panel pixels divided by Pixels/Unit,
so a blockout renders at exactly its bench size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["BlockoutMesh", "blockout_mesh"]

Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class BlockoutMesh:
    kind: str
    verts: list[Vec3]
    faces: list[tuple[int, ...]]


def _box(w: float, h: float, d: float, toward: float) -> tuple[list[Vec3], list[tuple[int, ...]]]:
    hx, hz = w / 2, h / 2
    verts = [(x, y, z) for y in (0.0, toward * d) for z in (-hz, hz) for x in (-hx, hx)]
    # 0..3 back (on the panel), 4..7 front (towards the camera)
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return verts, faces


def _cylinder(r: float, d: float, toward: float, segments: int):
    verts: list[Vec3] = []
    for y in (0.0, toward * d):
        for i in range(segments):
            a = 2 * math.pi * i / segments
            verts.append((r * math.cos(a), y, r * math.sin(a)))
    n = segments
    faces: list[tuple[int, ...]] = [tuple(range(n)), tuple(range(2 * n - 1, n - 1, -1))]
    faces += [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    return verts, faces


def blockout_mesh(shape: dict, frame_w: int, frame_h: int, ppb: float,
                  toward_camera: float = -1.0, segments: int = 32) -> BlockoutMesh | None:
    """Vertices and faces for a sidecar shape, or None without a shape or size.

    ``toward_camera`` is the sign of world Y pointing at the camera (-1 for the
    default ``neg_y`` camera).
    """
    if not shape or frame_w <= 0 or frame_h <= 0 or ppb <= 0:
        return None
    kind = str(shape.get("type", "box"))
    w, h = frame_w / ppb, frame_h / ppb
    side = min(w, h)
    if kind == "knob":
        diameter = float(shape.get("diameter_px", 0)) / ppb or side * 0.9
        verts, faces = _cylinder(diameter / 2, diameter * 0.35, toward_camera, segments)
    elif kind == "button":
        verts, faces = _box(w * 0.8, h * 0.8, side * 0.2, toward_camera)
    elif kind == "fader":
        # the handle at frame 0 (the bottom of a vertical fader, the left of a horizontal one)
        if h >= w:
            hh = min(h, w * 0.8)
            verts, faces = _box(w, hh, side * 0.4, toward_camera)
            verts = [(x, y, z - (h - hh) / 2) for x, y, z in verts]
        else:
            hw = min(w, h * 0.8)
            verts, faces = _box(hw, h, side * 0.4, toward_camera)
            verts = [(x - (w - hw) / 2, y, z) for x, y, z in verts]
    else:
        kind = "box"
        verts, faces = _box(w * 0.9, h * 0.9, side * 0.15, toward_camera)
    return BlockoutMesh(kind, verts, faces)
