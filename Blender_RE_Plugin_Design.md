# RE-Blend — Blender Add-on for Rack Extension GUI Asset Production

**Design document · v0.2 draft**
Name: **RE-Blend** — *RE* for Rack Extension, *Blend* for Blender and its `.blend` files. Python
package/import name `reblend`; distribution/repo name `re-blend`.

> **Status & home:** this document describes a **general-purpose, standalone Blender
> add-on** that will live in its own repository. No code exists yet — this is the design
> to build from. It is written to be self-contained so it can be dropped into the new
> repo as-is.

---

## 1. Problem statement

Producing the 2D GUI art for a Reason Rack Extension is a pipeline problem, not a drawing
problem. The SDK consumes flat PNG sprite sheets with hard, silent-failure rules. Those
rules *are* documented — SDK 4.6.0 ships a GUI designer manual, GUI design guidelines and
a scripting specification covering all 25 widget types (see §12 and
`docs/sdk-gui-reference.md`) — but the tooling does not enforce most of them. RE2DRender
compiles art that submission review will later reject, so the rules have to be checked
somewhere, and RE-Blend is that somewhere:

- Sprite sheets are **vertical strips**, frame 0 on top, strip height =
  `frameHeight × frameCount`.
- 8-bit PNG with **straight (un-premultiplied) alpha**.
- Pixel-exact registration: every frame of a control must place its centre at the same
  X,Y or the control visibly wobbles/jitters in Reason.
- The frame count baked into the art **must equal** the `frames` field in
  `GUI2D/device_2D.lua`, or knobs render jumpy. Beyond that, each widget type has its own
  frame contract: a `toggle_button` takes 2 or 4 frames, a `momentary_button`,
  `step_button` or `radio_button` exactly 2, an `up_down_button` exactly 3, and a
  `sequence_fader` one frame per handle position (which for a stepped property means one
  per `steps`). Most of these are *independent* of the bound property — see
  `docs/sdk-gui-reference.md` §3.
- Everything is authored at hi-res (panel world = 3770 px wide; 1U = 345 px tall, up to
  9U; folded panels = 150 px) and RE2DRender generates the 0.5× set — the artist must
  never hand-make it.
- Some widgets' art is not the artist's to make at all: sockets, the name tape, the
  back-panel placeholder, CV trim knobs and the browse groups must use the SDK's stock
  images, and text/bounds widgets are never drawn from the authored pixels.

Doing this by hand in Blender means: one camera setup per control, manually keyframed
turntables whose frame counts drift from the Lua contract, hand-cropped renders, manual
strip stacking in an image editor, and no check that any of it matches what the `.lua`
files declare. Every mismatch fails silently and is only discovered after an RE2DRender
run — or worse, in Reason as a wobbling knob.

**RE-Blend's job:** make the Blender scene the single source of truth for the *rendered
look* of every element, bind control states (knob rotation, indicator lighting, button
presses, fader detents) to Blender's timeline frames, and automate everything between
"scene" and "correct sprite sheets in `GUI2D/`" — including two-way synchronisation with
the RE project's Lua configuration so sizes, offsets, and frame counts can never drift.

## 2. Position in the RE tooling ecosystem

RE-Blend fills a gap; it deliberately does **not** replace existing tools:

| Tool | Role | RE-Blend's relationship |
| --- | --- | --- |
| **RE2DRender** (SDK) | Compiles `GUI2D/` (lua + PNGs) into the build format; generates the 0.5× set | RE-Blend **produces its input** (the PNGs) and never generates low-res assets |
| **RE2DPreview** (SDK) | Renders panels to images for a quick look | RE-Blend can shell out to it after export (optional convenience) |
| **RE Edit** (pongasoft, open source) | WYSIWYG editor for `hdgui_2D.lua` / `device_2D.lua` — placement and widget editing with existing PNGs | Overlapping by design where it helps: RE Edit edits the *layout files* against finished PNGs, RE-Blend produces the *art* and edits the same layout data from the Blender side. Feature overlap is acceptable whenever doing it inside Blender is genuinely better (see §6.5); the hard requirement is interoperability — both tools read/write the same two files (see §6.4) |
| **Recon / Reason** | Validation host / DAW | Downstream, untouched |
| **Photoshop / Krita / etc.** | Compositing, engraved labels, state glows | Reduced but not eliminated (see §5.6): RE-Blend supports both a "Blender-only" path and a "Blender → 2D tool" path |

The three-file contract of an RE (a `motherboard_def.lua` property → bound by an
`hdgui_2D.lua` widget → naming a node placed in `device_2D.lua` → naming a PNG) stays the
RE project's own responsibility; RE-Blend reads it, validates against it, and can write
the placement layer of it.

## 3. Users and core scenarios

1. **Greenfield device**: designer starts from RE-Blend's template scene (calibrated
   camera, lighting rig, panel plane at the right size), lays out parametric controls
   from the built-in library, and *exports* a first-pass `device_2D.lua` +
   `hdgui_2D.lua` skeleton plus all sprite sheets.
