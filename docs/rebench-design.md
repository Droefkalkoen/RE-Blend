# RE Bench: layout, rules and prototyping bench

RE Bench is a second package in this repo (`rebench/`, beside `reblend/`) for deciding a Rack
Extension's panel layout before any Blender work. The full design, with the rule tables and
rationale, is the "RE Layout Bench" design doc (2026-11-10) in the Terp Audio: Skou project;
this file is the in-repo summary agents work from. Agreed with the owner on 2026-11-10:
**browser bench inside RE-Blend, sharing its pure-Python core.**

## What it does

1. **Rules.** A shared rule catalogue (about 35 checks) that both the Blender validator and the
   bench run. Each rule records its source, because the evidence differs:
   - `req`: requirement in the SDK 4.6.0 GUI design guidelines (rejected at submission);
   - `spec`: Jukebox scripting specification;
   - `guide`: SDK guideline;
   - `obs`: observed RE2DRender / Recon behaviour (record the tool or Reason version);
   - `house`: a brand rule from a profile (Terp Audio's rules came from Skou).
2. **Layout.** A browser canvas at 1 SVG unit = 1 hi-res pixel, with Reason's reserved areas as
   overlays (fold arrow, rack ears and holes, 25 px margins, placeholder), real SDK sprites for
   SDK parts, live findings, 5 px snapping, and 1x / 0.5x views.
3. **Handoff.** Writes `device_2D.lua` and `hdgui_2D.lua` (patch mode on existing projects,
   generate mode on new ones) plus a sidecar, `GUI2D/rebench.json`, for what Lua cannot carry:
   frame sizes, blockout shapes, label text and size, zones. `rebench greybox` writes flat
   placeholder sprites so a new device builds and opens in Recon before Blender. RE-Blend's
   import reads the sidecar into blockout geometry and `re_frame_w` / `re_frame_h`.
4. **DSP (optional).** The device's real C++ built against a mock Jukebox host (pongasoft
   re-mock, licence and API to confirm): offline renders with LUFS / spectrum / null reports
   first, a live native audio host behind the canvas controls later.

## Ground rules

- **The RE project's Lua files are the source of truth for positions.** The bench and
  RE-Blend never talk to each other; they meet in the project files. The sidecar holds only
  what Lua cannot.
- **One rule engine.** Rules move out of `reblend/project/validation.py` into the catalogue;
  nothing is checked in one tool and not the other.
- **`rebench.core` stays pure Python** (no `bpy`), like `reblend.project`.
- **Agents use the same operations as the UI**: `project.load`, `layout.apply`,
  `layout.dry_run`, `rules.check`, `view.render` (PNG via headless Chromium),
  `greybox.build`, `dsp.render`, `dsp.measure`. Exposed as a CLI with JSON output and as an
  MCP server. Operations are atomic and return the findings they caused, with a fix hint.
- Existing projects (Skou) need no changes to adopt it; the sidecar starts empty.

## Phases

1. **Rules CLI.** Shared catalogue, `rebench check --json`, section B of
   [`skou-feedback-triage.md`](skou-feedback-triage.md). Fold-arrow and rack-hole geometry
   measured from Recon screenshots. Done when it flags the Skou issues against an older Skou
   commit.
2. **Canvas and agent API.** Done when an agent can move a Skou control, see the findings and
   post a screenshot.
3. **Greenfield and handoff.** Templates, Lua stubs, grey-box sprites, RE-Blend reading the
   sidecar. Done when a new effect goes from template to Recon, then into Blender as a blockout.
4. **Offline DSP.** Done when Skou's gain-match and render-test checks run against the real
   `Device.cpp` and Skou's hand-written `test/render/skou_model.cpp` can be retired.
5. **Live DSP.**

## Phase 1: what exists now

```sh
python3 -m rebench check <project root> [--json] [--strict]   # exit 1 on errors (or warnings with --strict)
python3 -m rebench rules                                       # the catalogue, with each rule's source
```

- `rebench/catalogue.py`: one entry per finding code, RE-Blend's validator codes included, each
  with its source (`req`, `spec`, `guide`, `obs`, `house`, `tool`). Codes that need a Blender
  scene (state tables, rotors, shadow travel) are marked `scene_only` and skipped by the bench.
  A test fails if `validation.py` emits a code the catalogue does not know.
- `rebench/layout.py`: the bench's own rules: `fold-arrow`, `rack-ear`, `bypass-top-left`,
  `device-name`, `socket-panel`, `display-panel`, `sdk-part-frames`.
- `rebench/profile.py`: the house and observed numbers (ear keep-out, fold-arrow keep-out,
  bypass corner, text floor), overridable per project with `rebench.profile.json` beside
  `GUI2D/`. The fold-arrow keep-out is measured from Reason screenshots
  ([`findings-fold-arrow.md`](findings-fold-arrow.md)), so an overlap is an error.
