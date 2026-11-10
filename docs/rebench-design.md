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

## Open items

- Fold-arrow keep-out rectangle: not in the SDK; measure from a Recon screenshot of a 2U device,
  front and back, folded and unfolded.
- Text floor for Terp devices: 43 px warning (SDK guideline) or error.
