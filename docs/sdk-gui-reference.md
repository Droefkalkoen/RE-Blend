# The RE SDK's 2D GUI rules, written down

RE-Blend's design document (§12) used to claim the Rack Extension SDK ships **no formal GUI
authoring manual**, and that the only references were the acceptance-testing checklist, the
example devices, and RE2DRender's observed behaviour. That was wrong. SDK 4.6.0 publishes three
documents that between them specify the whole 2D GUI contract:

| Document | What it is authoritative for |
| --- | --- |
| `gui-designer-manual-2d.html` | Panel and snapshot dimension tables, the `device_2D.lua` format, nesting, required widgets, RE2DRender invocation |
| `gui-design-guidelines.html` | The **requirement**/guideline split, panel dimensions, edge margins, mandatory device parts, the canonical lighting rig |
| `jukebox-scripting-specification.html` | The normative `hdgui_2D.lua` reference: all 25 widget types, their attributes, and their animation frame counts |

They live under `SDK_v4.6.0/API/` in an SDK installation, alongside `Jukebox.h`. **They are not
in this repository** and must not be: RE-Blend is GPL-3.0-or-later and the SDK is Reason Studios'
"all rights reserved" material. This file records the *facts* those documents state, with
citations, so RE-Blend's code can be checked against a spec rather than against a guess.

Where a rule below says **Requirement**, the guidelines mark it as one: a device that breaks it
is rejected at submission. Crucially, **RE2DRender does not enforce most of them** — it will
happily compile art that will later be rejected, which is precisely the silent-failure class
RE-Blend exists to close. Do not treat "RE2DRender accepted it" as evidence that a requirement is
satisfied.

---

## 1. Panel geometry

**Requirement.** Every device has four panels: front, back, folded front, folded back. (Players
are the exception — see §5.)

| Panel | Width | Height |
| --- | --- | --- |
| Front | 3770 | 345 × N |
| Back | 3770 | 345 × N |
| Folded front | 3770 | **150** |
| Folded back | 3770 | **150** |

- **Requirement.** All panel images are **3770 px** wide, including Players' (the visible width
  is narrower; the build pipeline crops the side margins).
- **Requirement.** Height is a whole number of rack units — 1U = **345 px** — and **must not
  exceed 9U**. Front and back must be the same height.
- **Requirement.** Folded front and folded back are both exactly **150 px**.

> **Correction.** RE-Blend carried 130 px for folded panels from M0 finding 7 through
> `calibration.FOLDED_HEIGHT_PX`, the design doc and the findings note. RE2DRender echoes back
> whatever backdrop size it is handed, so the M0 run confirmed nothing; both SDK documents state
> 150. Fixed.

### Custom snapshots (optional)

RE2DRender generates device thumbnails automatically, but picks up hand-made ones from `GUI2D/`
when they exist at exactly the right size:

| File | Width | Height |
| --- | --- | --- |
| `ThumbSnapshotFront.png` | 754 | 69 × N |
| `ThumbSnapshotFoldedFront.png` | 754 | 30 |

### Margins and bounds

- **Requirement.** At least a **25 px** empty margin along the left and right edges of every
  panel, with respect to *interaction* widgets: nothing that responds to user input may intersect
  it. `graphics.hit_boundaries` shrinks a widget's interactive area inward from its rectangle, so
  a rectangle that reaches into the margin is not automatically a violation — which is why
  RE-Blend reports this as a warning rather than an error.
- **Requirement.** Every widget boundary must be **completely inside** its panel.
- **Requirement.** Widget boundaries **may not overlap**, except for `static_decoration` widgets
  and widgets with a `visibility_switch` that cannot be visible simultaneously.
- *Guideline.* Keep 25 px clear at the top and bottom edges too.

### Required device parts

- **Requirement.** Every effect device has an on/off/bypass control in the **top left** corner.
- **Requirement.** The (non-folded) **back panel** declares a `jbox.placeholder`, a reserved
  **300 × 100** region kept clear of decoration.
- **Requirement.** The **folded back panel** declares `cable_origin`. The specification adds that
  it "must not be present in any other panel specification".
