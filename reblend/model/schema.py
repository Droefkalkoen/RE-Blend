"""The RE Element schema: versioned ``re_*`` custom properties (§4.2, §8).

An RE Element is a Blender collection carrying ``re_*`` custom properties.
This module is the single authority on which properties exist, their
defaults, and how old property sets migrate forward — every ``re_*`` schema
carries a version int and migrations run on file load, because ``.blend``
files outlive add-on versions and retrofitting migrations is miserable.

Kept pure on purpose: properties are modelled as plain mappings (Blender's
IDProperties behave like one), so schema logic and migrations are testable
without ``bpy``. The bridge to real collections lives in the Blender-side
modules, which do nothing schema-wise beyond get/set on the mapping.

Placements: the design table (§4.2) gives an element one node/offset, but an
element used on several panels (the On/Off/Bypass fader, the DeviceName tape)
is *one* collection referenced from each — one sheet, several placements. The
full placement list is stored as JSON under ``re_placements``; the singular
``re_node`` / ``re_panel`` / ``re_offset_*`` properties mirror the primary
(first) placement for display and simple access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, MutableMapping

from . import kinds

__all__ = [
    "SCHEMA_VERSION",
    "DEFAULTS",
    "Placement",
    "ElementData",
    "MIGRATIONS",
    "is_element",
    "migrate",
    "data_to_props",
    "props_to_data",
]

#: Current schema version. Bump on any property change and add a migration.
SCHEMA_VERSION = 2

#: Property names and their defaults at the current schema version.
DEFAULTS: dict[str, Any] = {
    "re_schema": SCHEMA_VERSION,
    "re_node": "",            # primary device_2D node name
    "re_path": "",            # sprite PNG basename (device_2D `path`)
    "re_kind": kinds.STATIC,  # one of kinds.ALL_KINDS
    "re_frames": 1,
    "re_frame_w": 0,          # 0 = not yet decided (validation flags it)
    "re_frame_h": 0,
    "re_panel": "",           # primary panel
    "re_offset_x": 0,         # primary placement, panel px, top-left origin
    "re_offset_y": 0,
    "re_registration": "",    # name of the element's registration empty (§4.2)
    "re_sweep_deg": 300.0,    # knob sweep, -sweep/2..+sweep/2 (§4.3)
    "re_states": "",          # state table JSON (state_tables module), "" = none
    "re_placements": "[]",    # JSON list of [panel, node, x, y]
    "re_preview_frame": 0,    # frame shown in the panel preview (§5.3)
}


@dataclass(frozen=True)
class Placement:
    """One appearance of the element: a node at an offset on a panel.

    Offsets are absolute panel pixels (top-left origin, +y down) — nested
    device_2D group offsets are already folded in.
    """

    panel: str
    node: str
    x: float
    y: float


@dataclass
class ElementData:
    """A schema-shaped snapshot of one element, decoupled from ``bpy``.

    This is what validation (§6.3) consumes: the Blender side turns each
    element collection into one of these; tests build them directly.
    """

    node: str
    path: str
    kind: str = kinds.STATIC
    frames: int = 1
    frame_w: int = 0
    frame_h: int = 0
    placements: tuple[Placement, ...] = field(default_factory=tuple)
    #: The element's ``re_states`` JSON verbatim, so validation can check the
    #: state table (state count, a fader's even travel) without ``bpy``.
    states: str = ""

    @property
    def panels(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for placement in self.placements:
            seen.setdefault(placement.panel, None)
        return tuple(seen)

    @property
    def has_frame_size(self) -> bool:
        return self.frame_w > 0 and self.frame_h > 0


def is_element(props: MutableMapping[str, Any] | Any) -> bool:
    """Whether a property mapping marks its owner as an RE Element."""
    try:
        return "re_path" in props or "re_node" in props
    except TypeError:
        return False


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

#: version present in the file -> function that lifts props to version + 1.
#: Version 0 is "pre-schema": elements tagged by hand in the M0 era, carrying
#: some re_* keys but no re_schema. Lifting fills in current defaults.
MIGRATIONS: dict[int, Callable[[MutableMapping[str, Any]], None]] = {}


def _migration(version: int):
    def register(fn: Callable[[MutableMapping[str, Any]], None]):
        MIGRATIONS[version] = fn
        return fn

    return register


@_migration(0)
def _lift_pre_schema(props: MutableMapping[str, Any]) -> None:
    """v0 → v1: fill defaults for every missing property."""
    for key, value in DEFAULTS.items():
        if key not in props:
            props[key] = value


@_migration(1)
def _add_preview_frame(props: MutableMapping[str, Any]) -> None:
    """v1 → v2: the state-playground preview frame (§5.3) joins the schema."""
    if "re_preview_frame" not in props:
        props["re_preview_frame"] = 0


def migrate(props: MutableMapping[str, Any]) -> bool:
    """Bring an element's properties up to :data:`SCHEMA_VERSION` in place.

    Returns True when anything changed. Raises :class:`ValueError` for a
    schema *newer* than this add-on understands — silently downgrading a
    file written by a newer RE-Blend would corrupt it.
    """
    version = int(props.get("re_schema", 0))
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"element schema v{version} is newer than this RE-Blend (v{SCHEMA_VERSION}); "
            "upgrade the add-on before editing this file"
        )
    if version == SCHEMA_VERSION:
        return False
    while version < SCHEMA_VERSION:
        MIGRATIONS[version](props)
        version += 1
        props["re_schema"] = version
    return True


# ---------------------------------------------------------------------------
# props <-> ElementData
# ---------------------------------------------------------------------------


def data_to_props(data: ElementData) -> dict[str, Any]:
    """Full current-version property mapping for an element."""
    primary = data.placements[0] if data.placements else Placement("", data.node, 0, 0)
    props = dict(DEFAULTS)
    props.update(
        re_node=data.node or primary.node,
        re_path=data.path,
        re_kind=data.kind,
        re_frames=int(data.frames),
        re_frame_w=int(data.frame_w),
        re_frame_h=int(data.frame_h),
        re_panel=primary.panel,
        re_offset_x=primary.x,
        re_offset_y=primary.y,
        re_placements=json.dumps(
            [[p.panel, p.node, p.x, p.y] for p in data.placements]
        ),
    )
    return props


def props_to_data(props: MutableMapping[str, Any]) -> ElementData:
    """Read an element's properties (any migratable version) into data.

    The stored ``re_placements`` list is authoritative; the singular
    ``re_panel``/``re_node``/``re_offset_*`` mirror is the fallback for
    property sets that predate (or lost) the JSON list.
    """
    working = dict(props)
    migrate(working)

    placements: list[Placement] = []
    try:
        raw = json.loads(working["re_placements"])
    except (json.JSONDecodeError, TypeError):
        raw = []
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, list) and len(entry) == 4:
            placements.append(
                Placement(str(entry[0]), str(entry[1]), float(entry[2]), float(entry[3]))
            )
    if not placements and (working["re_panel"] or working["re_node"]):
        placements.append(
            Placement(
                str(working["re_panel"]),
                str(working["re_node"]),
                float(working["re_offset_x"]),
                float(working["re_offset_y"]),
            )
        )

    return ElementData(
        node=str(working["re_node"]),
        path=str(working["re_path"]),
        kind=str(working["re_kind"]),
        frames=int(working["re_frames"]),
        frame_w=int(working["re_frame_w"]),
        frame_h=int(working["re_frame_h"]),
        placements=tuple(placements),
        states=str(working.get("re_states", "")),
    )
