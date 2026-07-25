"""The validation report (§6.3): every row of the cross-check table.

Strategy: build a *correct* project on disk (fixture Lua + generated sheets
at the right sizes), assert the report is completely clean, then break one
thing per test and assert exactly that finding appears. A validator that
cannot produce a clean pass on a correct project is as broken as one that
misses an error.
"""

import shutil
import struct
import zlib

import pytest

from reblend.model import kinds, state_tables
from reblend.project import validation
from reblend.project.link import load_project
from reblend.project.png_meta import write_rgba_png
from reblend.project.validation import SceneInfo, validate_link

#: Per-frame pixel sizes for every sheet in the silence_detector fixture —
#: all multiples of 5 (M0 finding 6). Backdrops define a 2U device.
SHEET_SIZES = {
    "Panel_Front": (3770, 690),
    "Panel_Back": (3770, 690),
    "Panel_Folded_Front": (3770, 150),
    "Panel_Folded_Back": (3770, 150),
    "Knob_65x65_61frames": (65, 65),
    "Button_50x35_2frames": (50, 35),
    "Lamp_15x15_2frames": (15, 15),
    "Tape_Horizontal_1frames": (390, 40),
    "Fader_25x60_3frames": (25, 60),
    "SharedAudioJack": (105, 105),
    "Logo_120x40_1frames": (120, 40),
    "Placeholder": (300, 100),
}


@pytest.fixture
def project_dir(silence_detector, tmp_path):
    """Fixture project with every sheet rendered at correct dimensions."""
    root = tmp_path / "device"
    shutil.copytree(silence_detector, root)
    link = load_project(root)
    for spec in link.specs:
        w, h = SHEET_SIZES[spec.path]
        write_rgba_png(root / "GUI2D" / f"{spec.path}.png", w, h * spec.frames,
                       bytes(w * h * spec.frames * 4))
    return root


def rigged_states(element):
    """A minimal *finished* state table for a state-rigged element.

    A named-but-empty table is what import seeds; it is not yet a rig, and the
    report says so. "Correct project" therefore means the multi-state elements
    carry actions that actually differ per frame — with, for a fader, the even
    travel its widget contract requires.
    """
    table = state_tables.default_state_table(element.kind, element.frames)
    if table is None:
        return ""
    if element.kind == kinds.FADER_HANDLE:
        table.add_actions([state_tables.location(f"{element.path}_handle", 2, 0.0)])
        table.spread_channel(table.channels()[0], 0.0, 0.2)
    else:
        table.add_actions([state_tables.emission_strength(f"{element.path}_mat", 0.0)])
        table.set_value(table.frames - 1, table.channels()[0], 5.0)
    return table.to_json()


def make_scene(root):
    """(link, elements) as the Blender side would hand them to validation."""
    link = load_project(root)
    elements = [spec.to_element_data() for spec in link.specs]
    for element in elements:
        element.states = rigged_states(element)
    return link, elements


def codes(report):
    return [f.code for f in report.findings]


# ---------------------------------------------------------------------------


def test_correct_project_is_completely_clean(project_dir):
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements, SceneInfo(view_transform="Standard"))
    assert report.findings == []
    assert report.ok


def test_missing_art_is_an_error(project_dir):
    link, elements = make_scene(project_dir)
    elements = [e for e in elements if e.path != "Knob_65x65_61frames"]
    report = validate_link(link, elements)
    missing = [f for f in report.errors if f.code == "missing-art"]
    assert len(missing) == 1
    assert missing[0].subject == "Knob_65x65_61frames"
    assert "knob_threshold" in missing[0].message


def test_orphan_element_is_a_warning(project_dir):
    link, elements = make_scene(project_dir)
    elements.append(validation.schema.ElementData(node="ghost", path="Unused_Thing"))
    report = validate_link(link, elements)
    assert "orphan-element" in codes(report)
    assert report.ok  # warning, not error