2. **Existing project**: a repo already has `device_2D.lua` / `hdgui_2D.lua` fully wired
   with placeholder offsets and final frame counts. Designer *imports* the project:
   RE-Blend builds a guide layout in Blender (panel plane, per-control bounding boxes at
   the declared offsets/sizes, rigs pre-configured with the declared frame counts).
   Designer models/materials the hardware inside those boxes and hits "Render All Sheets" —
   correct sheets land in `GUI2D/`.
3. **Iteration**: designer nudges a knob's look or moves a control; RE-Blend re-renders
   only the dirty elements and (if the control moved) updates its `offset` in
   `device_2D.lua`. Designer re-runs RE2DRender/Preview to see it in context.
4. **CI / build machine**: the `.blend` is rendered headlessly (`blender -b`) so the
   asset build is reproducible and art regressions are catchable in a pipeline.

## 4. Core concepts and data model

### 4.1 Project link

A scene is linked to exactly one RE project by pointing RE-Blend at the repo root. From
there RE-Blend locates and parses:

- `GUI2D/device_2D.lua` — nodes, offsets, sprite paths, frame counts, per panel
  (`front`, `folded_front`, `back`, `folded_back`), plus `CableOrigin`.
- `GUI2D/hdgui_2D.lua` — widget type per node (knob / toggle / momentary /
  sequence_fader / sockets / custom_display with `display_width_pixels` ×
  `display_height_pixels`), and which property each binds.
- `motherboard_def.lua` (read-only, best effort) — property kinds: `steps = N` on a
  stepped selector, booleans, etc. Used only for validation (a `sequence_fader` bound to
  an 8-step property whose sheet declares `frames = 3` is an error worth flagging).

Parsed data is cached on the scene; a **Check for Differences** operator re-reads and
reports drift (see §6).

### 4.2 The RE Element (the central object)

Every exported sprite sheet corresponds to one **RE Element**: a Blender **collection**
carrying RE-Blend custom properties:

| Property | Meaning |
| --- | --- |
| `re_node` | Node name in `device_2D.lua` (e.g. `knob_cutoff`) |
| `re_path` | Sprite PNG basename (e.g. `knob_cutoff` → `GUI2D/knob_cutoff.png`) |
| `re_kind` | `knob` / `button_toggle` / `button_momentary` / `fader_handle` / `selector` / `lamp` / `backdrop` / `static` / `socket` |
| `re_frames` | Frame count (61, 8, 3, 2, 1 …) |
| `re_frame_w`, `re_frame_h` | Per-frame pixel dimensions |
| `re_panel` | Which panel(s) it appears on |
| `re_offset_x`, `re_offset_y` | Placement in panel pixels (top-left origin, +y down) |
| `re_registration` | Reference to the element's **registration empty** (see below) |
| `re_shadow_owner` | Where the element's cast shadow is rendered: `background` (baked into the panel plate) or `element` (into its own sheet, travelling with the art) — see §5.1 |
| `re_rotor` | The name of the knob's rotating object. Recorded the first time a rotor is identified (the Rig panel's picker, a selection at Generate Rig, or an existing driver found by Generate All Rigs), so knob rigs are reproducible without a selection — in bulk and headlessly |

The **registration empty** is a 3D empty marking the element's registration point (for
knobs: the rotation axis). The per-element render camera is derived from it, which makes
pixel-exact registration across frames true **by construction** — the camera simply never
moves between frames of one element. This kills the wobble/jitter failure class outright.

Elements live under one root collection per panel (`RE Front`, `RE Back`, `RE Folded
Front`, `RE Folded Back`); an element used on several panels (typically the
On/Off/Bypass fader, which the acceptance checklist requires on the folded front too) is
one collection referenced from both.

### 4.3 Frame binding: the timeline **is** the sprite sheet

The designer binds control state to scene frames — RE-Blend's central idea, matching how
the designer already thinks ("frame 0 = knob at minimum"):

- **Knobs** (`re_kind = knob`): RE-Blend auto-creates a rotation driver on the knob's
  rotating part (recorded as `re_rotor`, so the rig can be rebuilt without a selection):
  scene frame 0 → min angle, frame `re_frames − 1` → max angle, linear, around the
  registration empty's axis. Default sweep −150°…+150° (300°), configurable per element
  in the Rig panel. Changing `re_frames` re-generates the driver — frame count can never
  silently diverge from the rig.
