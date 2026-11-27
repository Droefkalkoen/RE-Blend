# Skou feedback: triage and status

Skou (the pilot project) logged 39 issues with RE-Blend while its panels were built, in
`docs/RE-Blend_feedback.md` in the Skou repo. Skou worked around all of them by hand on `main`.
The fixes landed on `develop` (see "Branches" in `CLAUDE.md`), alongside RE Bench
([`rebench-design.md`](rebench-design.md)). Skou has shipped, so `develop` now merges into
`main`.

Each item has one disposition. Numbers are the Skou feedback numbers. **Status as of
2026-11-27: every item is done or by design.** Blender-side fixes have tests under
`tests/blender/` (run with the `bpy` module); the rest are pure-Python tests.

## A. Bugs

| # | Bug | Status |
| --- | --- | --- |
| 1 | Panel property getters raise `NameError` | resolved: the getters call module-level helpers |
| 2 | Preview Panel fails on a second run once the preview image is saved | fixed: a saved or file-backed preview image is recreated |
| 3 | Apply Choices reverts designer-set frame sizes | fixed: frame size is no longer a merge item; a stale sheet is a `png-dims` finding |
| 4 | Merge-item `resolution` enum is empty from Python | fixed: a string property with an enum proxy for the UI; Apply refuses an unknown choice |
| 5 | Placement 0 ignores `re_placements` | by design: placement 0 follows the registration empty |
| 6 | Drivers invalid after `material.copy()` | fixed: Repair Channels re-validates drivers |
| 17 | Knob turntable runs backwards for a `neg_y` camera | fixed: `calibration.turntable_expression`, tested for all six axes |
| 22, 34 | Copied elements share state actions | fixed: Generate Rig gives each element's objects their own action |
| 23 | A failing step after a render goes unnoticed | fixed: the render state (running, done, failed, cancelled) shows in the Render panel and the headless report |
| 35 | Hidden objects report stale bounds | fixed: travel sampling skips hidden objects; *Centre Selected on Element* measures hidden objects shown (`world_bounds_unhidden`) |
| 37 | False "shadow-owner: slides N px" after moving | fixed: the message says to run Generate Rig; the check measures live |

## B. Rules (one catalogue, one engine)

The bench's layout rules moved into `reblend/project/layout.py`, so `rebench check` and
RE-Blend's Validate run the same rules (`validate_link(..., rules=True)`).

| # | Rule | Status |
| --- | --- | --- |
| 8 | Vertical fader frame 0 shows the handle at the bottom | done: `fader-direction` (Validate) |
| 9 | SDK parts' frame counts known | done: `sdk-part-frames` |
| 10 | No border-alpha warning for backdrops and full-bleed windows | done: backdrop and display kinds, plus a per-element Full Bleed flag |
| 24 | Rack-ear keep-out | done: `rack-ear` (profile) |
| 26 | Text size at least 43 px | done: `text-size` (sidecar labels) |
| 29 | Fold-arrow keep-out; effect bypass top-left | done: `fold-arrow`, `bypass-top-left` |
| 30 | Rack-ear screws on Reason's rack holes | done: `rack-screws`, from screw nodes and sidecar `art` marks |
| 31 | Routing icons match the routing hint | done: `routing-icons`, from `Routing_Icon_*` nodes and sidecar `art` marks |
| 36 | `radio_button` is its own kind | done: `button_radio` (2 frames: released, held) |
| 7 | Findings show their age; re-validate on load and after Generate Rig | done |

## C. RE Bench

| # | Item | Status |
| --- | --- | --- |
| 15 | New nodes need hand edits to two Lua files | done: `add_control`, and `remove_control` to undo it |
| 11 | Fader template seeds wrong state names and a 0 x 0 frame | done: sidecar frame sizes; On/Off/Bypass faders get off/on/bypass, others none |
| 38 | Derived sheets (cropped folded fader) | done: `derive` / `underive`; RE-Blend writes the crop when it renders the source |
| 25 | SDK parts copied at the wrong resolution | done: canonical names and hi-res sizes; `sdk-part-size`; Install SDK Parts prefers a right-size copy |
| 28 | SDK jack art is not centred in its frame | done: `centre` op with `visible: true` |
| 33 | Overlap only shows up after placement changes | done: live overlap on the bench canvas, and a live warning in Blender while an empty is dragged |
| 27 | No half-resolution preview | done: bench 0.5x view and a 0.5x toggle on Blender's Preview |

## D. Blender rendering and modelling

| # | Feature | Status |
| --- | --- | --- |
| 14, 39 | Background render with progress and cancel; per-element samples | done |
| 12 | Shadow casters that keep bounce light | done: Render option |
| 13 | Panel-aware isolation | done: Render option |
| 19 | Alpha floor against shadow-catcher haze | done: Render option |
| 32 | Reload edited images before rendering | done |
| 18 | Knob zero-angle calibration | done: Pointer at Rest (`re_zero_deg`) |
| 16, 20 | Per-element art variants | done: Add Art Variant / Use Variant (child collections) |
| 21 | Wear / touch points with per-point seeds | done: `RE Touch Point` node group |