def test_missing_state_table_is_a_warning(project_dir):
    link, elements = make_scene(project_dir)
    fader = next(e for e in elements if e.kind == kinds.FADER_HANDLE)
    fader.states = ""
    report = validate_link(link, elements)
    states = [f for f in report.warnings if f.code == "states"]
    assert len(states) == 1
    assert "no state table" in states[0].message
    assert report.ok  # warning, not error


def test_state_table_without_actions_is_a_warning(project_dir):
    link, elements = make_scene(project_dir)
    fader = next(e for e in elements if e.kind == kinds.FADER_HANDLE)
    fader.states = state_tables.default_state_table(fader.kind, fader.frames).to_json()
    report = validate_link(link, elements)
    assert any(f.code == "states" and "no actions" in f.message
               for f in report.warnings)


def test_state_count_mismatch_is_an_error(project_dir):
    link, elements = make_scene(project_dir)
    fader = next(e for e in elements if e.kind == kinds.FADER_HANDLE)
    table = state_tables.StateTable.from_json(fader.states)
    table.states = table.states[:-1]
    fader.states = table.to_json()
    report = validate_link(link, elements)
    assert any(f.code == "states" and f.severity == "error" for f in report.findings)


def test_uneven_fader_travel_is_a_warning(project_dir):
    link, elements = make_scene(project_dir)
    fader = next(e for e in elements if e.kind == kinds.FADER_HANDLE)
    table = state_tables.StateTable.from_json(fader.states)
    table.set_value(1, table.channels()[0], 0.19)  # nearly at the top, not mid
    fader.states = table.to_json()
    report = validate_link(link, elements)
    travel = [f for f in report.warnings if f.code == "travel"]
    assert len(travel) == 1
    assert "evenly spaced" in travel[0].message
    assert report.ok


def test_uneven_travel_is_only_checked_on_faders(project_dir):
    link, elements = make_scene(project_dir)
    lamp = next(e for e in elements if e.kind == kinds.LAMP)
    table = state_tables.StateTable.from_json(lamp.states)
    table.add_actions([state_tables.location(f"{lamp.path}_obj", 2, 0.0)])
    table.set_value(1, table.channels()[-1], 3.7)
    lamp.states = table.to_json()
    assert "travel" not in codes(validate_link(link, elements))


def test_corrupt_state_table_is_an_error(project_dir):
    link, elements = make_scene(project_dir)
    next(e for e in elements if e.kind == kinds.FADER_HANDLE).states = "{not json"
    report = validate_link(link, elements)
    assert any(f.code == "states" and f.severity == "error" for f in report.findings)


def test_frame_count_mismatch_is_an_error(project_dir):
    link, elements = make_scene(project_dir)
    next(e for e in elements if e.path == "Knob_65x65_61frames").frames = 31
    report = validate_link(link, elements)
    assert any(f.code == "frame-count" and f.severity == "error" for f in report.findings)


def test_widget_pointing_at_missing_node_is_an_error(project_dir):
    hdgui = project_dir / "GUI2D" / "hdgui_2D.lua"
    hdgui.write_text(
        hdgui.read_text(encoding="utf-8").replace(
            'node = "knob_threshold"', 'node = "knob_gone"'
        ),
        encoding="utf-8",
    )
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    assert any(f.code == "widget-node" and f.subject == "knob_gone" for f in report.errors)


def test_frames_vs_steps_mismatch_is_a_warning(project_dir):
    device = project_dir / "GUI2D" / "device_2D.lua"
    device.write_text(
        device.read_text(encoding="utf-8").replace(
            '{ path = "Fader_25x60_3frames", frames = 3 }',
            '{ path = "Fader_25x60_3frames", frames = 4 }',
        ),
        encoding="utf-8",
    )
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    steps = [f for f in report.warnings if f.code == "steps"]
    assert steps and "builtin_onoffbypass" in steps[0].message


