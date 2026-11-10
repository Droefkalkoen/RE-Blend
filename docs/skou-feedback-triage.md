# Skou feedback: triage for the `develop` branch

Skou (the pilot project) logged 39 issues with RE-Blend while its panels were built, in
`docs/RE-Blend_feedback.md` in the Skou repo. Skou worked around all of them by hand and keeps
using RE-Blend as it is on `main` until Skou ships, so none of these land on `main` directly.
They land on `develop` (see "Branches" in `CLAUDE.md`), alongside RE Bench
([`rebench-design.md`](rebench-design.md)), and `develop` merges into `main` once Skou is done.

Each item gets one disposition. Numbers are the Skou feedback numbers.

## A. Bugs: fix first on `develop`

Small, self-contained, and each one already cost Skou a manual workaround. Verify each against
`develop` before fixing: some may already be fixed by later commits (the `props.py` getters in
#1 look resolved on the current code).

| # | Bug | Where |
| --- | --- | --- |
| 1 | Panel property getters raise `NameError` (`_active_frame_size` and friends) | `ui/props.py` |
| 2 | Preview Panel fails on a second run once the preview image is saved | `ui/operators.py` `_show_image` |
| 3 | Apply Choices reverts designer-set frame sizes to the stale PNG size | `project/merge.py`, frame-size probe |
| 4 | Merge-item `resolution` enum is empty from Python | `ui/props.py` dynamic enum |
| 5 | Placement 0 of a multi-placement element ignores `re_placements` | `model/schema.py`, placement code |
| 17 | Knob turntable runs backwards for a `neg_y` camera | `model/rigs.py` driver expression |
| 22, 34 | Copied elements share state actions; Generate Rig rewrites the original's keys | `model/rigs.py`: own action per element, or refuse |
| 37 | False "shadow-owner: slides N px" after moving an element | `project/validation.py` `_check_shadow_owner` |
| 35 | Hidden objects report stale bounds to placement helpers | helpers that place state-dependent objects |
| 6 | Drivers invalid after `material.copy()`; Repair Channels could re-validate | `ui/operators.py` repair |
| 23 | A failing step after a render goes unnoticed: add a render-complete flag | `render/renderer.py` |

## B. Rules: move into the shared rule catalogue (RE Bench phase 1)

These become entries in the catalogue that both the Blender validator and RE Bench run.

| # | Rule |
| --- | --- |
| 8 | Vertical `sequence_fader` frame 0 shows the handle at the bottom (`inverted` changes the mapping only) |
| 9 | SDK parts' frame counts known (audio and CV jacks are 3-frame strips) |
| 10 | No "alpha touches the border" warning for backdrops and full-bleed display windows |
| 24 | Rack-ear keep-out: nothing but ear art at x < 160 or x > 3610 (house rule, configurable) |
| 26 | Text size: functional labels at least 43 px (SDK guideline; floor configurable) |
| 29 | Fold-arrow keep-out on all panels; effects need on/off/bypass top-left on front and folded front |
| 30 | Rack-ear screws aligned with Reason's rack holes |
| 31 | Back panel carries every `Routing_Icon_*` the routing hint implies |
| 36 | `radio_button` is its own kind (2 frames, grouped by property and index), not `button_momentary` |
| 7 | Findings show their age; re-validate on load and after Generate Rig |

## C. Covered by the RE Bench handoff (phase 3)

| # | Item | How the bench covers it |
| --- | --- | --- |
| 15 | New nodes need hand edits to two Lua files | Bench writes property, widget and node stubs in one step |
| 11 | Fader template seeds wrong state names and a 0 x 0 frame | Frame size and states come from `rebench.json` |
| 38 | Derived sheets (cropped folded fader) are not understood | Per-placement crop / frame override in the sidecar |
| 25 | SDK parts copied at the wrong resolution | Bench SDK parts library resolves the canonical `RE2DRender/Images` files and checks size |
| 28 | SDK jack art is not centred in its frame | Bench places SDK parts by visible (alpha) centre |
| 33 | Overlap only shows up after placement changes | Live overlap check on the bench canvas; Blender keeps the validator check |
| 27 | No half-resolution preview | Bench 0.5x view; a Blender toggle stays on the stretch list |

## D. Blender rendering features: later on `develop`, not blocking the bench

| # | Feature |
| --- | --- |
| 14, 39 | Render in a background `blender -b` process with progress and cancel (Skou already does this by hand) |
| 12 | "Shadows + bounce" isolation mode as a setting (Skou patches `_SHADOW_ONLY_OFF`) |
| 13 | Panel-aware isolation (per-panel origin or active-panel-only) |
| 19 | Alpha floor / feather-to-edge render option (Skou remaps alpha with a 7 % floor) |
| 32 | Reload file-backed images whose mtime changed before rendering |
| 18 | Knob zero-angle calibration ("pointer at rest" angle) |
| 16, 20 | Per-element art variants (3D silk versus Photoshop overlay) |
| 21 | Wear / touch-point helper with per-point seeds |

## Order on `develop`

1. Section A bugs, each with a test where the code is pure Python.
2. RE Bench phase 1 with the section B rules (shared catalogue + `rebench check`).
3. RE Bench phases 2 and 3; section C falls out of them.
4. Section D as Skou or the next device needs it.
