# RE-Blend

A **Blender add-on for producing the 2D GUI sprite-sheet assets of a Reason Rack Extension (RE)**.

RE-Blend makes it easier to develop rack extensions for Reason (the DAW) in Blender. It allows the
user to render multiple elements, binds control states (knob rotation, indicator lighting, button 
presses, fader detents) to Blender's timeline frames, and automates everything between your Blender 
scene and correct sprite sheets in `GUI2D/`. This includes two-way synchronisation with the RE 
project's Lua configuration so sizes, offsets, and frame counts are matched.

> **Status: in development.** The M0 render spike passed against the real SDK toolchain, and
> the M1 (MVP: import, rigs, batch render, validation) and M2 (two-way sync: patch-mode Lua
> export, re-import merge, panel preview & QA) implementations have landed, pending their pilot
> exit criteria. The tool lives in the **RE-Blend** tab of Blender's N-panel. See
> [`ROADMAP.md`](ROADMAP.md) for milestone status.

## Why this exists

Making RE GUI art by hand is challenging, since the lay-out needs to match between Blender and 
config files in Lua. The SDK wants flat PNG sprite sheets under strict rules: vertical strips 
with frame 0 on top, 8-bit straight alpha, pixel-exact registration across frames, and a frame 
count that exactly matches what `device_2D.lua` declares — plus a per-widget frame contract
(a toggle button is 2 or 4 frames, a radio button always 2, an up/down button always 3) and a
set of parts whose art belongs to Reason rather than to you. Those rules *are* documented; the
trouble is that RE2DRender enforces almost none of them, so breaking one usually surfaces at
submission rather than at build time. 

RE-Blend's answer is to prevent these mismatches:

- **The SDK's own rules, checked.** Panel geometry, per-widget frame counts, edge margins and
  the required device parts are validated against what the SDK documents actually require —
  see [`docs/sdk-gui-reference.md`](docs/sdk-gui-reference.md).
- **Registration is guaranteed by construction.** Each element gets a fixed camera derived from
  a registration empty that never moves between frames. All frames of a control land on the
  same centre because the camera physically can't drift.
- **The timeline is the sprite sheet.** Frame 0 is the knob at minimum, frame N−1 at maximum;
  buttons, faders, selectors, and lamps get a state table compiled to constant-interpolation
  keyframes. Scrubbing the timeline previews the sheet; rendering the sheet is just rendering
  frames 0…N−1 and stitching them.
- **Frame counts can't diverge** because the sheet is generated *from* the element's declared
  frame count, which is synced and validated against `device_2D.lua`.
- **Exports are verified, not trusted.** Straight alpha is checked in the written file (Blender
  composites premultiplied internally, and this is the classic way these sheets go subtly
  wrong), frames are scanned for art bleeding past the declared bounds, and colour management
  is pinned.

## What it does

- **Import an existing project** *(landed, M1)*: parse `device_2D.lua` / `hdgui_2D.lua`, build a
  guide layout in Blender — panel planes, per-control bounding boxes at the declared offsets and
  sizes, rigs pre-configured with the declared frame counts. Model your hardware inside the
  boxes, hit Render All, correct sheets land in `GUI2D/`.
- **Write changes back to Lua** *(landed, M2)*: placement offsets and frame counts,  via anchored
  patch edits that leave everything else in the file intact. If an edit is ambiguous, RE-Blend 
  refuses with an error. Re-running import against changed Lua becomes a merge: per-item 
  accept-theirs / keep-mine, and removed nodes are flagged, never auto-deleted.
- **Preview and QA before the SDK** *(landed, M2)*: a full-panel composite of the rendered sheets 
  at their declared offsets with a per-element state playground, contact sheets and flipbook 
  playback for sweep smoothness, and one-click RE2DRender / RE2DPreview launch.
- **Validate the contract** *(landed, M1)*: missing art, orphan art, frame-count mismatches, 
  wrong PNG dimensions, case mismatches in paths, alpha bleed, etc.
- **Run headless** *(planned, M3)*: `blender -b` drives the same operators with a non-zero exit
  code on validation errors.
- **Start a greenfield device** *(planned, M4)* from a calibrated template scene and export a
  first-pass Lua skeleton plus all the sheets.

## Using it

Install the extension first — see [`docs/install.md`](docs/install.md). Everything lives in the
**RE-Blend** tab of the 3D viewport's N-panel (press `N`). Tool paths for RE2DRender /
RE2DPreview go in **Edit ▸ Preferences ▸ Add-ons ▸ RE-Blend** — they are per-machine settings
and are never written into the project or the `.blend`.

### 1. Link and import the project