- **Requirement.** The device name tape appears on **all four** panels.
- **Requirement.** The folded back panel has a hole where the patch cables originate.
- **Requirement.** Back panels carry routing symbols using the SDK's `Routing_Icon_*` art.

---

## 2. Image format

- **Requirement.** All 2D assets are **PNG**.
- **Requirement.** Film strips are **vertical**, first frame at the top.
- **Requirement.** Strip height is an integer multiple of the frame count.
- Alpha is used for compositing. RE-Blend additionally requires 8-bit **straight**
  (un-premultiplied) alpha and verifies it on write — see design §5.2 / risk §10.1.
- Empirically (M0 finding 6, not in the SDK docs): per-frame width and height must be multiples
  of 5, or RE2DRender silently emits a `-reframed` copy and registration breaks.

---

## 3. Widget frame-count contract

This is the part RE-Blend previously guessed at, and got wrong in three places. Every animated
widget's frame count is fixed by the specification, and for most of them it has **nothing to do
with the bound property's `steps`**. Encoded in `reblend/model/kinds.py` as `WIDGET_FRAME_RULES`.

| Widget | Frames | Meaning |
| --- | --- | --- |
| `analog_knob` | any | animation resolution is the artist's choice |
| `zero_snap_knob` | any | snaps to the frame nearest the property's zero |
| `pitch_wheel` | any | snaps back to the animation's midpoint on release |
| `toggle_button` | **2 or 4** | off, on — or off, off-held, on, on-held |
| `momentary_button` | **2** | released, held |
| `step_button` | **2** | released, held |
| `radio_button` | **2** | released, held |
| `up_down_button` | **3** | neutral, up-held, down-held |
| `sequence_fader` | N | the handle's full travel, one frame per position, linear |
| `sequence_meter` | N | value → frame; this is the lamp/meter widget |
| `static_decoration` | **1** | "cannot be animated" |
| `custom_display` | **1** | "must not be animated" |

Three consequences worth stating plainly, because RE-Blend assumed otherwise:

1. **`step_button` and `radio_button` are not selectors.** Both are ordinary two-frame
   released/held buttons. A radio group over an 8-value property is **eight separate widgets**,
   each with its own `index`, its own node and its own two-frame sheet — not one 8-frame sheet.
   RE-Blend used to map all three stepping buttons to a `selector` kind and warn when their frame
   count differed from the property's `steps`, which is backwards: matching `steps` would be the
   bug.
2. **`sequence_fader` always bakes its travel.** The specification is explicit: "The animation
   must include the entire travel distance of the handle… The amount the handle travels between
   each animation frame must be constant." `handle_size` specifies the handle's extent along the
   orientation axis so a press *outside* the handle knows where to jump — it is hit behaviour,
   not a drawing mode. There is no "1-frame handle the SDK slides along a track" authoring
   pattern. This closes the open question in design risk §10.4.
3. **`static_decoration` cannot be an animated lamp.** The widget for an animated indicator is
   `sequence_meter`, bound to an `rt_owner` property. RE-Blend's `silence_detector` fixture used
   to place a 2-frame lamp under a `static_decoration` and the kind-derivation code treated that
   as "the SDK-example lamp pattern"; both have been corrected.

`sequence_fader` is the one widget whose frame count *should* track `steps` when it drives a
stepped property — one frame per detent. RE-Blend keeps that as a warning, since a fader may
legitimately drive a continuous property or use `value_switch`/`values` instead of a single
`value`.

---

## 4. Widgets whose art is not yours

Two groups RE-Blend must never render.

### SDK-supplied parts

For these the specification says "you cannot change the appearance of this widget" and names the
stock image; the guidelines repeat each as a requirement. RE-Blend resolves them out of the SDK
folder configured in the add-on preferences and copies them into `GUI2D/` under whatever sprite
name `device_2D.lua` already uses (**RE-Blend > Install SDK Parts**, `reblend/project/sdk_parts.py`).
The stock images live in `RE2DRender/Images` — the "RE2D package" the specification refers to —
and the example devices carry their own copies.

