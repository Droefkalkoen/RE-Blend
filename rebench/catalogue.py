"""The rule catalogue: one entry per finding code, with where the rule comes from.

``source`` keeps the kinds of evidence apart (CLAUDE.md, working conventions):

- ``req``: a requirement in the SDK 4.6.0 GUI design guidelines;
- ``spec``: the Jukebox scripting specification;
- ``guide``: an SDK guideline (encouraged, not required);
- ``obs``: observed RE2DRender / Recon behaviour, not in the SDK documents;
- ``house``: a brand rule from the active profile (Terp Audio's came from Skou);
- ``tool``: RE-Blend's own consistency checks (art, rig and Lua agree).

``scene_only`` codes need a Blender scene (rigs, state tables, measured travel)
and are skipped by the bench, which works from the project files alone.

Codes are the ones :mod:`reblend.project.validation` emits plus the bench's own
layout rules (:mod:`rebench.layout`). One list for both, so the Blender
validator and the bench cannot quietly disagree about what a code means.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Rule", "RULES", "rule_for"]


@dataclass(frozen=True)
class Rule:
    code: str
    title: str
    source: str
    ref: str = ""
    scene_only: bool = False


_G = "SDK 4.6.0 GUI design guidelines"
_S = "Jukebox scripting specification"

RULES: dict[str, Rule] = {r.code: r for r in (
    # --- panels -----------------------------------------------------------
    Rule("panel-missing", "Every panel exists", "req", f"{_G}: panel dimensions"),
    Rule("panel-size", "Panel 3770 px wide; folded panels 150 px tall", "req", f"{_G}: panel dimensions"),
    Rule("rack-height", "Height is whole rack units, at most 9U", "req", f"{_G}: panel dimensions"),
    Rule("player-folded", "Players have no folded panels", "spec", _S),
    # --- reserved areas ----------------------------------------------------
    Rule("edge-margin", "25 px left/right margin clear of interaction widgets", "req", f"{_G}: required device parts"),
    Rule("fold-arrow", "Nothing overlaps Reason's fold/unfold arrow", "req",
         f"{_G}: required device parts (position observed: docs/findings-fold-arrow.md)"),
    Rule("rack-ear", "Only ear art (tape, screws) on the rack ears", "house", "Skou feedback #24"),
    Rule("placeholder", "Back panel declares the 300x100 placeholder", "req", f"{_G}: required device parts"),
    Rule("cable-origin", "cable_origin on the folded back panel only", "spec", _S),
    # --- required parts ----------------------------------------------------
    Rule("bypass-top-left", "Effects: on/off/bypass top-left on front and folded front", "req",
         f"{_G}: required device parts (corner size is a house reading)"),
    Rule("device-name", "Device name tape on all four panels", "req", f"{_G}: required device parts"),
    Rule("socket-panel", "Audio and CV sockets on the back panel only", "spec", f"{_S}: sockets"),
    Rule("display-panel", "Custom displays on the front and folded front only", "spec", f"{_S}: custom_display"),
    Rule("sdk-part-frames", "SDK parts keep their stock frame counts", "obs", "SDK example devices; Skou feedback #9"),
    # --- widgets and sprites -----------------------------------------------
    Rule("bounds", "Widget fully inside its panel", "req", f"{_G}: required device parts"),
    Rule("overlap", "Widgets do not overlap", "req", f"{_G}: required device parts"),
    Rule("widget-frames", "Frame counts follow the widget's contract", "spec", f"{_S}: widget animation frames"),
    Rule("steps", "sequence_fader frames match the property's steps", "spec", f"{_S}: sequence_fader"),
    Rule("frame-count", "Sheet frame count matches device_2D.lua", "req", f"{_G}: image formats"),
    Rule("frame-bounds", "Frame size is renderable: positive, multiples of 5", "obs",
         "RE-Blend docs/findings-m0.md, finding 6"),
    Rule("reframed", "Frame width and height are multiples of 5", "obs", "RE-Blend docs/findings-m0.md, finding 6"),
    Rule("widget-node", "Every widget names a node that exists", "spec", _S),
    # --- files -------------------------------------------------------------
    Rule("png-missing", "Every sprite path has a PNG", "tool"),
    Rule("png-dims", "Sheet size is frames x frame size", "req", f"{_G}: image formats"),
    Rule("png-format", "8-bit RGBA PNG", "req", f"{_G}: image formats"),
    Rule("case", "Sprite file names match case exactly", "tool"),
    Rule("duplicate-path", "One element per sprite path", "tool"),
    Rule("missing-art", "Every placed sprite has an element", "tool"),
    Rule("orphan-element", "Every element is placed in device_2D.lua", "tool"),
    Rule("kind", "Element kind matches its widget", "tool"),
    Rule("frame-size", "Frame size is known", "tool"),
    # --- Blender scene only (skipped by the bench) -------------------------
    Rule("states", "Multi-frame elements have a state table", "tool", scene_only=True),
    Rule("rotor", "Knobs have a rotor", "tool", scene_only=True),
    Rule("shadow-owner", "Baked shadows do not move", "tool", scene_only=True),
    Rule("travel", "Geometry travel measured", "tool", scene_only=True),
    Rule("moved", "Scene moves exported to Lua", "tool", scene_only=True),
    Rule("view-transform", "Standard view transform", "tool", scene_only=True),
)}


def rule_for(code: str) -> Rule:
    """The rule behind a finding code; unknown codes are reported as tool checks."""
    return RULES.get(code) or Rule(code, code, "tool")