- Exit check: run against Skou's files from before its readability/SDK audit commit, it reports
  the folded tape under the fold arrow and the folded bypass fader not top-left (Skou feedback
  #29), the two layout issues that audit fixed by hand.

Not in phase 1, because the project files do not carry the information: routing icons and
rack-hole screws (they are panel art), and fader frame direction (needs the rendered frames).
Text size arrived with the sidecar in phase 2.

## Phase 2: what exists now

```sh
python3 -m rebench serve <project root> [--port 8765]          # the canvas, on 127.0.0.1
python3 -m rebench apply <project root> ops.json [--dry-run] [--json]   # exit 3 when refused
python3 -m rebench render <project root> --panel front --out front.png [--scale 0.5] [--ops ops.json]
python3 -m rebench mcp [--project <project root>]              # MCP server on stdio
```

- `rebench/sidecar.py`: `GUI2D/rebench.json` (schema version 1): per-sprite frame size and
  blockout shape, and labels (text, panel, position, size in px, functional or decorative).
  Missing means empty, so existing projects need no change. Sidecar frame sizes win over the
  PNG probe, which is how a designer-set size survives a stale render.
- `text-size` rule (`guide`): a functional label under the profile's `min_text_px` (43) warns.
- `rebench/ops.py`: the edit operations (`move`, `nudge`, `set_frames`, `set_frame_size`,
  `set_shape`, `label`, `remove_label`). A batch applies whole or not at all; positions and
  frame counts go through RE-Blend's patch mode, so comments and unknown attributes survive and
  an ambiguous anchor refuses. Every apply reports the findings it added and resolved.
- `rebench/render.py`: a panel as SVG at 1 unit = 1 hi-res px (frame N of each sprite, grey
  blockouts for missing art, labels, findings outlined on their nodes, hatched margins, ear
  lines and the measured fold-arrow keep-out), and PNG via headless Chromium.
- `rebench/server.py` + `static/index.html`: the canvas. Drag nodes and labels (5 px snap),
  click to place a label, switch panels, zoom (fit, 25 %, 50 %, 100 %), show any sprite frame.
  Edits are staged and previewed by dry run; nothing is written until **Write to Lua**.
- `rebench/agent.py` + `rebench/mcp_server.py`: the agent operations `project_load`,
  `rules_check`, `layout_dry_run`, `layout_apply`, `view_render` (underscores, since not every
  MCP client accepts dots). `view_render` returns the PNG as an image so an agent can look.
  Standard library only; Playwright is needed for PNGs alone (`pip install -e ".[screenshots]"`).
- Exit check: on a copy of Skou's files, dragging `meter_input` 60 px right in the canvas
  resolves the rack-ear finding and **Write to Lua** changes that one offset, comment intact;
  the same move by `layout_dry_run` and a `view_render` screenshot work over MCP.

## Phase 3: what exists now

```sh
python3 -m rebench new <dir> --name "Grit Distortion" --manufacturer "Terp Audio" [--height 2] [--sdk <SDK root>]
python3 -m rebench greybox <project root> [--sdk <SDK root>] [--dry-run]
```

- `rebench/templates/effect/` and `rebench/new.py`: a stereo `creative_fx` with one Level knob,
  bypass fader, tape on all four panels, jacks, placeholder and cable origin, auto-routing and
  auto-bypass, a gain-stage C++ (On/Off/Bypass, mono in, silence) and a re-cmake build. It
  passes `rebench check` with no findings; the template files are free to use (see
  `rebench/templates/README.md`).
- `add_control` op (`rebench/stubs.py`, Skou feedback #15): node, widget, a `document_owner`
  property stub with the lowest free tag, and its English name, as anchored insertions with
  refusal on ambiguity and a re-parse of every edited file. Knob, toggle, momentary and stepped
  fader get stubs; any widget can bind an existing property. The canvas has an Add control form.
- `rebench/greybox.py`: flat placeholder sheets at the right size and frame count (knobs turn,
  faders start at the bottom, buttons light), SDK parts copied from the SDK when given. Each
  placeholder's hash goes in the sidecar's `greybox` table and only an unchanged placeholder is
  redrawn, so a render is never overwritten. The canvas has a Greybox button.
- The sidecar format moved to `reblend/project/sidecar.py`. `load_project` takes frame sizes and
  shapes from it (designer sizes beat the PNG probe, Skou feedback #3/#11), and a first import
  adds a blockout mesh per shaped element (`reblend/model/blockout.py`; the mesh maths is
  tested, the Blender step is not, as no Blender runs in CI).
- `AGENTS.md`: how agents drive both tools, and the SDK rules in our own words.
- MCP gains `greybox_build` and `project_new`.
- Exit check (to run on the build machine): `rebench new`, build, open in Recon; then import into
  Blender and see the blockouts. In this repo the template's C++ passes a syntax check against
  `Jukebox.h` 4.6.0 and its Lua loads.

Not yet: an instrument template, removing controls, routing-icon art and its check.

## Open items

- Text floor for Terp devices: 43 px warning (SDK guideline) or error.