def test_png_dimension_mismatch_is_an_error(project_dir):
    write_rgba_png(project_dir / "GUI2D" / "Knob_65x65_61frames.png",
                   65, 65 * 61 - 65, bytes(65 * 65 * 60 * 4))  # one frame short
    link, elements = make_scene(project_dir)
    # keep the declared size: the probe won't fill it from the short sheet
    knob = next(e for e in elements if e.path == "Knob_65x65_61frames")
    knob.frame_w, knob.frame_h = 65, 65
    report = validate_link(link, elements)
    assert any(f.code == "png-dims" and f.severity == "error" for f in report.findings)


def test_missing_png_is_a_warning_until_first_render(project_dir):
    (project_dir / "GUI2D" / "Lamp_15x15_2frames.png").unlink()
    link, elements = make_scene(project_dir)
    lamp = next(e for e in elements if e.path == "Lamp_15x15_2frames")
    lamp.frame_w, lamp.frame_h = 15, 15
    report = validate_link(link, elements)
    assert any(f.code == "png-missing" and f.severity == "warning" for f in report.findings)


def test_case_mismatch_is_an_error(project_dir):
    gui2d = project_dir / "GUI2D"
    (gui2d / "Lamp_15x15_2frames.png").rename(gui2d / "lamp_15x15_2frames.png")
    link, elements = make_scene(project_dir)
    lamp = next(e for e in elements if e.path == "Lamp_15x15_2frames")
    lamp.frame_w, lamp.frame_h = 15, 15
    report = validate_link(link, elements)
    assert any(f.code == "case" and f.severity == "error" for f in report.findings)


def test_unset_frame_size_is_a_warning(project_dir):
    (project_dir / "GUI2D" / "Lamp_15x15_2frames.png").unlink()
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    assert any(f.code == "frame-size" and f.subject == "Lamp_15x15_2frames"
               for f in report.warnings)


def test_frame_bounds_not_multiple_of_five_is_an_error(project_dir):
    link, elements = make_scene(project_dir)
    knob = next(e for e in elements if e.path == "Knob_65x65_61frames")
    knob.frame_w = knob.frame_h = 63
    report = validate_link(link, elements)
    bounds = [f for f in report.errors if f.code == "frame-bounds"]
    assert len(bounds) == 2  # width and height each flagged
    # and the sheet on disk (65 px wide) now disagrees with the declared size
    assert any(f.code == "png-dims" for f in report.errors)


def test_reframed_artifact_is_an_error(project_dir):
    write_rgba_png(project_dir / "GUI2D" / "Knob_65x65_61frames-reframed.png",
                   65, 65, bytes(65 * 65 * 4))
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    assert any(f.code == "reframed" for f in report.errors)


def test_non_standard_view_transform_is_a_warning(project_dir):
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements, SceneInfo(view_transform="AgX"))
    assert any(f.code == "view-transform" for f in report.warnings)
    assert validate_link(link, elements, SceneInfo(view_transform=None)).findings == []


def test_element_outside_panel_is_an_error(project_dir):
    device = project_dir / "GUI2D" / "device_2D.lua"
    device.write_text(
        device.read_text(encoding="utf-8").replace(
            "offset = { 1810, 145 }", "offset = { 3760, 145 }"
        ),
        encoding="utf-8",
    )
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    # "All widget boundaries must be completely inside the boundaries of
    # their corresponding panel" is a stated requirement, so this is an error.
    assert any(f.code == "bounds" and f.subject == "Button_50x35_2frames"
               for f in report.errors)


def test_overlapping_elements_are_a_warning(project_dir):
    device = project_dir / "GUI2D" / "device_2D.lua"
    device.write_text(
        device.read_text(encoding="utf-8").replace(
            "offset = { 30, 0 }", "offset = { 5, 0 }"
        ),
        encoding="utf-8",
    )
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    overlap = [f for f in report.warnings if f.code == "overlap"]
    assert overlap and "lamp_signal" in overlap[0].subject