In the **RE Project** panel, point *RE Project* at the repo root (the directory containing
`GUI2D/`), check *Pixels / Unit* (default: 100 px per Blender unit), *Rack Units*, and the
*World Origin* you want to model around, then click **Import RE Project**. You get, per sprite
sheet: a collection with the `re_*` properties filled from the Lua, a registration empty at the
frame centre, wireframe guide boxes at every declared placement, and a default rig or state
table for its widget kind. Changed the calibration later? **Re-import & Reposition** snaps
everything onto the new settings.

### 2. Set frame sizes

The RE Lua never stores per-frame pixel size, so fresh imports are unsized until you decide
(existing sheets on disk are measured automatically). Use **Set All Missing Sizes** in the
element list, or the per-element *Frame W/H* fields. Keep both dimensions **multiples of 5** —
RE2DRender silently reframes sheets that aren't (in SDK v4.x.x).

### 3. Model and rig

Model each control inside its guide box. For a knob, select the rotating part and click
**Generate Rig**: scene frame 0 becomes the minimum position, frame N−1 the maximum, spinning
about the registration empty (sweep configurable via `re_sweep_deg`). For buttons, faders,
selectors, and lamps, build the **State Table**: add actions (visibility, emission strength or
colour, a location axis, a shape key), set each state's value, and Generate Rig compiles them to
constant-interpolation keyframes — scrubbing the timeline previews exactly the discrete sheet.

Values don't have to be typed. **Capture From Scene** (eyedropper) stores whatever the object
currently holds, so a fader detent is placed by moving it in the viewport. **Spread Between
Extremes** fills every in-between state by linear interpolation from the first and last: an
8-position selector needs only its two end positions, and a `sequence_fader`'s travel comes out
exactly evenly spaced — which the SDK requires of it, and hand-typed detents don't guarantee.
Validation flags uneven fader travel, state tables that don't match the frame count, and
multi-frame controls with no rig at all.

For anything the built-in actions don't cover, add a **Driver Value**: a named number
(`lovely-cucumber`) the states drive, which your own drivers can read as a Single Property
variable — so a state table can reach a modifier, a geometry-node input or a constraint without
RE-Blend needing an action type for each.

### 4. Render and validate

**Render All** (or **Render Active**) isolates each element, renders frames 0…N−1 through a
camera derived from its registration empty, stitches the vertical strip, and writes
`GUI2D/<path>.png` — then *verifies* the written file: straight alpha, 8-bit RGBA, exact
dimensions, and art bleeding past the frame bounds. **Validate** runs the full cross-check
against the Lua and reports errors and warnings in the panel.

### 5. Preview, play, launch

In **Preview & QA**, pick a panel and click **Preview** to composite the rendered sheets at
their declared offsets without leaving Blender. The **State Playground** sliders choose which 
frame each element shows (selector at step 3, lamp lit…). **Contact Sheet** lays every frame 
of the active element out as a grid; **Flipbook** loads the strip as a playable sequence in 
the Image Editor. **Run RE2DRender / RE2DPreview** close the real loop (render output lands in 
`RE2DRender_Output/` beside `GUI2D/`).

### 6. Write back

Moved a control? Drag its registration empty and click **Export Layout (Patch Lua)**. RE-Blend
rewrites *only* the `offset`/`frames` number literals of nodes it knows, verifies the patched
file by re-parsing it before replacing anything, and refuses when changes are ambiguous. 
If the Lua changed upstream, **Sync With Project** lists what's new, removed, or different, 
with per-item *Theirs / Mine* resolution; **Apply Resolutions** brings accepted changes in 
through the same path a full import uses.

## What it doesn't do

RE-Blend produces the hi-res PNGs and never the 0.5× set — that's
[RE2DRender](https://developer.reasonstudios.com/)'s job. Nor does it author the parts whose
appearance Reason fixes (sockets, the name tape, the back-panel placeholder, CV trim knobs, the
browse groups): it installs those from the SDK you point it at. It reads `motherboard_def.lua` for
validation but never writes it, and it stays out of `realtime_controller.lua`, `display.lua`,
and anything C++. It interoperates with [RE Edit](https://github.com/pongasoft/re-edit) rather
than replacing it: both tools read and write the same two layout files, and files RE-Blend
patches must keep loading in RE Edit (and of course all SDK-tools).

## Planned platform

A standalone **Blender 4.2 LTS+ extension** (pure Python, shipped via `blender_manifest.toml`,
which allows bundling wheels — `lupa` for reading the Lua config; `numpy` already ships with
Blender). No compiled RE-Blend code, no SDK code or assets bundled. SDK tool paths are a
per-machine setting and never end up in your repo.

## License

**GPL-3.0-or-later** — see [`LICENSE`](LICENSE). Note the license covers RE-Blend itself; the 
sprite sheets and Lua files it produces are your project's output and yours to license however
you like. The SDK remains under Reason Studios' own terms, this tool only interoperates.
