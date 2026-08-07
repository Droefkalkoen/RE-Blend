# RE-Blend roadmap

This is the implementation plan for the design in
[`Blender_RE_Plugin_Design.md`](Blender_RE_Plugin_Design.md). The design doc says *what* and
*why*; this file says *in what order* and *when it counts as done*. Where the two disagree, the
design doc wins and this file gets fixed. Section references (§) point into the design doc.

## Ground rules

Everything here is anchored to a **pilot project** — a real, in-flight Rack Extension whose
`GUI2D/*.lua` files are already wired, and whose art will be produced entirely through RE-Blend.
Exit criteria are phrased against the pilot on purpose: if the pilot needs something RE-Blend
can't do, that's a design bug, not a nice-to-have (§11).

Two more rules that shape the ordering:

1. **The riskiest assumption goes first.** Straight-alpha PNG output from Blender is the one
   thing that could sink the whole approach (§10.1), so it gets proven in M0 against real SDK
   input — RE2DRender is the acceptance test — before anything else gets built on top.
2. **Nothing writes to a Lua file until round-tripping is proven.** Read-only comes first,
   patch mode comes with interop fixtures (files RE-Blend writes must load in RE Edit, §6.4),
   and the fancier layout-editing features stay parked until that's solid (§10.5). Corrupting
   someone's hand-commented config file once is one time too many.

## M0 — Spike: prove the pixels ✅ PASSED

The smallest thing that can fail: calibration, one hand-tagged knob element, the auto-generated
turntable driver, a strip render, in-process stitching, and straight-alpha verification of the
written file.

No UI polish, no import, no schema migrations — one knob, done properly.

**Done when:** a 61-frame knob strip rendered from the pilot project's `.blend` is accepted by
RE2DRender and turns smoothly in RE2DPreview. Smoothly means no wobble and no jumps — both
failure modes this milestone exists to kill.

**Status — PASSED.** A 61-frame knob rendered from the pilot `.blend` is accepted by RE2DRender
(2.0.11b258) and sweeps min→max in RE2DPreview with clean straight-alpha edges, no wobble, and
no jumps. The riskiest assumption (§10.1) is retired. What it took, and the RE2DRender input
contract discovered along the way, is written up in
[`docs/findings-m0.md`](docs/findings-m0.md) — including the **multiples-of-5 frame-size rule**
M1's `render/` must enforce so RE2DRender never reframes a sheet.

## M1 — MVP

**The MVP is: render a complete, existing device's sheet list from one `.blend`, correctly,
with the tool telling you when something's wrong.** One-directional — Lua is read, never
written. Concretely:

- **Project import (read-only)** (§6.1): parse `device_2D.lua` / `hdgui_2D.lua` via the
  sandboxed Lua interpreter with `jbox` stubs, materialise panel planes, bounding boxes,
  pre-configured rigs, and filled-in `re_*` properties.
- **RE Element schema** with versioning and migrations (§4.2, §8) — this lands early because
  `.blend` files outlive add-on versions and retrofitting migrations is miserable.
- **Rigs for all element kinds** (§4.3): knob driver, state tables for
  buttons/faders/selectors/lamps, statics.
- **Batch render** with per-element scene push/pop, stitching, alpha and overflow validation
  (§5.1, §5.2).
- **Validation report** (§6.3) in the UI, covering the full cross-check table.

**Done when:** the pilot project's complete phase-1 sheet list renders from one `.blend` with
zero validation errors.

**Status — implementation landed, exit criterion pending.** All five M1 work items are coded
and the Blender-independent layers are covered by the pytest suite (import/correlation, schema
migrations, state-table compilation, stitcher geometry, the full §6.3 cross-check table, and
the M0 multiples-of-5 rule enforced in `render/validators.py`). What remains is running it on
the pilot: import the pilot project, model into the imported boxes, and get the phase-1 sheet
list through Render All Sheets + Validate with zero errors on a machine with Blender 4.2 LTS+.

Worth saying out loud: the MVP is already useful. Even without write-back, "model in the boxes,
press render, get correct sheets" replaces the entire manual crop-and-stack workflow.

## M2 — Sync: earn the right to write

- **Patch-mode export** (§6.2): anchored edits to `offset` and `frames` only; refuse on any
  ambiguity rather than guess (§10.2).
- **Re-import merge**: new nodes appear, removed nodes get flagged, changed values get per-item
  accept-theirs/keep-mine resolution.
- **Panel compositor preview** and **state playground** (§5.3), **flipbook and contact sheet**
  (§5.4), optional one-click RE2DRender/RE2DPreview launch.
- **Interop fixtures** become part of the test suite here: SDK example devices (the
  `SilenceDetectionEffect` is the canonical one) plus the pilot project; every write-path
  feature is gated on them.

**Done when:** moving a control in Blender updates its `device_2D.lua` offset, RE2DPreview
confirms the move, and RE Edit still loads the patched files without complaint.