def test_kind_mismatch_is_a_warning(project_dir):
    link, elements = make_scene(project_dir)
    next(e for e in elements if e.path == "Knob_65x65_61frames").kind = "lamp"
    report = validate_link(link, elements)
    assert any(f.code == "kind" and f.subject == "Knob_65x65_61frames"
               for f in report.warnings)


def test_non_rgba_png_is_a_warning(project_dir):
    # Hand-build a 16-bit RGB PNG header: read_png_meta only needs the IHDR.
    def chunk(ctype, payload):
        return (struct.pack(">I", len(payload)) + ctype + payload
                + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 390, 40, 16, 2, 0, 0, 0)
    blob = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")
    (project_dir / "GUI2D" / "Tape_Horizontal_1frames.png").write_bytes(blob)

    link, elements = make_scene(project_dir)
    tape = next(e for e in elements if e.path == "Tape_Horizontal_1frames")
    tape.frame_w, tape.frame_h = 390, 40
    report = validate_link(link, elements)
    assert any(f.code == "png-format" for f in report.warnings)


def test_report_severity_partition(project_dir):
    link, elements = make_scene(project_dir)
    elements = [e for e in elements if e.path != "Knob_65x65_61frames"]
    report = validate_link(link, elements, SceneInfo(view_transform="Filmic"))
    assert not report.ok
    assert {f.severity for f in report.errors} == {"error"}
    assert {f.severity for f in report.warnings} == {"warning"}
    assert len(report.errors) + len(report.warnings) == len(report.findings)


# ---------------------------------------------------------------------------
# Per-widget frame contracts (SDK 4.6.0 scripting specification)
# ---------------------------------------------------------------------------


def retarget(project_dir, old, new):
    """Rewrite a snippet in both GUI2D Lua files and re-read the project."""
    for name in ("device_2D.lua", "hdgui_2D.lua"):
        path = project_dir / "GUI2D" / name
        path.write_text(path.read_text(encoding="utf-8").replace(old, new),
                        encoding="utf-8")


@pytest.mark.parametrize(
    "widget, frames, legal",
    [
        ("toggle_button", 2, True),
        ("toggle_button", 4, True),   # off, off-held, on, on-held
        ("toggle_button", 3, False),
        ("momentary_button", 2, True),
        ("momentary_button", 3, False),
        # A step_button/radio_button animates released-vs-held and is always
        # two frames, however many steps its property has.
        ("step_button", 2, True),
        ("step_button", 6, False),
        ("radio_button", 2, True),
        ("radio_button", 8, False),
        ("up_down_button", 3, True),
        ("up_down_button", 2, False),
    ],
)
def test_widget_frame_counts(project_dir, widget, frames, legal):
    retarget(project_dir, "jbox.toggle_button{", "jbox.%s{" % widget)
    retarget(project_dir,
             '{ path = "Button_50x35_2frames", frames = 2 }',
             '{ path = "Button_50x35_2frames", frames = %d }' % frames)
    link, elements = make_scene(project_dir)
    # keep the element in step with the Lua so only the widget rule can fire
    button = next(e for e in elements if e.path == "Button_50x35_2frames")
    button.frames, button.frame_w, button.frame_h = frames, 50, 35
    report = validate_link(link, elements)
    offending = [f for f in report.errors if f.code == "widget-frames"]
    assert (not offending) is legal, [f.message for f in offending]


def test_static_decoration_may_not_be_animated(project_dir):
    retarget(project_dir,
             '{ path = "Logo_120x40_1frames" }',
             '{ path = "Logo_120x40_1frames", frames = 2 }')
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    found = [f for f in report.errors if f.code == "widget-frames"]
    assert found and "static_decoration" in found[0].message
    assert "cannot be animated" in found[0].message


def test_fader_frames_are_not_capped_by_a_fixed_count(project_dir):
    """A sequence_fader bakes its whole travel, so any frame count is legal."""
    retarget(project_dir,
             '{ path = "Fader_25x60_3frames", frames = 3 }',
             '{ path = "Fader_25x60_3frames", frames = 64 }')
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    assert not [f for f in report.errors if f.code == "widget-frames"]


