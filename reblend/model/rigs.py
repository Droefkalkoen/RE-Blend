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


def apply_state_table(table: state_tables.StateTable) -> int:
    """Write a state table as constant-interpolation keyframes (§4.3).

    Compilation validates totality (every state sets every channel) before
    anything is touched, so a bad table changes nothing.

    Keys the table owns that fall *outside* ``0…frames−1`` are removed:
    shrinking ``re_frames`` from 8 to 3 used to leave five orphan keys behind,
    which the render never visits (it renders exactly those frames) but the
    viewport does — so the preview stopped matching the sheet, and growing the
    count again silently resurrected stale poses. Returns how many were pruned.
    """
    keys = table.compile()
    touched: set[tuple[str, str, str, int]] = set()

    for key in keys:
        block = _resolve_block(key.id_type, key.target)
        block, data_path = _hop_embedded(block, key.data_path)
        _set_value(block, data_path, key.index, key.value)
        if isinstance(key.value, tuple):
            for component in range(len(key.value)):
                block.keyframe_insert(data_path=data_path, index=component, frame=key.frame)
        else:
            block.keyframe_insert(data_path=data_path, index=key.index, frame=key.frame)
        touched.add((key.id_type, key.target, key.data_path, key.index))

    pruned = 0
    for id_type, target, data_path, index in touched:
        block = _resolve_block(id_type, target)
        block, data_path = _hop_embedded(block, data_path)
        pruned += _finish_channel(block, data_path, index, table.frames)
    return pruned


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

    name = state_tables.id_property_of(data_path)
    if name is not None:
        if name not in block.keys():
            raise KeyError(f"'{target}' has no custom property '{name}'")
        return float(block[name])

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
    name = state_tables.id_property_of(data_path)
    if name is not None:
        # A driver value the table owns: create it on first apply so
        # regenerating a rig on a fresh file restores it rather than failing.
        block[name] = float(value)
        return
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
    if not head:
        return block, head, attr
    try:
        owner = block.path_resolve(head)
    except ValueError as exc:
        raise KeyError(_unresolved_message(block, data_path)) from exc
    return owner, head, attr


def _unresolved_message(block, data_path: str) -> str:
    """Explain a dead path in the designer's terms, with what *is* there.

    Blender's own "path could not be resolved" names the whole path and no
    alternatives, which is the least useful moment to be terse: the usual cause
    is a socket that exists under a different name on a different shader (an
    Emission node's ``Strength`` is a Principled BSDF's ``Emission Strength``).
    """
    node_name = state_tables.node_of(data_path)
    socket = state_tables.socket_of(data_path)
    if node_name is None or socket is None:
        return f"'{block.name}' has no '{data_path}'"

    nodes = getattr(block, "nodes", None)
    node = nodes.get(node_name) if nodes is not None else None
    if node is None:
        available = ", ".join(sorted(n.name for n in nodes)) if nodes else "none"
        return (
            f"'{block.name}' has no shader node '{node_name}' "
            f"(nodes present: {available})"
        )
    inputs = ", ".join(f"'{socket_in.name}'" for socket_in in node.inputs)
    return (
        f"node '{node_name}' on '{block.name}' has no input '{socket}' — "
        f"its inputs are: {inputs}"
    )


def _finish_channel(block, data_path: str, index: int, frames: int) -> int:
    """Force constant interpolation and drop keys outside ``0…frames−1``.

    Both passes filter on ``array_index`` when the channel addresses one
    component, so keying a fader handle's Z never touches the designer's own
    keys on X or Y.
    """
    anim = block.animation_data
    if anim is None or anim.action is None:
        return 0
    pruned = 0
    for fcurve in _fcurves(anim.action):
        if fcurve.data_path != data_path:
            continue
        if index >= 0 and fcurve.array_index != index:
            continue
        stale = [
            point for point in fcurve.keyframe_points
            if not 0 <= round(point.co[0]) <= frames - 1
        ]
        for point in reversed(stale):
            fcurve.keyframe_points.remove(point)
        pruned += len(stale)
        for point in fcurve.keyframe_points:
            point.interpolation = "CONSTANT"
        if stale:
            fcurve.update()
    return pruned


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
