"""Rig generators: apply the pure rig descriptions to a live scene (§4.3).

Two rig flavours exist (:func:`reblend.model.kinds.rig_for_kind`):

- **Turntable driver** (knobs): a rotation driver on the rotating part —
  scene frame 0 → min angle, frame ``frames − 1`` → max angle, linear, around
  the registration empty's axis. Regenerating on every ``re_frames`` change
  is the whole point: the rig can never silently diverge from the frame
  count baked into the sheet.
- **State keyframes** (buttons/faders/selectors/lamps): the element's
  compiled :class:`~reblend.model.state_tables.StateTable` written as
  constant-interpolation keyframes, so scrubbing the timeline previews
  exactly the discrete sheet.

The only module in ``model/`` that imports ``bpy``.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import bpy

from . import calibration, state_tables

__all__ = [
    "ensure_turntable_driver",
    "clear_turntable_driver",
    "apply_state_table",
    "read_channel_value",
]


def ensure_turntable_driver(
    rotor: "bpy.types.Object",
    frames: int,
    sweep_deg: float = calibration.DEFAULT_SWEEP_DEG,
    axis: Sequence[float] = (0.0, -1.0, 0.0),
) -> None:
    """(Re)create the knob rotation driver on ``rotor``.

    ``axis`` is the world-space rotation axis (the registration empty's
    axis; −Y faces the viewer under the §4.4 convention). The rotor's origin
    must sit on that axis — that is what the registration empty marks.
    ``rotation_euler`` is driven in the rotor's local frame, which equals
    world for an un-rotated rotor (the M0-proven case).
    """
    if frames < 2:
        raise ValueError(f"a knob needs at least 2 frames, got {frames}")
    index, sign = calibration.dominant_axis(tuple(axis))

    rotor.rotation_mode = "XYZ"
    clear_turntable_driver(rotor)
    fcurve = rotor.driver_add("rotation_euler", index)
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    half = sweep_deg / 2.0
    driver.expression = (
        f"radians({sign} * (-{half} + {sweep_deg} * frame / {frames - 1}))"
    )


def clear_turntable_driver(rotor: "bpy.types.Object") -> None:
    rotor.driver_remove("rotation_euler")


def apply_state_table(table: state_tables.StateTable) -> None:
    """Write a state table as constant-interpolation keyframes (§4.3).

    Compilation validates totality (every state sets every channel) before
    anything is touched, so a bad table changes nothing.
    """
    keys = table.compile()
    touched: set[tuple[str, str, str]] = set()

    for key in keys:
        block = _resolve_block(key.id_type, key.target)
        block, data_path = _hop_embedded(block, key.data_path)
        _set_value(block, data_path, key.index, key.value)
        if isinstance(key.value, tuple):
            for component in range(len(key.value)):
                block.keyframe_insert(data_path=data_path, index=component, frame=key.frame)
        else:
            block.keyframe_insert(data_path=data_path, index=key.index, frame=key.frame)
        touched.add((key.id_type, key.target, key.data_path))

    for id_type, target, data_path in touched:
        block = _resolve_block(id_type, target)
        block, data_path = _hop_embedded(block, data_path)
        _make_constant(block, data_path)


def read_channel_value(channel: state_tables.Channel):
    """The value a state-table channel currently holds in the scene (§4.3).

    The inverse of :func:`_set_value`, so a designer can pose the object in the
    viewport and capture that pose into a state instead of typing coordinates.
    Raises the same :class:`KeyError` as applying a table when the datablock or
    path is gone, so a stale channel reports the same way in both directions.
    """
    id_type, target, data_path, index = channel
    block = _resolve_block(id_type, target)
    block, data_path = _hop_embedded(block, data_path)
    owner, _, attr = _rna_split(block, data_path)
    try:
        current = getattr(owner, attr)
    except AttributeError as exc:
        raise KeyError(f"'{target}' has no property '{data_path}'") from exc
    if hasattr(current, "__len__") and not isinstance(current, str):
        if index >= 0:
            return float(current[index])
        return tuple(float(component) for component in current)
    return float(current)


def _resolve_block(id_type: str, target: str):
    collection = getattr(bpy.data, id_type, None)
    if collection is None or target not in collection:
        raise KeyError(f"bpy.data.{id_type}[{target!r}] does not exist in this file")
    return collection[target]


#: Path prefixes that cross into an embedded/owned ID, where the animation
#: data actually lives — keyframe_insert must be called on the owning ID, and
#: a path through the boundary fails with "path spans ID blocks".
_EMBEDDED_HOPS = (
    ("node_tree.", lambda block: block.node_tree),
    ("data.shape_keys.", lambda block: block.data.shape_keys),
)


def _hop_embedded(block, data_path: str):
    for prefix, hop in _EMBEDDED_HOPS:
        if data_path.startswith(prefix):
            owner = hop(block)
            if owner is None:
                raise KeyError(f"'{block.name}' has no {prefix.rstrip('.')} to animate")
            return owner, data_path[len(prefix):]
    return block, data_path


def _set_value(block, data_path: str, index: int, value) -> None:
    owner, _, attr = _rna_split(block, data_path)
    current = getattr(owner, attr)
    if isinstance(value, tuple):
        setattr(owner, attr, value)
    elif index >= 0 and hasattr(current, "__len__"):
        current[index] = value
    elif isinstance(current, bool):
        setattr(owner, attr, bool(value))
    else:
        setattr(owner, attr, value)


def _rna_split(block, data_path: str):
    """Resolve a data path to (owner, path, final attribute name)."""
    head, _, attr = data_path.rpartition(".")
    owner = block.path_resolve(head) if head else block
    return owner, head, attr


def _make_constant(block, data_path: str) -> None:
    anim = block.animation_data
    if anim is None or anim.action is None:
        return
    for fcurve in _fcurves(anim.action):
        if fcurve.data_path == data_path:
            for point in fcurve.keyframe_points:
                point.interpolation = "CONSTANT"


def _fcurves(action) -> Iterable:
    # Blender 4.4+ layered actions keep fcurves on channelbags; 4.2 LTS keeps
    # them directly on the action. Support both.
    if getattr(action, "fcurves", None) is not None:
        yield from action.fcurves
        return
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for channelbag in strip.channelbags:
                yield from channelbag.fcurves
