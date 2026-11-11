# Working on Rack Extensions with RE Bench and RE-Blend: a guide for AI agents

This guide is for an agent (or a person) designing a Reason Rack Extension's panels with the two
tools in this repository. It covers what each tool is for, how to drive it, and the SDK rules
you need to follow, written in our own words. You should be able to work through it **without
the Reason Studios SDK**. Where something still needs the SDK, section 9 says so.

Contents:

1. [The two tools](#1-the-two-tools)
2. [The workflow, start to finish](#2-the-workflow-start-to-finish)
3. [Anatomy of an RE project](#3-anatomy-of-an-re-project)
4. [RE Bench reference](#4-re-bench-reference)
5. [RE-Blend reference](#5-re-blend-reference)
6. [SDK rules: the device contract (Lua)](#6-sdk-rules-the-device-contract-lua)
7. [SDK rules: the panels (GUI)](#7-sdk-rules-the-panels-gui)
8. [SDK rules: the C++ realtime code](#8-sdk-rules-the-c-realtime-code)
9. [What still needs the SDK](#9-what-still-needs-the-sdk)
10. [Ground rules for agents](#10-ground-rules-for-agents)

Each rule below is marked with where it comes from. The same tags are used in the rule
catalogue (`python3 -m rebench rules`):

| Tag | Meaning | How firm |
| --- | --- | --- |
| **req** | A requirement in the SDK 4.6.0 documents | Breaking it gets the device rejected at submission |
| **spec** | The Jukebox scripting specification says so | The tools or Reason reject or misbehave |
| **guide** | An SDK guideline or recommendation | Expected at review, not mechanically enforced |
| **obs** | Observed behaviour of RE2DRender, Recon or Reason | Ground truth until the SDK says otherwise |
| **house** | A brand rule from a profile (`rebench.profile.json`) | Configurable per project |

A hard-won lesson: **RE2DRender, the SDK's graphics compiler, enforces almost none of the GUI
requirements.** It happily builds art that submission review will reject. "It built" is not
evidence that a rule holds. Run `rebench check` instead.

---

## 1. The two tools

| | RE Bench (`rebench/`) | RE-Blend (`reblend/`) |
| --- | --- | --- |
| What it is | A layout and rules bench: a CLI, a browser canvas and an MCP server | A Blender 4.2+ extension that renders the sprite sheets |
| When to use it | **Before Blender**: decide sizes and positions, check the rules, start new devices | **During art**: model controls, rig their states, render sheets, write positions back |
| Needs | Python 3.11+, `lupa`; Playwright and Chromium only for PNG screenshots | Blender 4.2 LTS+ |
| Writes | `device_2D.lua` (positions, frames), `hdgui_2D.lua`, `motherboard_def.lua` and `texts.lua` (only when adding a control), `GUI2D/rebench.json`, grey-box PNGs | `GUI2D/*.png`, `device_2D.lua` (positions, frames only) |
| Agent interface | CLI with `--json`, MCP server (`rebench mcp`) | The N-panel in Blender; the pure-Python layer `reblend.project` |

They never talk to each other directly. **They meet in the project's files**: the Lua files hold
positions, and the sidecar `GUI2D/rebench.json` holds what Lua cannot (frame sizes, blockout
shapes, label text and size, which PNGs are placeholders). Both share one rule engine, so a rule
is never checked in one tool and missed in the other.

## 2. The workflow, start to finish

```sh
pip install -e ".[dev,screenshots]"                # once; or: pip install lupa pytest playwright

# 1. Start a device (or skip to step 2 on an existing project)
python3 -m rebench new ../MyFx --name "Grit Distortion" --manufacturer "Terp Audio" [--height 2]

# 2. Lay it out. Each batch of ops is all-or-nothing and reports what it changed.
python3 -m rebench apply ../MyFx ops.json --dry-run --json   # see the consequences
python3 -m rebench apply ../MyFx ops.json --json             # then write
python3 -m rebench render ../MyFx --panel front --scale 0.5 --out front.png   # look at it

# 3. Check every rule. Exit 1 on errors (or on warnings with --strict).
python3 -m rebench check ../MyFx --json

# 4. Placeholder art for anything not yet rendered, so the device builds.
python3 -m rebench greybox ../MyFx [--sdk <SDK root>]

# 5. Build and open in Recon (on a machine with the SDK; see the project's CMakeLists.txt).
# 6. In Blender: RE-Blend > Import RE Project. Sized guide boxes and blockout meshes appear.
#    Model, rig, Render All Sheets, Validate, then Write Layout to Lua for any moves.
# 7. Rebuild, open in Recon, compare with the bench screenshots.
```

A person may prefer the canvas: `python3 -m rebench serve ../MyFx` opens it on
`http://127.0.0.1:8765/`. Edits there go through the same operations and are only written when
they press **Write to Lua**.

Loop in steps 2 and 3 until `check` is clean, then move to art. Changing a frame size or a
position later is fine. RE-Blend keeps the registration of rendered sheets, and greybox redraws
only its own placeholders.

## 3. Anatomy of an RE project

```
info.lua                      device identity and type
motherboard_def.lua           properties, sockets, routing: the device's contract
realtime_controller.lua       creates the C++ instance; which property changes reach the C++
display.lua                   optional: Lua drawing for custom displays
Resources/English/texts.lua   every user-visible string, by key (English is required)
GUI2D/device_2D.lua           where each sprite sits (hi-res pixels) and its frame count
GUI2D/hdgui_2D.lua            which widget each node is and which property it binds
GUI2D/*.png                   the sprite sheets (hi-res only)
GUI2D/rebench.json            RE Bench's sidecar (not read by the SDK)
src/cpp/                      the DSP: JukeboxExports.cpp plus your device code
```

**The chain every control follows** (all links must agree, and RE2DRender checks only some):

```
motherboard_def.lua property  ← bound by →  hdgui_2D.lua widget  ← names →  device_2D.lua node  ← names →  GUI2D/<path>.png
/custom_properties/drive         jbox.analog_knob{value=...}         knob_drive = { offset, {path, frames} }   knob_drive.png
```

`device_2D.lua` shape, as RE Bench and the templates write it:

```lua
format_version = "2.0"
front = {
  Panel_Front = {{ path = "Panel_Front" }},          -- the backdrop node
  {                                                  -- an unnamed group: draw order is list order
    knob_drive = { offset = { 1300, 110 }, { path = "knob_drive", frames = 61 } },
  },
}
-- back, folded_front, folded_back likewise; all four are required (obs: RE2DRender aborts)
folded_back = { Panel_Folded_Back = {{ path = "Panel_Folded_Back" }},
                CableOrigin = { offset = { 260, 75 } }, { ... } }   -- a point, no image
```

- `offset` is the **top-left** of the frame, in hi-res panel pixels, relative to the parent.
  Nested groups add their offsets.
- Named entries in a Lua table have no order. That is why nodes go in unnamed groups, where the
  later entry draws on top. RE Bench appends new controls to the **last** group.

`hdgui_2D.lua` shape:

```lua
format_version = "2.0"
front = jbox.panel {
  graphics = { node = "Panel_Front" },
  widgets = {
    jbox.analog_knob { graphics = { node = "knob_drive" }, value = "/custom_properties/drive" },
  },
}
folded_back = jbox.panel { graphics = { node = "Panel_Folded_Back" },
                           cable_origin = { node = "CableOrigin" }, widgets = { ... } }
```

## 4. RE Bench reference

### Commands

| Command | What it does | Exit status |
| --- | --- | --- |
| `rebench check <root> [--json] [--strict]` | Every rule in the catalogue | 0 clean, 1 errors (or warnings with `--strict`), 2 unreadable |
| `rebench rules` | The catalogue: code, source tag, title | 0 |
| `rebench apply <root> <ops.json or -> [--dry-run] [--json]` | Apply a batch of edit operations | 0, 2 unreadable, **3 refused (nothing written)** |
| `rebench render <root> --panel P --out F.png [--scale 0.5] [--frame N] [--ops ops.json] [--no-overlays]` | Screenshot a panel (`.svg` output needs no browser) | 0, 2, 3 |
| `rebench new <dir> --name N --manufacturer M [--height U] [--product-id ID] [--sdk S]` | New device from the effect template | 0, 2 |
| `rebench greybox <root> [--sdk S] [--dry-run] [--json]` | Placeholder sheets for every node without art | 0, 2 |
| `rebench serve <root> [--port 8765]` | The browser canvas | runs until stopped |
| `rebench mcp [--project <root>]` | MCP server on stdio | runs until stdin closes |

The project root is the folder that holds `GUI2D/`. Panels are `front`, `back`, `folded_front`,
and `folded_back`. All coordinates are **hi-res panel pixels**, with the origin at the top left
and y pointing down.

### Edit operations (`apply`, `layout_dry_run`, `layout_apply`, the canvas)

A batch is a JSON list. It applies whole or not at all. Ops run in order, so a later op can
move a control an earlier op added.

```json
[
  {"op": "add_control", "panel": "front", "node": "knob_drive", "widget": "analog_knob",
   "x": 1300, "y": 110, "title": "Drive"},
  {"op": "add_control", "panel": "front", "node": "sel_mode", "widget": "sequence_fader",
   "x": 2300, "y": 50, "steps": 4},
  {"op": "move",  "panel": "front", "node": "knob_drive", "x": 1310, "y": 110},
  {"op": "nudge", "panel": "front", "node": "knob_drive", "dx": 5, "dy": 0},
  {"op": "set_frames", "panel": "front", "node": "knob_drive", "frames": 61},
  {"op": "set_frame_size", "sprite": "knob_drive", "w": 120, "h": 120},
  {"op": "set_shape", "sprite": "knob_drive", "shape": {"type": "knob", "diameter_px": 100}},
  {"op": "label", "id": "lbl-drive", "panel": "front", "text": "DRIVE", "x": 1370, "y": 260,
   "size_px": 48, "functional": true, "node": "knob_drive"},
  {"op": "remove_label", "id": "lbl-drive"}
]
```

- **`add_control`** writes four files in one go: the node, the widget, a property stub in
  `document_owner` (with the lowest free `property_tag`), and the property's English name.
  - `widget` can be `analog_knob` (a 0–1 number, 61 frames), `toggle_button` (a boolean, 2
    frames), `momentary_button` (a boolean with `persistence = "none"`, 2 frames) or
    `sequence_fader` (a number with `steps`, one frame per step, labelled 1…N until you rename
    them).
  - `property` names the property. By default it is the node name without a `knob_`, `btn_`,
    `sel_`, `fader_` or `led_` prefix.
  - When the property already exists, nothing is stubbed and any widget type may bind it.
  - `w` and `h` set the frame size; by default it comes from the widget type.
  - The op refuses rather than guesses. It refuses a duplicate node, a name that is not a Lua
    identifier, a property name over 34 characters, and a widget it cannot stub (meters,
    displays: declare their property by hand first). It also refuses when an anchor in a file is
    ambiguous.
  - The device's C++ still has to read the new property. That part is yours.
- **`move`** takes the absolute top-left position. **`nudge`** moves relative to the current
  position. Both patch only the `offset` numbers, and comments survive.
- **Labels** live only in the sidecar. They are panel text (silk) with a size in hi-res pixels,
  so the text-size rule can run before any art exists. Set `functional: false` for decorative
  text such as logos.

Every result has the same shape:

```json
{"written": false, "applied": ["add_control front/knob_drive (...)"],
 "errors": 0, "warnings": 1,
 "added":    [{"severity": "warning", "code": "rack-ear", "...": "..."}],
 "resolved": [],
 "findings": ["...every current finding..."]}
```

Use `added` and `resolved` to judge each change. A refused batch returns
`{"refused": true, "reasons": [...]}` and writes nothing.

### Findings

```json
{"severity": "error", "code": "fold-arrow", "message": "...", "subject": "DeviceName_Folded",
 "panel": "folded_front", "source": "obs", "rule": "Fold-arrow keep-out", "ref": "..."}
```

`subject` is a node, sprite path or label id. `source` is the tag from the table at the top.
`python3 -m rebench rules` lists every code. Codes marked "Blender scene only" (state tables,
rotors, shadow travel) are checked by RE-Blend's Validate, not by the bench.

### MCP tools (`python3 -m rebench mcp --project <root>`)

| Tool | Arguments | Returns |
| --- | --- | --- |
| `project_load` | `project?` | Panels with size and every node (`x`, `y`, `w`, `h`, `frames`, `kind`), labels, sidecar sprites |
| `rules_check` | `project?` | Counts and findings |
| `layout_dry_run` | `project?`, `ops` | The result above, `written: false` |
| `layout_apply` | `project?`, `ops` | The result above |
| `view_render` | `project?`, `panel`, `scale=0.5`, `frame=0`, `overlays=true`, `ops?`, `out?` | **The PNG as an image**, plus the panel's findings |
| `greybox_build` | `project?`, `sdk?`, `dry_run?` | What was drawn, copied and kept |
| `project_new` | `target`, `name`, `manufacturer`, `height_ru?`, `product_id?`, `sdk?` | The new project's path |

Client config: `{"command": "python3", "args": ["-m", "rebench", "mcp", "--project", "/path/to/device"]}`,
with the RE-Blend checkout as the working directory or on `PYTHONPATH`.

A good loop for an agent is `project_load`, then `layout_dry_run` and `view_render` with the
same `ops`. Look at the image and the `added` findings, adjust, and finish with `layout_apply`.

### The sidecar, `GUI2D/rebench.json`

```json
{
  "version": 1,
  "sprites": {"knob_drive": {"frame_w": 120, "frame_h": 120,
                             "shape": {"type": "knob", "diameter_px": 100}}},
  "labels":  [{"id": "lbl-drive", "panel": "front", "text": "DRIVE", "x": 1370, "y": 260,
               "size_px": 48, "functional": true, "node": "knob_drive"}],
  "greybox": {"knob_drive": "<sha256 of the placeholder PNG>"}
}
```

- A sidecar frame size **wins over the PNG on disk**, because the PNG only says what the last
  render produced. RE-Blend's import reads it too.
- Shape types are `knob`, `button`, `fader`, and anything else, which becomes a box. They drive
  the canvas blockouts, the grey-box drawings and RE-Blend's blockout meshes.
- `greybox` lists the placeholders the bench drew, with a hash of each file. A PNG is redrawn
  only while it still matches that hash, so a real render is never overwritten.
- A missing sidecar is the same as an empty one, and existing projects need no change.

### Profiles: `rebench.profile.json` beside `GUI2D/`

```json
{"ear_left_px": 160, "ear_right_px": 3610, "bypass_box_w": 400,
 "min_text_px": 43, "fold_arrow_full": [60, 35, 85, 85], "fold_arrow_folded": [60, 35, 85, 85]}
```

These override the house and observed numbers for one project (see `rebench/profile.py` for
every field).

### The effect template (`rebench new`)

The template is a 1U (or `--height` U) stereo `creative_fx` with:

- one Level knob;
- the On/Off/Bypass fader top left on the front and folded front;
- the name tape on all four panels;
- four audio jacks and the placeholder on the back, and the cable origin on the folded back;
- auto-routing, auto-bypass routing, a remote chart and a MIDI CC;
- a C++ gain stage that follows On/Off/Bypass, handles mono input and stays silent on silence;
- a re-cmake `CMakeLists.txt`.

It passes `rebench check` with zero findings. Before release you still need to do these:

- set `device_categories` (it ships as `"Misc"`);
- paint the routing icons on the back panel (section 7);
- write the real DSP;
- register the `product_id`.

## 5. RE-Blend reference

RE-Blend runs inside Blender; everything is in the **RE-Blend** tab of the 3D viewport's N-panel.
The full user guide is [`README.md`](README.md). The design is in
[`Blender_RE_Plugin_Design.md`](Blender_RE_Plugin_Design.md).

The flow is:

1. **Import RE Project.** This creates one collection per sprite sheet ("RE Element") with
   `re_*` properties, a registration empty at the frame centre, and guide boxes at every
   placement. With a sidecar it also uses the bench's frame sizes and, on first import, adds a
   blockout mesh (`re_blockout`) from the sidecar shape.
2. **Generate Rig.** Knobs get a rotation driver (frame 0 is the minimum, frame N−1 the
   maximum). Buttons, faders and lamps get a **State Table** compiled to constant keyframes.
   *Spread Between Extremes* spaces fader detents evenly, which the SDK requires.
3. **Render All Sheets.** It renders frames 0…N−1 through a fixed per-element camera, stitches
   the vertical strip into `GUI2D/<path>.png`, and verifies it: 8-bit RGBA, straight alpha,
   exact size, and no art outside the frame.
4. **Validate** runs the full cross-check, including the Blender-only rules.
5. **Check for Differences**, then **Apply Choices**, merges Lua edits made elsewhere (for
   example by RE Bench). **Write Layout to Lua** patches `offset` and `frames` only, after a
   confirmation.

For agents without Blender, the pure-Python layer is importable on its own:

```python
from reblend.project.link import load_project          # parse a project into ElementSpecs
from reblend.project import validation                  # validate_link(link, elements=[], scene)
from reblend.project.lua_writer import patch_device_2d  # anchored offset/frames edits
from reblend.model import kinds                          # widget -> kind, frame-count rules
```

RE-Blend never writes `motherboard_def.lua`, `realtime_controller.lua`, `display.lua` or C++.
It installs SDK-supplied art from your SDK (**Install SDK Parts**) rather than rendering it.

## 6. SDK rules: the device contract (Lua)

All contract files are Lua run in a sandbox: global tables are built once at load and then
frozen (section 6.8). Every file starts with `format_version`: `"1.0"` for `info.lua`,
`realtime_controller.lua` and `texts.lua`; `"3.0"` for `motherboard_def.lua`; `"2.0"` for
`device_2D.lua` and `hdgui_2D.lua`. (spec)

### 6.1 `info.lua`

| Field | Rule |
| --- | --- |
| `product_id` | Reverse-domain, letters, digits, `.` and `_` only. It cannot change once uploaded, and its last part names the built library. (spec) |
| `manufacturer` | Shown in Reason's device palette. (spec) |
| `long_name` / `medium_name` / `short_name` | At most 40 / 20 / 10 characters. The long name is "Product Description" with every word capitalised (for example "Grit Distortion"), and no manufacturer. (spec, req at review) |
| `version_number` | `N.N.NdN` (debug), `N.N.NbN` (beta) or `N.N.NfN` (release candidate), with no leading zeros. Bump it to make Recon drop cached graphics. (spec, obs) |
| `device_type` | `"instrument"`, `"creative_fx"`, `"studio_fx"`, `"helper"` or `"note_player"`. Effects automatically get `/custom_properties/builtin_onoffbypass`. (spec) |
| `device_categories` | One or more of: Amps, Arpeggio, Audio Meter, Audio Mixer, Bass, Chords, CV Meter, CV Processor, CV Source, Delay, Dist, Drums, Dynamics, EQ, Filter, Guitar, Keys, Loops, Misc, Modulation, Note FX, Orchestral, Pitch, Reverb, Samples, Sequencer, Stereo, Synth, Vocoder. Each fits certain device types, and the choice is checked at acceptance. (spec) |
| `device_height_ru` | Must match the panel art (1U = 345 px). (spec) |
| `automation_highlight_color` | Must be `{ r = 60, g = 255, b = 2 }` unless Reason Studios agrees otherwise. (spec) |
| `supports_patches` | If true, the device needs a patch browser group and a `default_patch`. (spec) |
| `accepts_notes`, `auto_create_track`, `auto_create_note_lane`, `supports_performance_automation` | Booleans; set all four explicitly, as the template does. Typically only instruments create a track. (spec, acceptance) |

### 6.2 `motherboard_def.lua`: properties

```lua
custom_properties = jbox.property_set {
  gui_owner      = { properties = { ... } },   -- GUI-only state; not visible to the C++
  document_owner = { properties = { ... } },   -- the knobs and buttons: saved, automatable
  rtc_owner      = { properties = { instance = jbox.native_object {} } },
  rt_owner       = { properties = { ... } },   -- written by the C++: meters, lamps
}
```

- **Owner scopes.** Only the owner may write a property. GUI widgets write `document_owner` and
  `gui_owner`. The realtime controller writes `rtc_owner`. The C++ writes `rt_owner`. Only
  `document_owner` and `gui_owner` can be saved. (spec)
- **Names.** ASCII only, at most **34 characters**. The path is `/custom_properties/<name>`.
  (spec)
- **`jbox.number`.** A continuous number is 0.0–1.0. With `steps = N` (2 to 4,194,304) it holds
  the integers 0…N−1. `default` must fit. (spec)
- **`jbox.boolean`**, **`jbox.string`**, **`jbox.native_object`** (the C++ instance), plus
  sample, BLOB, DSP buffer and performance properties for specialised uses. (spec)
- **`ui_name`** is required and must be `jbox.ui_text("key")`, with the key in `texts.lua`.
  (spec)
- **`ui_type`** is one of:
  - `jbox.ui_selector{ jbox.ui_text(..), ... }`, stepped only, with exactly `steps` entries;
  - `jbox.ui_percent{ min, max, decimals }`;
  - `jbox.ui_linear{ min, max, units = {...} }`;
  - `jbox.ui_nonlinear{ data_to_gui, gui_to_data, units }`, which must be a bijection.
  - Built-in strings: `jbox.UI_TEXT_ON`, `jbox.UI_TEXT_OFF`, `jbox.UI_TEMPLATE_HZ`,
    `jbox.UI_TEMPLATE_KHZ`. A `^0` in a template is replaced by the value. (spec)
- **`persistence`** is `"patch"` (song and patch), `"song"` or `"none"`, and is allowed on
  `document_owner` and `gui_owner` only. Use `"song"` for an effect's dry/wet, and `"none"` for
  momentary controls. (spec, guide)
- **`property_tag`** is an optional integer of 0 or more, unique within the set. It is the C++
  shortcut to the property. (spec)
- **Built-in host properties.**
  - `builtin_onoffbypass`: effects only, 3 steps. 0 = Off, 1 = On, 2 = Bypass. Every effect
    must show it. (spec, req)
  - `/transport/request_reset_audio`, a counter (section 8).
  - `/environment/system_sample_rate`.

### 6.3 Sockets and routing

- **Sockets.** Declare them as
  `audio_inputs = { inLeft = jbox.audio_input{ ui_name = ... } }`, with `audio_outputs`,
  `cv_inputs` and `cv_outputs` likewise. There are at most 100 of each. CV values range from
  −10000 to 10000. (spec)
- **Routing.**
  - **Effects**: declare `jbox.add_stereo_effect_routing_hint{ left_input, right_input,
    left_output, right_output, type }`. `type` is `"true_stereo"` (channels processed
    independently), `"mixing_stereo"` (both channels feed the effect) or `"spreading"` (mono in,
    stereo out).
  - **Instruments**: use the instrument routing hints instead.
  - Add `jbox.add_stereo_audio_routing_pair` for each L/R pair and
    `jbox.add_stereo_audio_routing_target` for auto-cabling. (spec)
- **`jbox.set_effect_auto_bypass_routing{ {in_L, out_L}, {in_R, out_R} }`** defines what Reason
  connects when the user has no licence. Effects need it. (spec)
- **Mono in gives mono out** for an effect unless it truly creates stereo. (req, acceptance)

### 6.4 Remote, MIDI, UI groups

- `remote_implementation_chart` maps a property path to
  `{ internal_name, short_ui_name, shortest_ui_name }`. Use a descriptive English
  `internal_name`. Every parameter should be remotable, except those inside custom displays.
  (spec, req at acceptance)
- `midi_implementation_chart = { midi_cc_chart = { [12] = "/custom_properties/x" } }`. A
  property with a CC becomes automatable in Reason. Don't make meter modes, run buttons or
  inert parameters automatable. (spec, guide)
- `ui_groups` group parameters for the Combinator programmer. The flat list holds at most 20,
  and no group should have a single entry. Groups only show from 10 automatable properties.
  (spec, req at acceptance)

### 6.5 Compatibility after release (spec)

Once a version has shipped:

- never remove a saved property;
- never lower a property's persistence;
- never change a property's type;
- never lower `steps`;
- never change a MIDI CC or Remote mapping;
- never remove or reorder sockets.

New properties must not change the sound at their defaults.

### 6.6 `realtime_controller.lua`

```lua
rtc_bindings = { { source = "/environment/system_sample_rate", dest = "/global_rtc/on_sr" } }
global_rtc = { on_sr = function(path, value)
  jbox.store_property("/custom_properties/instance",
                      jbox.make_native_object_rw("Instance", { value }))
end }
rt_input_setup = { notify = { "/transport/request_reset_audio" } }   -- which diffs the C++ sees
sample_rate_setup = { native = { 22050, 44100, 48000, 88200, 96000, 192000 } }
```

- All six sample rates must be listed, each either `native` or `converted`. (spec)
- A bound function may read only its bound properties and write only `rtc_owner` properties.
  Each property can be bound to only one function. (spec)
- Properties listed in `rt_input_setup.notify` arrive as diffs in the C++ (an initial diff is
  sent at creation). The C++ can always *read* any property; `notify` is about change events.
  (spec)

### 6.7 `Resources/English/texts.lua`

`texts = { key = "Text", ... }`. Every `jbox.ui_text("key")` must resolve, and English is
required. German, French and Japanese are optional. (spec)

### 6.8 The Lua sandbox (spec)

After the first load nothing global may change. In particular:

- don't assign to globals or global tables from inside a function;
- don't modify upvalues;
- don't redefine `jbox.*` functions.

`#` and `table.getn` return 0 for global arrays, so iterate with `pairs`.

## 7. SDK rules: the panels (GUI)

The full transcription, with the per-widget frame-count table, is
[`docs/sdk-gui-reference.md`](docs/sdk-gui-reference.md). Read it before touching widgets. The
essentials:

**Panel geometry.**

- Every panel is 3770 px wide. (req)
- 1U = 345 px, at most 9U, and front and back have the same height. (req)
- Folded panels are 150 px tall. (req)
- All four panels must exist. Players have only front and back. (req)
- The hi-res set is the only one you make; RE2DRender derives the 0.5× set. (req, spec)

**Images.**

- PNG; sprite sheets are **vertical strips**, frame 0 on top, with height = frame height ×
  frames. (req)
- Use 8-bit RGBA with straight alpha. (RE-Blend rule; Blender works premultiplied)
- **Frame width and height must be multiples of 5**, or RE2DRender quietly writes a
  `-reframed` copy and registration breaks. (obs)
- Every frame of a control must have its centre at the same pixel. (spec, by RE-Blend's
  construction)

**Where things may go.**

- 25 px left and right margins with no interactive widget. (req) Keep 25 px top and bottom too.
  (guide)
- Widgets lie entirely inside their panel. They may not overlap, except `static_decoration` or
  widgets whose `visibility_switch` values exclude each other. (req)
- The **fold/unfold arrow** sits at about x 80–125, y 55–105 on every panel. Keep the rectangle
  (60, 35, 85, 85) clear. (req that nothing overlaps it; position obs, see
  [`docs/findings-fold-arrow.md`](docs/findings-fold-arrow.md))
- Rack ears: only ear art at x < 160 or x > 3610 on the front. (house, configurable) Screws
  should line up with Reason's rack holes. (guide)
- Effects need the On/Off/Bypass control in the **top left** of the front *and* folded front.
  (req) The house rule reads "top left" as the control's centre within the leftmost 400 px.
- The device name tape (`jbox.device_name`) appears on **all four** panels. (req)
- The back panel has a `jbox.placeholder` (300 × 100, stock art) with nothing drawn under it.
  (req)
- The folded back has `cable_origin`, and only the folded back. (req, spec)
- Sockets go on the back panel only. Custom displays go on the front and folded front only.
  (spec)
- **Routing icons.** Effects show at least two `Routing_Icon_*` icons on the back panel: one for
  mono input, one for stereo input, and every one that can apply. This is painted panel art; the
  bench cannot check it yet. (req)

**Text.** Functional text should be at least **43 px** (hi-res), about 21 px in the 0.5× set.
(guide) The rule is `text-size`, with the floor configurable. Decorative text need not be
readable, but it must be obviously decorative. (acceptance)

**Widgets and frame counts.**

| Widget | Frames |
| --- | --- |
| `analog_knob` | any number (61 by convention) |
| `toggle_button` | 2 or 4 |
| `momentary_button`, `step_button`, `radio_button` | exactly 2 |
| `up_down_button` | exactly 3 |
| `sequence_fader` | one per handle position (= `steps`); frame 0 has the handle at the **bottom** (left when horizontal), travel is even, and `inverted = true` flips only the value mapping |
| `sequence_meter` | one per value; binds an **`rt_owner`** property |
| `static_decoration`, `custom_display` | exactly 1 |

(spec) A radio group over N values is N two-frame `radio_button` widgets, each with its own
`index`. It is never one N-frame sheet.

**Common widget attributes.**

- `value`, or `value_switch` with `values`;
- `visibility_switch` with `visibility_values` (unfolded panels only);
- `show_remote_box`, `show_automation_rect`, `tooltip_position`, `tooltip_template`;
- `graphics = { node, hit_boundaries = { left, top, right, bottom } }`. `hit_boundaries` are
  inward offsets, and they also set Reason's automation rectangle. Keep hit boxes reasonably
  sized. (spec, acceptance)

**Art you do not make.**

- Sockets, the name tape, the placeholder, CV trim knobs and the patch/sample browse groups use
  stock SDK images. The stock sizes are:

  | Part | Size |
  | --- | --- |
  | Audio jack | 95 × 105, 3 frames |
  | CV jack | 75 × 85, 3 frames |
  | Tape | 400 × 65 |
  | Placeholder | 300 × 100 |

- `value_display`, `popup_button`, `patch_name` and `sample_drop_zone` are bounds only: Reason
  draws their contents.

(req, spec)

**Lighting** (guide), for 3D art that sits right in the rack:

- orthographic, straight on;
- main light from (−0.08, 0.38, 1.0);
- three environment lights (see the reference);
- no shadows or glow across panel edges. (acceptance, recommended)

## 8. SDK rules: the C++ realtime code

You need `Jukebox.h` from the SDK to compile (section 9). The rules themselves:

- **Two exports.**
  - `JBox_Export_CreateNativeObject(op, params, count)`: allocate your instance here. It may
    not touch the motherboard, must return non-null, and the block it returns may not point
    outside itself.
  - `JBox_Export_RenderRealtime(instance, diffs, count)`: called once per batch. (spec)
- **No heap allocation in `RenderRealtime`.** That includes `std::vector` growth and
  `std::string`. It must also return fast; a stall "blows the fuse" and the device dies for the
  session. (spec)
- **Batches are 64 frames.** Read inputs with `JBox_GetDSPBufferData` and write outputs with
  `JBox_SetDSPBufferData`. (spec)
- **Silence.**
  - An input whose samples are all at or below `kJBox_SilentThreshold` is silent.
  - Stop rendering when the inputs fall silent (after any tail).
  - An output you don't touch in a batch counts as silent; a buffer of zeros does not.
  - If you write an output, write every frame of it, and never NaN or infinity.

  (spec, req)
- **On/Off/Bypass.** Read `builtin_onoffbypass` every batch (`kJBox_CustomPropertiesOnOffBypass`).
  - Off: write nothing.
  - Bypass: pass the input through untouched.
  - On: process.

  In a Combinator, Bypass All FX sets Bypass. (spec, acceptance)
- **Audio reset.** When `/transport/request_reset_audio` changes (subscribe to it in
  `rt_input_setup`), clear tails and voices as if no sound had ever arrived. Reason does this
  before exporting audio. (spec, acceptance)
- **Mono in.** With only the left input connected, produce mono output (the left output only)
  unless the effect spreads. (acceptance)
- **Latency.** Report processing latency per output through its `dsp_latency` property. (spec)
- **Floating point.** Single-precision floats have about 7 significant digits, so never compare
  floats for equality. Jukebox fixes the floating-point mode. (spec)
- **Never invent `JBox_` calls.** If a call is not in `Jukebox.h`, it does not exist.

## 9. What still needs the SDK

| Task | Why |
| --- | --- |
| Compiling and linking the C++ | `Jukebox.h`, `JukeboxTypes.h` and the toolchain are Reason Studios' files and cannot be in this GPL repository |
| Building the `.u45`, RE2DRender, RE2DPreview, Recon | SDK tools; build with pongasoft re-cmake on a machine where the SDK is installed |
| Stock art (jacks, tape, placeholder, routing icons) | Copied from `RE2DRender/Images` by `rebench greybox --sdk` or RE-Blend's *Install SDK Parts*. Without the SDK the bench draws same-size stand-ins |
| Rare widget attributes and the display.lua drawing API | Only the essentials are summarised here; the scripting specification is authoritative |
| Final acceptance | Reason Studios' review against the acceptance checklist (`SDK_v4.6.0/Documentation/acceptance_testing_checklist.txt`, kept in this repo) |

When the SDK is present, its HTML documents in `SDK_v4.6.0/API/` are the authority. Record new
facts in [`docs/sdk-gui-reference.md`](docs/sdk-gui-reference.md) in your own words; never commit
the SDK's files.

## 10. Ground rules for agents

- **Lua files are the source of truth for positions.** Change them through `rebench apply` (or
  RE-Blend's patch mode), never by rewriting a file. Hand-written comments in them matter.
- **Dry-run first.** Check `added` before writing. A batch that adds an error is a step
  backwards unless it is the first step of a planned sequence.
- **Look at the result.** `view_render` at `scale: 0.5` shows roughly what a user sees in the rack.
- **Keep spec and observation apart.** If you learn something from what Recon or RE2DRender
  does, record it as observed (`obs`), with the tool version, not as a requirement.
- **Never generate the 0.5× art.** Never hand-draw SDK-supplied parts.
- **Never commit the SDK** or any file copied from it.
- **Do not write the owner's personal name** in commits, PRs, issues or docs in this public
  repository; use the GitHub login or "the owner".
- Branches: `main` is what the pilot device (Skou) uses and takes no feature work until Skou
  ships; work goes to `develop` (see [`CLAUDE.md`](CLAUDE.md)).
- Tests: `python3 -m pytest` needs neither Blender nor the SDK. Set `REBENCH_JUKEBOX_DIR` to
  the SDK's `API` folder to also compile the template's C++.