| Widget | Stock part |
| --- | --- |
| `audio_input_socket`, `audio_output_socket` | `Cable_Attachment_Audio` (`SharedAudioJack` in the examples) |
| `cv_input_socket`, `cv_output_socket` | `CV_Attachment_Audio` |
| `cv_trim_knob` | `Trim_Knob` |
| `device_name` | `Tape_Horizontal` / `Tape_Vertical` |
| `placeholder` | `Placeholder` (300 × 100) |
| `patch_browse_group` | `Patch_Browse_Group` |
| `sample_browse_group` | `Sample_Browse_Group` |

### Bounds-only widgets

`value_display`, `popup_button`, `patch_name` and `sample_drop_zone` use their graphics
definition purely as a rectangle — to lay text into, or to hit-test against. The GUI designer
manual's worked example spells this out: a `value_display` positioned over a red box notes that
"the red box will not be drawn in Reason." Only the rectangle's position and size matter, so
RE-Blend skips rendering them and validates the geometry instead.

`custom_display` sits between the two: the region's own art (bezel, screen backdrop) *is*
authored, but it may not animate, and its runtime contents belong to `display.lua`. It also takes
an optional `background = jbox.image{...}`, supplied hi-res as `<resource-name>-HD.png`.

### `graphics.main_node`

`static_decoration` and `custom_display` are declared with `graphics = { main_node = ... }` rather
than `graphics = { node = ... }`. RE-Blend accepts **both** spellings on read: honouring only
`node` silently detaches those widgets from their node, after which kind derivation falls through
and validation reports the node as unbound.

---

## 5. Players

A Player device sets `panel_type = "note_player"` in `device_2D.lua` and defines **only** `front`
and `back` — Players have no folded panels. Panel images are still authored 3770 px wide; the
visible width is 3590 (front) / 3180 (back).

---

## 6. Running RE2DRender

```
RE2DRender <absolute path to GUI2D> <absolute path to GUI> [hi-res|hi-res-only]
```

The third argument matters more than it looks:

- **omitted** — renders **only the legacy lo-res (0.5×) form**, as pre-hi-res versions did.
  Reason and Recon 12+ do not use it.
- `hi-res` — both forms. What a submission build wants.
- `hi-res-only` — skips the lo-res pass; the fast choice while iterating.

RE-Blend's one-click launch used to omit the argument entirely, so it produced lo-res-only output
for a Reason 12+ acceptance loop. It now defaults to `hi-res-only` and offers the other two.

Either way: **never hand-author the 0.5× set.** Generating it is RE2DRender's job (design §5.2, §9).

---

## 7. The canonical lighting rig

The guidelines publish the light setup Reason's own devices use, and recommend matching its
direction and shadow contrast so a device sits plausibly in the rack. Useful as the basis for
design §5.7's lighting kit.

| Light | Direction | Colour (floating-point RGB) |
| --- | --- | --- |
| Main | (−0.08, 0.38, 1.0) | (3.0, 3.0, 3.0) |
| Environment 1 | (−1.0, 0.02, 0.1) | (0.9563, 0.8467, 0.6864) |
| Environment 2 | (1.0, 0.02, 0.1) | (0.7253, 0.8484, 0.9925) |
| Environment 3 | (0.15, 0.0, 1.0) | (0.4369, 0.4369, 0.4369) |

*Guideline.* Use an orthogonal projection — straight from the front, no perspective. RE-Blend's
per-element cameras are orthographic by construction (design §4.4).

Readability: *guideline* — 43 px is a safe font size for functional text to stay legible on a
present-day 27" display.

---

## 8. What is still empirical

The SDK documents do not cover everything. These remain findings from observed tool behaviour,
recorded in `docs/findings-m0.md`, and the ground truth for them is still what RE2DRender accepts
and what RE2DPreview/Recon display:

- Per-frame width/height must be multiples of 5, or RE2DRender emits a `-reframed` copy.
- RE2DRender aborts if any of the four panels is missing from `device_2D.lua`.
- `cable_origin` is enforced at the `gui.lua` export stage, not at read time.
- Straight vs premultiplied alpha handling end to end (design risk §10.1).
- Whether RE2DRender accepts `node` where the spec writes `main_node` (RE-Blend accepts both, so
  reading is safe either way; this only matters for what generate mode should emit).