# ---------------------------------------------------------------------------
# Panel-level requirements
# ---------------------------------------------------------------------------


def test_cable_origin_outside_folded_back_is_an_error(project_dir):
    hdgui = project_dir / "GUI2D" / "hdgui_2D.lua"
    hdgui.write_text(
        hdgui.read_text(encoding="utf-8").replace(
            'graphics = { node = "Panel_back_bg" },',
            'graphics = { node = "Panel_back_bg" },\n\tcable_origin = { node = "CableOrigin" },',
        ),
        encoding="utf-8",
    )
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    found = [f for f in report.errors if f.code == "cable-origin"]
    assert found and found[0].panel == "back"


def test_missing_cable_origin_is_an_error(project_dir):
    hdgui = project_dir / "GUI2D" / "hdgui_2D.lua"
    text = hdgui.read_text(encoding="utf-8")
    # drop the second (folded_back) declaration only
    head, sep, tail = text.rpartition('\tcable_origin = { node = "CableOrigin" },\n')
    hdgui.write_text(head + tail, encoding="utf-8")
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    assert any(f.code == "cable-origin" and f.panel == "folded_back"
               for f in report.errors)


def test_missing_back_panel_placeholder_is_an_error(project_dir):
    hdgui = project_dir / "GUI2D" / "hdgui_2D.lua"
    hdgui.write_text(
        hdgui.read_text(encoding="utf-8").replace(
            'jbox.placeholder{\n\t\t\tgraphics = { node = "Placeholder" },\n\t\t},', ""
        ),
        encoding="utf-8",
    )
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    assert any(f.code == "placeholder" for f in report.errors)


def test_missing_panel_is_an_error(project_dir):
    device = project_dir / "GUI2D" / "device_2D.lua"
    text = device.read_text(encoding="utf-8")
    device.write_text(text[: text.index("folded_back = {")], encoding="utf-8")
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    assert any(f.code == "panel-missing" and f.panel == "folded_back"
               for f in report.errors)


def test_folded_backdrop_must_be_150_px(project_dir):
    """Folded panels are a fixed 150 px — RE2DRender will not say so."""
    link, elements = make_scene(project_dir)
    folded = next(e for e in elements if e.path == "Panel_Folded_Front")
    folded.frame_h = 130
    report = validate_link(link, elements)
    found = [f for f in report.errors if f.code == "panel-size"]
    assert found and "150" in found[0].message


def test_device_taller_than_nine_units_is_an_error(project_dir):
    link, elements = make_scene(project_dir)
    for element in elements:
        if element.path in ("Panel_Front", "Panel_Back"):
            element.frame_h = 345 * 10
    report = validate_link(link, elements)
    assert any(f.code == "rack-height" and "9U" in f.message for f in report.errors)


def test_interactive_widget_in_the_edge_margin_is_a_warning(project_dir):
    device = project_dir / "GUI2D" / "device_2D.lua"
    device.write_text(
        device.read_text(encoding="utf-8").replace(
            "offset = { 1810, 145 }", "offset = { 10, 145 }"
        ),
        encoding="utf-8",
    )
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    assert any(f.code == "edge-margin" and f.subject == "Button_50x35_2frames"
               for f in report.warnings)


def test_static_decoration_may_overlap_other_widgets(project_dir):
    """The spec exempts static_decoration from the no-overlap rule."""
    device = project_dir / "GUI2D" / "device_2D.lua"
    device.write_text(
        device.read_text(encoding="utf-8").replace(
            "offset = { 2600, 100 }", "offset = { 950, 120 }"  # onto the knob
        ),
        encoding="utf-8",
    )
    link, elements = make_scene(project_dir)
    report = validate_link(link, elements)
    assert not [f for f in report.warnings if f.code == "overlap"]
