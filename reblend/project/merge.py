"""Re-import merge (§6.1): diff a fresh project read against the scene.

Re-running Import on a linked scene must not silently clobber the designer's
state, and must not silently ignore upstream edits either. This module is the
pure half of that sync: compare the freshly parsed :class:`ElementSpec` list
with the scene's current :class:`ElementData` snapshots and produce one
:class:`MergeItem` per sprite path that differs —

- **added**: in the Lua, not in the scene → import materialises it,
- **removed**: in the scene, not in the Lua → flagged; never deleted
  automatically, but the designer can resolve the item as an explicit,
  confirmed delete (the Blender layer sweeps the element and its rig),
- **changed**: both sides, values differ → per-item accept-theirs/keep-mine.

"Keep mine" is meaningful because M2 also brings patch-mode export: keeping a
scene-side offset and then exporting writes *it* into ``device_2D.lua``.

Applying resolutions is the Blender layer's job; nothing here imports ``bpy``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..model.schema import ElementData
from .link import ElementSpec

__all__ = ["ADDED", "REMOVED", "CHANGED", "FieldChange", "MergeItem", "diff_link"]

ADDED = "added"
REMOVED = "removed"
CHANGED = "changed"


@dataclass(frozen=True)
class FieldChange:
    """One differing field: the scene's value (mine) vs the file's (theirs)."""

    field: str
    mine: str
    theirs: str

    def __str__(self) -> str:
        return f"{self.field}: mine {self.mine} / theirs {self.theirs}"


@dataclass
class MergeItem:
    """One sprite path's difference between scene and project files."""

    path: str
    status: str
    spec: ElementSpec | None = None       # the file side (None when removed)
    element: ElementData | None = None    # the scene side (None when added)
    changes: tuple[FieldChange, ...] = ()

    @property
    def summary(self) -> str:
        if self.status == ADDED:
            # "In the Lua, not in the scene" — deliberately not "new": the
            # node may be years old, and a scene-side delete resurfaces it
            # here too, because the Lua owns existence. Retiring a control
            # for good means removing it from the Lua contract itself.
            return (f"in the Lua, not in the scene: {self.spec.kind}, "
                    f"{self.spec.frames} frame(s)")
        if self.status == REMOVED:
            return "no longer in Lua — kept until you delete it"
        return "; ".join(str(change) for change in self.changes)


def diff_link(
    specs: Sequence[ElementSpec], elements: Sequence[ElementData]
) -> list[MergeItem]:
    """Diff parsed specs (theirs) against scene elements (mine), by path.

    Deterministic order: changed/removed items follow the scene's element
    order, added items follow the file's spec order.
    """
    spec_by_path = {spec.path: spec for spec in specs}
    element_by_path = {element.path: element for element in elements if element.path}

    items: list[MergeItem] = []
    for element in elements:
        if not element.path:
            continue
        spec = spec_by_path.get(element.path)
        if spec is None:
            items.append(MergeItem(element.path, REMOVED, element=element))
            continue
        changes = _field_changes(spec, element)
        if changes:
            items.append(
                MergeItem(element.path, CHANGED, spec=spec, element=element,
                          changes=changes)
            )
    for spec in specs:
        if spec.path not in element_by_path:
            items.append(MergeItem(spec.path, ADDED, spec=spec))
    return items


def _field_changes(spec: ElementSpec, element: ElementData) -> tuple[FieldChange, ...]:
    changes: list[FieldChange] = []
    if int(spec.frames) != int(element.frames):
        changes.append(FieldChange("frames", str(element.frames), str(spec.frames)))
    if spec.kind != element.kind:
        changes.append(FieldChange("kind", element.kind, spec.kind))

    # "Mine" is where the scene *now* puts the element — a dragged registration
    # empty is a scene-side change like any other, and the whole point of
    # keep-mine is that exporting then writes it into the Lua.
    theirs = tuple((p.panel, p.node, float(p.x), float(p.y)) for p in spec.placements)
    mine = tuple(
        (p.panel, p.node, float(p.x), float(p.y))
        for p in element.effective_placements
    )
    if theirs != mine:
        changes.append(
            FieldChange("placements", _placements_str(mine), _placements_str(theirs))
        )

    # Frame size is scene-owned (§5.2) — the Lua never carries it. Only a
    # probed-from-disk spec size (nonzero) that contradicts a set scene size
    # counts as a change; an unsized side never overrides a sized one.
    if (
        spec.frame_w and spec.frame_h and element.has_frame_size
        and (spec.frame_w, spec.frame_h) != (element.frame_w, element.frame_h)
    ):
        changes.append(
            FieldChange(
                "frame size",
                f"{element.frame_w}x{element.frame_h}",
                f"{spec.frame_w}x{spec.frame_h}",
            )
        )
    return tuple(changes)


def _placements_str(placements) -> str:
    return (
        "["
        + ", ".join(
            f"{panel}/{node}@({x:g}, {y:g})" for panel, node, x, y in placements
        )
        + "]"
    )