- **Multi-state controls** (buttons, fader handles, selectors, lamps): a **state table**
  on the element maps each sprite frame to a named state, and each state to a set of
  *state actions*: object visibility toggles, material emission strength/colour values,
  object transforms (a fader handle's detent position), shape keys. Example for a
  3-state On/Off/Bypass fader (3 frames, following the SDK examples' use of the built-in
  `builtin_onoffbypass` property with a `jbox.sequence_fader`): frame 0 = *Off* (handle
  down, lamp dark), frame 1 = *On* (handle mid, lamp lit), frame 2 = *Bypass* (handle
  up, alternate lamp colour). RE-Blend compiles the state table into keyframes with
  **constant** interpolation so scrubbing the timeline previews exactly the discrete
  sheet. Numeric channels (transforms, emission strength, emission colour, shape keys)
  additionally support a **linear spread**: the designer sets only the two extreme
  states and RE-Blend fills the in-between ones by interpolation. An 8-position selector
  therefore needs two positions typed rather than eight, and — the reason it is not
  merely a convenience — a `sequence_fader`'s travel comes out *exactly* evenly spaced,
  which the scripting specification requires of it ("the amount the handle travels
  between each animation frame must be constant") and which hand-entered detents do not
  guarantee. Values can also be **captured from the scene**, so a detent is placed by
  posing the object in the viewport rather than by typing coordinates.
- **Driver values**: the action vocabulary above covers what most controls need, but
  Blender animates far more than RE-Blend has action types for — a modifier factor, a
  geometry-node input, a constraint influence. Rather than grow an action type per
  target, a state table can drive one **named custom property** (an `adjective-noun`
  name, on the element's registration empty by convention) that the designer then points
  any number of their own drivers at. One channel, arbitrary reach, and no new dialog per
  property type.
- Applying a table also **removes keys outside `0…frames−1`** on the channels it owns.
  Shrinking a frame count otherwise leaves orphan keys that the render never visits but
  the viewport does, so the preview quietly stops matching the sheet.
- **Lamps / LEDs**: a two-state specialisation (unlit/lit) driving emission — the
  "lighting of indicators bound to frames" case. The lit state's emission colour can be
  picked from the project palette (§5.7).
- **Static elements** (backdrops, meter windows, plates): 1 frame; no rig.

Because the mapping is *frame-indexed*, previewing a control is just scrubbing the
timeline, and rendering a sheet is just rendering frames `0…N−1`.

### 4.4 World calibration

One scene-level convention makes everything else automatic: a fixed **world-to-pixel
scale** (default 1 Blender unit = 100 panel px; configurable once per scene). RE-Blend
provides:

- A **Calibrate** operator that creates/repairs the panel reference: an orthographic
  camera framing exactly the panel rect (3770 × 345·U for the front/back at U rack
  units, 3770 × 150 folded), film set transparent, resolution locked to the panel size.
- Per-element cameras derived from the same scale: ortho scale ⇔ `re_frame_w`,
  resolution = `re_frame_w × re_frame_h`, centred on the registration empty.
- Panel-unit helpers: snap increments in panel px, a HUD readout of the selected
  element's position in panel coordinates, and optional guide bands for whatever layout
  zones the project's own design defines.
- A **world-origin** selector — top-left of the device (the native RE panel-pixel
  convention), top-centre, or centre — choosing which panel pixel the Blender world
  origin lands on. This is a modelling convenience only: `re_offset_*` and the Lua stay
  top-left panel pixels, and switching origins just shifts where guides and registration
  empties sit in Blender. **Re-import & Reposition** re-reads the project read-only and
  snaps every already-placed element onto the current scale and origin. Positioning is
  done in world space (via `matrix_world`), so an element parented under an organising
  master empty still lands correctly. A **Move Geometry** toggle chooses whether the
  element's modelled geometry travels with the reposition (default — the registration
  empty is the anchor and geometry shifts by the same world delta to stay registered) or
  only the empties and guide boxes move. Either way a calibration change (or an upstream
  layout edit) can be re-applied in one click instead of re-importing into an empty scene.
- **Camera Axis** and **Knob Rotation Axis** selectors. The render camera looks along a
  chosen signed world axis (default −Y, the front orthographic view) applied through the
  registration empty, so a device modelled facing another way — or a single tilted
  element — still renders square. The knob rotation axis defaults to *follow the camera
  axis* (so the rotor faces the camera and spins in view) but can be pinned to an explicit
  world axis when the rotor geometry demands it.
- **Scale to Bounds**: snap the active object to exactly `re_frame_w × re_frame_h` world
  units across the camera's screen plane — stretch to fill, or uniform to fit inside.
  Mostly for backdrops (rough plane → exact panel rect) and any static art sized to a
  known frame.

## 5. Rendering engine

### 5.1 Per-element batch render

The **Render Elements** operator, for each selected (or all, or dirty-only) element:

1. Isolate: only that element's collection (plus a per-element optional "context"
   collection, excluded from the render, usable for light-catching geometry) is visible.
   The other RE Element collections are removed from the visible image, but *how* is a
   user setting: by default they stay in the render as **shadow-only casters** (Cycles ray
   visibility — invisible to the camera, still shadowing the active element, catching
   nothing), so neighbouring geometry keeps grounding the active control; alternatively
   they are **hidden** outright (engine-agnostic) so the element renders wholly alone.
   This setting governs shadows falling *on* the element being rendered; where the
   element's own cast shadow goes is a separate, per-element question — see below.
2. Configure: element camera, `re_frame_w × re_frame_h` resolution, transparent film,
   straight-alpha PNG output, 8-bit, sRGB.
3. Render frames `0 … re_frames − 1`.
4. **Stitch** the frames into one vertical strip (frame 0 on top) in-process
   (§8 covers how) and write `GUI2D/<re_path>.png` in the linked project.
5. Record a render manifest entry (element, frames, size, content hash, scene hash).

Backdrops render as a full-panel pass over the same isolation rules as any other
element — interactive controls are invisible to the camera, but by default still cast
onto the plate (or are shown in an engraved/recessed representation that is part of the
panel itself).

#### Shadow ownership (per element)

Every element declares who owns its *cast* shadow (`re_shadow_owner`):

- **Background** (default) — the shadow bakes into the panel plate underneath. Correct
  for anything whose silhouette holds still across its own frames: a knob spins in place,
  a button's cap depresses within its own outline, so the contact shadow is identical
  frame to frame. Cheaper too — one shadow in the backdrop instead of the same shadow
  repeated in every frame of the sheet.
- **Element** — the shadow renders into the element's own sheet and travels with the art
  frame by frame. Required when the silhouette moves relative to the panel between
  frames. A `sequence_fader` is the clear case: the spec makes it bake the handle's
  entire travel, one frame per position, so a shadow left in the plate would sit frozen
  at one handle position while the handle slides away from it.

This is a correctness property, not a preference, so it cannot be a scene-wide switch —
and it is a *two-sided* one, read from opposite ends in two different render passes:

- Rendering the backdrop: a neighbour is kept as a shadow-only caster only if it is a
  **background** owner. An element owner never casts into someone else's sheet.
- Rendering an **element** owner: the backdrop beneath it becomes a Cycles **shadow
  catcher**, so its shadow arrives as straight-alpha darkness in its own frames, and
  every other caster is taken out for that pass. One rule keeps the result unambiguous:
  an element-owned sheet contains exactly one shadow, its own — a neighbour's is already
  in the plate, and catching it here too would darken the overlap twice.

The default is derived from the element kind on import (fader → element, everything else
→ background) and is user-owned thereafter: re-import never moves a shadow the designer
has placed. Two costs are the designer's to weigh, and are why *element* is not the
blanket default: the shadow must fit inside `re_frame_w × re_frame_h` (the overflow
check in §5.2 enforces it), which for a widget whose graphics rect is also its hit rect
means a slightly larger touch area; and shadow catching needs Cycles. Both are reported
as findings rather than assumed.

Because the default is a guess and the choice is a correctness one, validation measures
the element's geometry frame by frame and checks the two against each other (§6.3).
Movement is decomposed in the render camera's basis, since the two axes fail differently:
sliding **across** the camera plane strands a baked shadow at frame 0's position and is
an **error**; moving **along** the camera axis (a button cap pressing in) only shifts and
softens the real shadow, so it is a **warning** — whether that shift reads depends on the
scene's lights, which is the designer's call. Travel is measured per object and maxed, so
a static bracket sharing the collection cannot average a moving part's travel down, and
sub-pixel movement is ignored because the sheet is authored in whole pixels.

### 5.2 Correctness guarantees (the point of the tool)

- **Registration:** camera fixed per element ⇒ all frames register identically.
- **Frame count:** the sheet is generated *from* `re_frames`, which is synced/validated
  against `device_2D.lua` ⇒ the art/Lua/Blender triple can't disagree.
- **Strip geometry:** stitching is computed, not manual ⇒ height is always
  `frameH × frameCount`, order always top-down.
- **Alpha:** exporter enforces straight alpha and verifies the written PNG (Blender's
  internal compositing is premultiplied; the export path must guarantee unassociated
  alpha in the file and RE-Blend validates the result rather than trusting settings).
- **Bit depth / colour:** 8-bit PNG enforced; scene colour management pinned to
  **Standard** view transform (not Filmic/AgX) so palette hex values survive to the file
  — with a scene check that warns when the view transform has been changed.
- **Overflow detection:** after rendering, RE-Blend scans each frame's alpha for non-zero
  pixels touching the frame border — geometry, shadow, or glow bleeding outside the
  declared bounding box (which would clip in the sheet or misregister in Reason) is
  reported per frame.
- **Never produce the 0.5× set** — that is RE2DRender's job.

### 5.3 Layout / composite preview

A **Preview Panel** operator composites the rendered strips (frame of choice per
element, e.g. "all defaults" or "everything lit") at their `re_offset` positions over
the backdrop, producing a full-panel PNG *inside Blender* — the "does the layout read?"
check without leaving the tool. A **state playground** variant lets the designer pick
each element's frame interactively (e.g. selector at step 3 + a check-mode button lit)
to eyeball state combinations. This mirrors what RE2DPreview does, but pre-export and
per-state.

If SDK tool paths are configured (never stored in the repo — a per-machine setting, in
line with the common practice of git-ignoring local SDK paths), one-click **Run
RE2DRender / RE2DPreview** buttons close the real loop.

### 5.4 Flipbook / smoothness check

For knobs, a **Flipbook** operator renders the strip and plays it back in the image
editor (or exports an animated preview) so 61-frame smoothness, indicator-line
legibility, and lighting consistency across the sweep are checked before the file ever
reaches the SDK. Also renders a **contact sheet** (grid of all frames) for at-a-glance
QA of multi-state controls.

### 5.5 Incremental / dirty-only rendering

Each element stores a hash over its inputs (its collection's datablocks, rig, camera
params, world/light rig, palette). **Render Dirty** re-renders only elements whose hash
changed — a 61-frame knob strip is cheap, but a full device (easily 200+ frames across a
dozen sheets, plus 3770-px-wide panel backdrops, times four panels) is not, and fast
iteration is the whole game.

### 5.6 Emission separation (the 2D-compositing question)

Many RE art pipelines keep state glows out of the 3D render — accent lighting is added
per state in a 2D tool so it can be tuned without re-rendering. RE-Blend supports
**both** pipelines and lets the project choose per element:

- **Blender-complete** (default for new projects): state glows are Blender emission
  materials driven by the state table (§4.3); the sheet leaving Blender is final. This
  makes the 2D compositing step optional for controls.
- **Post-composite**: RE-Blend renders *two* aligned strips per element — base (emission
  disabled) and an **emission-only pass** (everything else black/transparent, via a
  second view layer) — so a compositing app can add/tune glows non-destructively. The
  emission strip is a working file (written to the project's design-sources area, not
  `GUI2D/`).

Engraved label text remains out of scope for v1 (panel typography is better set in a 2D
tool), but backdrops can be round-tripped: RE-Blend renders the raw panel, the 2D tool
adds engraving, and the flattened result returns to `GUI2D/` untouched by RE-Blend —
validation only checks its dimensions.

### 5.7 Palette and material kit

- **Palette loader**: reads a small palette file from the project (proposed convention:
  `Design/palette.json` — a named list of sRGB hex values) and exposes the swatches as a
  Blender node group / color attributes, so an accent colour is picked, not retyped.
- **Material starters**: brushed/anodised metal (with grain-direction control), dark
  glass (meter windows), plastic cap, machined-aluminium knurl — each a node group with
  few, named parameters, tuned to read correctly at the SDK's downscaled 0.5× size.
- **Lighting rig template**: an appendable collection implementing a studio-hardware
  panel look (soft area key at ~30–45° elevation, low cool fill) so lighting is
  consistent across sessions, files, and devices of one brand. Projects can save their
  own rig as the template.

## 6. SDK configuration integration (two-way sync)

This is the second pillar: the Lua contract and the Blender scene must not be able to
drift apart silently.

### 6.1 Import (Lua → Blender)

**Import Project** parses `device_2D.lua` + `hdgui_2D.lua` and materialises:

- The panel planes at correct sizes with zone guides.
- One RE Element per node: an empty + wireframe **bounding box** at the node's
  `offset` with the declared per-frame size, named after the node, rig pre-created from
  the widget type (knob rig for `analog_knob`, N-state table for `sequence_fader` with
  N = frames, 2-state for toggles…), `re_*` properties filled in.
- `custom_display` nodes become **cut-out guides** (the `display_width_pixels ×
  display_height_pixels` rect drawn on the panel) so backdrop art leaves the right hole —
  their content is drawn live by the device's display code and is explicitly not
  rendered art.
- Optional reference underlay: load a mockup image (a layout sketch, screenshot, or
  HTML-mockup export) as a camera background mapped 1:1 to panel pixels.

Re-running Import on a linked scene performs a **sync**: new nodes appear as new
placeholder elements, removed nodes are flagged (never deleted automatically — each
can be resolved as *keep* or as an explicit, confirmed *delete* that also sweeps the
element's registration empty, guide boxes and rig; a bulk *Delete Orphaned Elements*
action deletes all of them after one confirmation), and changed offsets/frames/sizes
are listed with a per-item accept-theirs / keep-mine choice (drawn as *Use Project's /
Keep Scene's*). The Sync & Export panel stages this as Check → Resolve → Write: the
read-only diff (*Check for Differences*) first, the per-item choices and *Apply
Choices* second, and the Lua write (*Write Layout to Lua*, §6.2) last.

An element's registration empty **is** its position, so dragging one is a layout edit
that lives only in the scene until an export writes it out. That drift is first-class:
every comparison between scene and project (validate, sync, export) reads the empty back
through the current calibration and carries it alongside the stored offsets, so a moved
element is reported rather than sitting invisible until an export quietly writes it.
*Re-import & reposition* discards those moves by definition, so it **confirms first**,
naming every element whose scene value would be overwritten.

### 6.2 Export (Blender → Lua)

Two modes, chosen per project:

- **Patch mode** (default for existing projects): RE-Blend updates only the fields it
  owns — `offset = { x, y }` and `frames = N` values of nodes it knows — leaving all
  other content, comments, and formatting untouched. Implemented as anchored structural
  edits, not a reserialisation of the whole file (hand-maintained comments in these
  files are often load-bearing documentation).
- **Generate mode** (greenfield): RE-Blend emits complete `device_2D.lua` and a skeleton
  `hdgui_2D.lua` (widget stubs with `graphics.node` filled and `value = "/custom_properties/TODO"`
  placeholders) from the scene, formatted to match SDK-example conventions.

Export never touches `motherboard_def.lua` — properties are the developer's contract;
RE-Blend only reads it for validation.

Patch mode **confirms before writing**, listing every value it will change (node, panel,
old → new). The safeguards below it — anchored edits, verification by re-parse, refusal
on ambiguity — protect against RE-Blend corrupting the file; the confirmation protects
against RE-Blend faithfully writing something the designer did not mean. An optional
`.bak` copy is offered in the same dialog.

### 6.5 Optional layout editing (deliberate RE Edit overlap)

Some of what RE Edit does is worth having inside Blender too, because during art
production Blender *is* where the designer already is, with the real 3D art on screen
instead of exported PNGs. Overlap is embraced where the Blender context adds value, and
skipped where it doesn't:

**Worth overlapping (planned, post-M2):**

- **Full placement editing in-viewport**: dragging elements with panel-pixel snapping,
  alignment/distribution operators, and zone guides — with the result exported via
  patch mode. This is RE Edit's core placement feature, but doing it against the live
  3D art (and having the offsets land back in both the Lua *and* the render pipeline)
  is strictly better during the art phase.
- **Adding/removing widgets from Blender**: creating a new element from the control
  library can append the corresponding `device_2D.lua` node *and* an `hdgui_2D.lua`
  widget stub (extending generate mode's skeleton emission to incremental edits), so a
  new knob doesn't require a round-trip through a text editor or RE Edit.
- **Widget property panel**: editing the fields RE-Blend understands (node/graphics
  binding, `frames`, `handle_size`, `display_width/height_pixels`, bound property path
  chosen from the parsed `motherboard_def.lua` list) directly on the RE Element, written
  back through the same anchored-edit machinery.

**Not worth overlapping (still out of scope):** widget types or attributes RE-Blend has
no visual representation for, device-flavour variations, and anything requiring
emulation of Reason's runtime widget behaviour — for those, RE Edit or a text editor
remains the right tool. When RE-Blend encounters widget attributes it doesn't model, it
preserves them byte-for-byte on write (the patch-mode guarantee), so mixed workflows —
RE-Blend for art and placement, RE Edit for fine widget tuning — stay safe in both
directions.

### 6.3 Validation report

A **Validate** panel runs the full cross-check and lists errors/warnings with
click-to-select:

| Check | Severity |
| --- | --- |
| Node in Lua with no RE Element in the scene (missing art) | error |
| RE Element with no node in Lua (orphan art) | warning |
| `re_frames` ≠ `frames` in `device_2D.lua` | error |
| Frames ≠ steps of the bound stepped property (via `motherboard_def.lua`) | warning |
| Rendered PNG dims ≠ `frame_w × frame_h × frames` | error |
| `path` PNG missing from `GUI2D/` | warning (until first render) |
| Case mismatch between `path` and file name (case-sensitive at build time) | error |
| Alpha bleed at frame borders (§5.2) | warning |
| Non-Standard view transform / non-sRGB output | warning |
| Element bounding boxes overlapping / outside panel bounds | warning |
| Widget type ↔ element kind mismatch (e.g. knob rig on a `toggle_button` node) | warning |
| Geometry slides across the panel between frames with its shadow baked into the background (§5.1) | error |
| Geometry moves only along the camera axis with its shadow baked into the background | warning |

The same validation runs headlessly with a non-zero exit code on errors (§7), so it can
gate a build.

### 6.4 Lua parsing/writing strategy

`device_2D.lua` / `hdgui_2D.lua` are declarative Lua (tables plus `jbox.*`
constructors and a `format_version`). Options considered:

1. **Embedded Lua interpreter** (bundle the `lupa` wheel; Blender 4.2+ extensions can
   ship wheels): execute the files in a sandbox with a stub `jbox` table that records
   constructor calls. Highest fidelity for *reading* — anything the SDK accepts, RE-Blend
   reads, including files RE Edit wrote.
2. Pure-Python tolerant parser: no binary dependency, but a second grammar to maintain
   and a fidelity risk.

**Decision: option 1 for reading; patch-mode structural edits (not reserialisation) for
writing** (§6.2). Interop rule: RE-Blend must correctly read files written by RE Edit and
the SDK examples, and files RE-Blend writes must load in RE Edit — this is a test-suite
fixture requirement, with the SDK's example devices (e.g. the stereo-FX
`SilenceDetectionEffect`) plus at least one real-world project as fixtures.

## 7. Headless / CI operation

Everything the UI does must be drivable via operators with no UI state, so:

```
blender -b MyDevice.blend --python-expr "import reblend; reblend.cli()" -- \
    render --all --project /path/to/mydevice --strict
```

- `render --all | --dirty | --element knob_cutoff`
- `validate` (exit code ≠ 0 on errors → CI gate)
- `export-config --patch`
- Manifest output (JSON): per-sheet hashes, sizes, frame counts — diffable in review, and
  a machine-readable version of the artist↔developer hand-off contract that RE projects
  otherwise keep in hand-maintained tables.

This lets a build machine (or a CI runner with Blender) regenerate `GUI2D/*.png`
reproducibly from the committed `.blend`, making art a build product instead of an
opaque binary drop.

## 8. Architecture

- **Platform:** Blender **4.2 LTS+**, shipped as a Blender **extension**
  (`blender_manifest.toml`), which permits bundling Python wheels (`lupa`; `numpy` ships
  with Blender). Pure Python; no compiled RE-Blend code.
- **Modules:**
  - `project/` — project link, Lua read (sandboxed interpreter + `jbox` stubs), Lua
    patch-writer, palette loader, manifest.
  - `model/` — RE Element schema (custom properties), state tables, rig
    generators (knob driver, state keyframes), calibration.
  - `render/` — render queue, per-element scene state push/pop (visibility, camera,
    resolution, colour management), strip stitcher (numpy over `bpy` image pixels — no
    external image dependency), overflow/alpha validators, flipbook/contact sheet,
    panel compositor.
  - `ui/` — N-panel tab ("RE-Blend"), its panels ordered the way the work runs:
    project setup → element list (with status badges: active / orphaned / moved /
    unsized — per-element "synced/dirty" tracking is M3, with the render manifest) →
    active element with its State Table and, below it, the Rig sub-panel → render (with
    its QA report) → validation → preview/state playground → sync & export, staged
    Check → Resolve → Write. Buttons whose preconditions aren't met are disabled with
    an adjacent explanation rather than erroring after the click.
  - `cli.py` — headless entry points (§7).
- **No SDK code or assets are bundled.** RE-Blend points at tool *paths* the user
  configures per machine (RE2DRender/RE2DPreview launch is optional convenience). This
  keeps RE-Blend cleanly outside the RE SDK licence: it only reads/writes the user's own
  project files. RE-Blend is open source under **GPL-3.0-or-later** (see `LICENSE`) —
  add-ons that import `bpy` are conventionally GPL, and this settles the question that
  earlier drafts left open.
- **Scene data versioning:** every `re_*` schema carries a version int; migrations run
  on file load, since `.blend` files outlive add-on versions.

## 9. Non-goals (v1)

- Generating the 0.5× asset set (RE2DRender's job).
- Editing `motherboard_def.lua`, `realtime_controller.lua`, `display.lua`, or any C++.
- Rendering custom-display *content* (meters etc. are drawn live by the device's
  display code; RE-Blend only renders their housings and marks their cut-outs).
- Panel typography/engraving authoring (import-friendly, not authored in v1; see §5.6).
- Widget attributes RE-Blend has no visual model for (preserved byte-for-byte on write;
  edited in RE Edit or a text editor — see §6.5 for where overlap *is* planned).
- Building `.u45` packages or driving Recon.

## 10. Risks and open questions

1. **Straight-alpha fidelity.** Blender composits premultiplied internally and its PNG
   save path has historically had edge cases around unassociated alpha with emissive
   semi-transparent pixels. Mitigation is designed in (§5.2): verify the written file,
   and if needed un-premultiply explicitly in the stitcher before save. Must be nailed in
   the first spike, on real SDK input (RE2DRender is the acceptance test).
2. **Lua patch-writing robustness.** Anchored edits on hand-written Lua can be fooled by
   exotic formatting. Constrain: patch mode only rewrites number literals inside
   `offset = { … }` / `frames = …` of nodes it located via the interpreter-read pass; on
   any anchor ambiguity it refuses and tells the user to use generate mode or edit
   manually. Never corrupt a file silently.
3. **Colour fidelity at 0.5×.** Fine grain/knurl detail can alias when RE2DRender
   downscales. Mitigation: contact-sheet QA at 50% zoom (§5.4), material kit tuned for
   downscale legibility (§5.7). Worth an explicit "preview at 0.5×" toggle in the panel
   compositor.
4. **SDK behaviour the documentation does not reach.** *Resolved, in part:* SDK 4.6.0
   does publish a GUI designer manual, GUI design guidelines and a full scripting
   specification, and `docs/sdk-gui-reference.md` transcribes what they say about panel
   geometry, per-widget frame counts and the parts whose appearance is fixed. That
   answers the `sequence_fader` question this risk used to leave open: a fader's
   animation must include the handle's entire travel, one frame per position, and
   `handle_size` configures press-to-jump hit behaviour rather than a drawing mode —
   there is no 1-frame-moving-handle authoring pattern to support.
   What remains genuinely empirical is narrower but real: RE2DRender does not *enforce*
   most documented requirements (it echoes back whatever panel size it is handed, which
   is how RE-Blend came to carry a 130 px folded height for a milestone), and its
   undocumented behaviours — the multiple-of-5 frame bounds, the `-reframed` copy, the
   `gui.lua`-stage checks — are only knowable by running it. So the rule stands with its
   emphasis moved: verify against RE2DRender/Recon, but never treat "RE2DRender accepted
   it" as evidence that a *requirement* is met. Capture both kinds of finding in
   RE-Blend's own docs.
5. **RE Edit overlap without RE Edit's maturity.** Overlap is deliberate (§6.5), but a
   half-working in-Blender widget editor is worse than none: it must never write
   anything the SDK or RE Edit rejects. Guard rails: layout editing lands only after
   patch-mode round-tripping is proven (post-M2), unknown widget attributes are always
   preserved verbatim, and the RE Edit/SDK-example interop fixtures (§6.4) gate every
   write-path feature.
6. **Blender version churn.** Extension platform + LTS targeting limits this; CI should
   run the headless suite against current LTS and latest stable.

## 11. Milestones

Development is anchored to a **pilot project**: a real, in-flight Rack Extension whose
`GUI2D/*.lua` files are already wired and whose art is produced entirely through
RE-Blend. Milestone exit criteria are phrased against it on purpose — anything the pilot
project's pipeline needs that RE-Blend can't do is a design bug in this document.

| Milestone | Contents | Exit criterion |
| --- | --- | --- |
| **M0 — Spike** | Calibration, one hand-tagged knob element, turntable driver, strip render + stitch, straight-alpha verification | A 61-frame knob strip rendered from the pilot project's `.blend` that RE2DRender accepts and that turns smoothly in RE2DPreview |
| **M1 — MVP** | Project import (read-only), RE Element schema + rigs for all kinds, batch render, validation report | The pilot project's complete phase-1 sheet list rendered from one `.blend`, zero validation errors |
| **M2 — Sync** | Patch-mode export, re-import merge, panel compositor preview, flipbook/contact sheet, SDK tool launch | Move a control in Blender → `device_2D.lua` offset updates → RE2DPreview confirms; RE Edit still loads the patched files |
| **M3 — Production** | Headless CLI + manifest, dirty-only rendering, palette/material/lighting kits, emission-pass export | The pilot project's art build runs headless on its build machine; its docs can point at RE-Blend as *the* art pipeline |
| **M4 — Library & layout editing** | Parametric control library (asset browser), generate-mode config export, in-viewport placement editing + widget add/remove/property panel (§6.5), greenfield template, docs | A new blank RE gets from empty scene to previewable panel without hand-editing Lua or leaving Blender |

## 12. Reference material

The SDK **does** document its 2D GUI contract, in three documents under `SDK/API/`.
An earlier draft of this document claimed otherwise and reverse-engineered rules that
were already written down; `docs/sdk-gui-reference.md` records what they actually say,
with the corrections that followed. Those documents are Reason Studios' "all rights
reserved" material and are never committed to this GPL-3.0 repository — cite them, do
not copy them. In order of authority:

- **`SDK/API/jukebox-scripting-specification.html`** — normative for `hdgui_2D.lua`: all
  25 widget types, their attributes, and the frame count each animation must have. This
  is the reference for anything widget-shaped.
- **`SDK/API/gui-design-guidelines.html`** — the **requirement**/guideline split. Panel
  dimensions, the 9U ceiling, edge margins, the mandatory device parts, and the
  canonical lighting rig. Requirements here are submission-blocking.
- **`SDK/API/gui-designer-manual-2d.html`** — the `device_2D.lua` format, nesting, panel
  and snapshot dimension tables, and how RE2DRender is invoked.
- **The SDK's example devices** (`SDK/Examples/`) — worked examples of file shape and
  stock-part usage (e.g. `SharedAudioJack`); the stereo-FX `SilenceDetectionEffect` is a
  good canonical fixture. Useful, but the specification outranks them.
- **The SDK tools' observed behaviour** — still the ground truth for what the *tools* do,
  which is not the same as what the spec requires: RE2DRender enforces very little.
  RE-Blend's test suite must encode these findings (see risk §10.4).
- **`SDK/Documentation/acceptance_testing_checklist.txt`** — the checklist Recon
  validates against (e.g. the On/Off/Bypass control appearing on the folded front).
- **`SDK/API/Jukebox.h` / `JukeboxTypes.h`** — the API source of truth for anything
  touching the native side (RE-Blend itself stays out of it).
- **pongasoft's open-source RE tooling** — [RE Edit](https://github.com/pongasoft/re-edit)
  (the interop peer for `device_2D.lua` / `hdgui_2D.lua`, and prior art for parsing them
  via re-mock), re-mock, and re-cmake.
- **Blender** — extensions platform (`blender_manifest.toml`, bundled wheels), colour
  management (Standard vs Filmic/AgX), driver & custom-property APIs.

— End of design document —