**Status — implementation landed, exit criterion pending.** All four M2 work items are coded:
patch-mode export (`project/lua_writer.py` — comment/string-aware anchored edits to only the
`offset`/`frames` literals, every ambiguity refused with the reason, and the patched text
re-parsed and compared against the intended tree *before* the file is atomically replaced),
re-import merge (`project/merge.py` diff + per-item accept-theirs/keep-mine in the Sync panel;
removed nodes are flagged and deleted only via an explicit, confirmed resolution), the panel compositor preview with the state
playground plus flipbook and contact sheet (`render/compositor.py` + operators), and one-click
RE2DRender/RE2DPreview launch (paths live in per-machine add-on preferences, never in the
repo). Write-path features are gated on interop fixtures in the test suite: the SDK-convention
`silence_detector` project and a hostile-formatting `patch_styles` fixture (comment/string
decoys, single-line nodes, quoted keys, CRLF) — every patch must round-trip through the real
Lua interpreter byte-exact outside the edited literals. What remains is the pilot pass: move a
control, export, and confirm in RE2DPreview and RE Edit on a machine with Blender 4.2 LTS+.

## M3 — Production: make it a build step

- **Headless CLI** (§7): `render --all | --dirty | --element`, `validate` with a non-zero exit
  code on errors, `export-config --patch`, JSON manifest with per-sheet hashes.
- **Dirty-only rendering** (§5.5) — a full device is easily 200+ frames across a dozen sheets
  times four panels; fast iteration is the whole game.
- **Palette loader, material starters, lighting rig template** (§5.7).
- **Emission-pass export** (§5.6) for the post-composite pipeline.

**Done when:** the pilot project's art build runs headless on its build machine, and its docs
can point at RE-Blend as *the* art pipeline.

## M4 — Library & layout editing

- **Parametric control library** in the asset browser.
- **Generate-mode config export** (§6.2): complete `device_2D.lua` plus a skeleton
  `hdgui_2D.lua` for greenfield projects.
- **In-viewport placement editing** with panel-pixel snapping, alignment/distribution
  operators, and widget add/remove/property editing from Blender (§6.5) — the deliberate
  RE Edit overlap, allowed in only now that patch-mode round-tripping has an M2 track record.
- **Greenfield template scene** and user-facing docs.

**Done when:** a brand-new, empty RE gets from blank scene to previewable panel without
hand-editing Lua or leaving Blender.

## Stretch goals

Not scheduled, not promised, but each one has a clear reason to exist. Roughly in order of how
much I'd want them:

- **0.5× preview toggle** in the panel compositor (§10.3). Fine grain and knurl detail alias
  when RE2DRender downscales; seeing the half-size result *before* export would catch it at the
  material-tuning stage. Cheap to build, disproportionately useful.
- **Auto frame-size from a rendered matte.** Frame size is the one number the RE Lua never
  carries (§5.2), so today the designer sets it by hand. Instead: render the active element's
  alpha matte (or every element's, in bulk), measure the tight bounding box of the non-zero
  alpha, and set `re_frame_w`/`re_frame_h` from it — rounding each dimension *up to the next
  multiple of 5* (breathing room, and it keeps the sheet on tidy pixel steps). Two thoroughness
  modes: **current frame only** (fast — one matte, assumes the widest frame is on screen) and
  **all frames** (render every frame `0…N−1`, union the per-frame boxes, so a knob whose pointer
  sweeps outside frame 0's silhouette or a fader whose handle travels still gets a box that
  contains every state). The matte render reuses the per-element camera and straight-alpha path
  the renderer already sets up, so this is measurement on top of existing machinery — no new
  export surface. Pairs naturally with the frame-size warning the validator already raises: this
  is the "just compute it for me" answer to that nag.
- **Render-manifest diffing in CI**: compare the manifest against the last committed one and
  fail (or comment on the PR) when a sheet changed that shouldn't have. The manifest exists
  from M3; this is a small script on top that turns it into an art-regression gate.
- **A practical GUI authoring reference.** `docs/sdk-gui-reference.md` now transcribes what the
  SDK's own GUI documents require (panel geometry, per-widget frame counts, the parts Reason
  owns) alongside the behaviours only RE2DRender can tell you (§10.4 — the multiple-of-5 frame
  bounds and the `-reframed` copy being the poster children). Growing that into a standalone,
  worked reference — with the failure mode each rule produces, which the SDK documents never
  state — would be worth more to the RE community than most features on this list.
- **Watch mode**: re-render dirty elements automatically on scene save. Dirty tracking exists
  from M3; this is a timer and a toggle.
- **Animated flipbook export** (GIF/MP4 of the knob sweep) for design review outside Blender —
  posting a turning knob in a chat thread beats attaching a 61-frame strip.
- **Brand kits**: bundling a project's palette, material parameters, and lighting rig as a
  shareable preset, so device number two of a brand starts looking like device number one on
  day one. The pieces all exist from M3; this is packaging.
- **Shared control library**: community-contributed parametric controls for the M4 asset
  browser. Depends entirely on there being a community, so it goes last.

## Non-goals

For clarity, restating what's out of scope no matter the milestone (§9): the 0.5× asset set
(RE2DRender's job), editing `motherboard_def.lua` / `realtime_controller.lua` / `display.lua` /
any C++, rendering custom-display content, panel typography authoring, and building `.u45`
packages. If a stretch goal ever seems to require one of these, the stretch goal is wrong.
