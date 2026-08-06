"""Shadow ownership: what every collection does during one element's render (§5.1).

Two questions decide how a scene is set up for a render, and they are not the
same question:

- **Isolation** — how the elements that are *not* being rendered behave, so
  only the active one lands in the sheet. That is a scene-wide preference
  (:data:`INACTIVE_SHADOW` / :data:`INACTIVE_HIDDEN`): keep the neighbours in
  as shadow-only casters so they still ground the active element, or take
  them out entirely.
- **Shadow ownership** — where an element's own cast shadow ends up: baked
  into the panel plate underneath it (``kinds.SHADOW_BACKGROUND``) or rendered
  into the element's own sheet (``kinds.SHADOW_ELEMENT``). That is per element,
  because the right answer is a fact about the element rather than a taste:
  art that holds still across its frames can bake its shadow into the plate,
  art whose silhouette moves cannot — the baked shadow would sit frozen at one
  position while the art slides away from it.

Ownership is a two-sided switch, which is why it cannot be folded into the
isolation setting. It is read off the *neighbour* when rendering the plate
(only ``background`` owners cast into someone else's sheet) and off the
*active element* when rendering that element (an ``element`` owner turns the
plate beneath it into a shadow catcher, so its shadow arrives as alpha in its
own frames).

One rule keeps the result unambiguous: **an element that owns its shadow gets
exactly one shadow in its sheet — its own.** Every other caster is taken out
for that render. A neighbour's shadow is already baked into the plate, so
catching it here as well would draw it twice and darken the overlap.

Pure on purpose: the policy is the part worth testing, and it needs no
``bpy``. :mod:`reblend.render.renderer` applies the roles to real collections.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from ..model import kinds
from ..model.schema import Travel

__all__ = [
    "INACTIVE_SHADOW",
    "INACTIVE_HIDDEN",
    "ROLE_VISIBLE",
    "ROLE_HIDDEN",
    "ROLE_CASTER",
    "ROLE_CATCHER",
    "NEEDS_CYCLES",
    "sibling_role",
    "travel_from_samples",
]

#: Isolation modes (the scene-wide "Inactive Elements" setting).
INACTIVE_SHADOW = "SHADOW"
INACTIVE_HIDDEN = "HIDDEN"

#: What one collection does during the active element's render.
#:
#: - ``ROLE_VISIBLE`` — renders normally: the active element and its context.
#: - ``ROLE_HIDDEN`` — excluded from the render; contributes nothing.
#: - ``ROLE_CASTER`` — in the render but invisible to the camera, still
#:   dropping shadows onto the active element (Cycles ray visibility).
#: - ``ROLE_CATCHER`` — the plate beneath an element that owns its shadow:
#:   transparent except where that shadow falls (Cycles shadow catcher).
ROLE_VISIBLE = "visible"
ROLE_HIDDEN = "hidden"
ROLE_CASTER = "caster"
ROLE_CATCHER = "catcher"

#: Roles that only do what they say under Cycles. Under EEVEE/Workbench the
#: object flags behind them are ignored and the collection renders *visible*,
#: which pollutes the sheet — worth a warning rather than a silent wrong sheet.
NEEDS_CYCLES = frozenset({ROLE_CASTER, ROLE_CATCHER})


def sibling_role(
    active_owner: str,
    sibling_kind: str,
    sibling_owner: str,
    inactive_render: str = INACTIVE_SHADOW,
) -> str:
    """What one non-active collection does during the active element's render.

    ``active_owner`` is the *active* element's shadow owner, ``sibling_kind``
    and ``sibling_owner`` describe the collection being placed. The active
    element and its own context collection never reach here — the caller
    marks those :data:`ROLE_VISIBLE` first.
    """
    if active_owner == kinds.SHADOW_ELEMENT:
        # The active element carries its own shadow, so the plate underneath
        # catches it into this sheet and nothing else may cast: every other
        # neighbour's shadow already lives in the plate's own art.
        if sibling_kind == kinds.BACKDROP:
            return ROLE_CATCHER
        return ROLE_HIDDEN
    if inactive_render == INACTIVE_HIDDEN:
        return ROLE_HIDDEN
    if sibling_owner == kinds.SHADOW_ELEMENT:
        # This neighbour's shadow travels in its own sheet frame by frame;
        # casting it into the plate as well would draw it twice.
        return ROLE_HIDDEN
    return ROLE_CASTER


def travel_from_samples(
    per_object: Mapping[str, Sequence[tuple[float, float, float]]], ppb: float
) -> Travel:
    """Reduce per-frame position samples to one element's travel, in panel px.

    Each sample is one object's position at one frame, already projected onto
    the render camera's basis as ``(along the camera axis, in-plane u,
    in-plane v)``; ``ppb`` converts Blender units to panel pixels.

    The two decisions worth stating. Travel is the widest any *single* object
    moves, not how far the element's combined bounds move: a control modelled
    as a moving part plus a static bracket in one collection would otherwise
    average down towards standing still. And the in-plane result combines both
    axes, so a diagonal slide counts as its true distance rather than as
    whichever component happens to be larger.
    """
    across = depth = 0.0
    for samples in per_object.values():
        if len(samples) < 2:
            continue
        spans = [max(values) - min(values) for values in zip(*samples)]
        depth = max(depth, spans[0] * ppb)
        across = max(across, math.hypot(spans[1], spans[2]) * ppb)
    return Travel(across=across, depth=depth)
