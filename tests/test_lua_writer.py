"""Patch-mode device_2D writing (§6.2, §10.2) against the interop fixtures.

The interop rule (§6.4) gates every write-path feature on fixture round
trips: the SDK-convention project must patch with nothing but the intended
literals changed, hostile hand formatting must either patch correctly or be
refused outright, and a patched file must re-read (through the same real-Lua
interpreter the SDK and RE Edit effectively define) to exactly the intended
tree.
"""

import shutil

import pytest

from reblend.model.schema import ElementData, Placement
from reblend.project.lua_reader import LuaConfigError, read_device_2d, read_device_2d_text
from reblend.project.lua_writer import (
    FramesEdit,
    OffsetEdit,
    PatchError,
    compute_device_edits,
    node_base_offset,
    patch_device_2d,
    patch_device_2d_file,
)

from tests.conftest import FIXTURES


@pytest.fixture
def device_source(silence_detector):
    return (silence_detector / "GUI2D" / "device_2D.lua").read_text()


@pytest.fixture
def hostile_source():
    return (FIXTURES / "patch_styles" / "device_2D.lua").read_text()


def changed_lines(before: str, after: str):
    return [
        (a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b
    ]


# ---------------------------------------------------------------------------
# the SDK-convention fixture: minimal, surgical edits
# ---------------------------------------------------------------------------


def test_offset_patch_touches_exactly_one_line(device_source):
    result = patch_device_2d(
        device_source, [OffsetEdit("front", "knob_threshold", 900, 130)]
    )
    assert changed_lines(device_source, result.source) == [
        ("\t\t\toffset = { 950, 120 },", "\t\t\toffset = { 900, 130 },")
    ]
    assert result.applied == ["front/knob_threshold: offset {950, 120} -> {900, 130}"]


def test_comments_survive_byte_for_byte(device_source):
    result = patch_device_2d(
        device_source, [OffsetEdit("front", "knob_threshold", 900, 130)]
    )
    for line in device_source.splitlines():
        if line.strip().startswith("--"):
            assert line in result.source


def test_patched_text_reparses_to_the_edit(device_source):
    result = patch_device_2d(
        device_source,
        [
            OffsetEdit("front", "knob_threshold", 900, 130),
            FramesEdit("front", "SilenceSwitch", "Button_50x35_2frames", 4),
        ],
    )
    device = read_device_2d_text(result.source)
    assert device.node("front", "knob_threshold").offset == (900, 130)
    assert device.node("front", "SilenceSwitch").frames == 4
    # everything else untouched
    assert device.node("front", "lamp_silence").offset == (30, 0)
    assert device.node("front", "knob_threshold").frames == 61


def test_nested_group_node_patches_its_relative_offset(device_source):
    result = patch_device_2d(device_source, [OffsetEdit("front", "lamp_silence", 35, 5)])
    assert "offset = { 35, 5 }" in result.source
    # the group's own offset is untouched
    assert read_device_2d_text(result.source).node("front", "lamp_group").offset == (300, 100)


def test_same_node_name_on_other_panels_is_untouched(device_source):
    result = patch_device_2d(device_source, [OffsetEdit("front", "DeviceName", 1600, 30)])
    device = read_device_2d_text(result.source)
    assert device.node("front", "DeviceName").offset == (1600, 30)
    assert device.node("back", "DeviceName").offset == (1665, 25)
    assert device.node("folded_front", "DeviceName").offset == (660, 50)


def test_noop_edits_leave_the_source_identical(device_source):
    result = patch_device_2d(
        device_source,
        [
            OffsetEdit("front", "knob_threshold", 950, 120),
            FramesEdit("front", "knob_threshold", "Knob_65x65_61frames", 61),
        ],
    )
    assert result.source == device_source
    assert not result.dirty
    assert len(result.unchanged) == 2


def test_crlf_line_endings_survive(device_source, tmp_path):
    crlf = device_source.replace("\n", "\r\n")
    target = tmp_path / "device_2D.lua"
    target.write_bytes(crlf.encode("utf-8"))
    result = patch_device_2d_file(target, [OffsetEdit("front", "knob_threshold", 900, 130)])
    written = target.read_bytes().decode("utf-8")
    assert result.dirty
    assert "\r\n" in written and "offset = { 900, 130 }" in written
    assert written.count("\r\n") == crlf.count("\r\n")


def test_file_patch_is_written_and_reparses(silence_detector, tmp_path):
    project = tmp_path / "proj"
    shutil.copytree(silence_detector, project)
    target = project / "GUI2D" / "device_2D.lua"
    result = patch_device_2d_file(target, [OffsetEdit("front", "SilenceSwitch", 1800, 150)])
    assert result.dirty
    assert read_device_2d(target).node("front", "SilenceSwitch").offset == (1800, 150)


def test_file_patch_preserves_permissions(silence_detector, tmp_path):
    # mkstemp files are 0600; the replaced file must keep e.g. group-read so
    # CI or teammates on a shared checkout don't lose access to it.
    project = tmp_path / "proj"
    shutil.copytree(silence_detector, project)
    target = project / "GUI2D" / "device_2D.lua"
    target.chmod(0o664)
    patch_device_2d_file(target, [OffsetEdit("front", "SilenceSwitch", 1800, 150)])
    assert (target.stat().st_mode & 0o777) == 0o664


def test_refusal_leaves_the_file_untouched(silence_detector, tmp_path):
    project = tmp_path / "proj"
    shutil.copytree(silence_detector, project)
    target = project / "GUI2D" / "device_2D.lua"
    before = target.read_bytes()
    with pytest.raises(PatchError):
        patch_device_2d_file(
            target,
            [
                OffsetEdit("front", "SilenceSwitch", 1800, 150),  # fine alone
                OffsetEdit("front", "no_such_node", 1, 2),        # poisons the batch
            ],
        )
    assert target.read_bytes() == before


# ---------------------------------------------------------------------------
# refusals: never guess (§10.2)
# ---------------------------------------------------------------------------


def test_unknown_node_refuses(device_source):
    with pytest.raises(PatchError, match="not found"):
        patch_device_2d(device_source, [OffsetEdit("front", "no_such_node", 1, 2)])


def test_node_without_offset_field_refuses(device_source):
    with pytest.raises(PatchError, match="no 'offset' field"):
        patch_device_2d(device_source, [OffsetEdit("front", "Panel_front_bg", 1, 2)])


def test_missing_frames_field_refuses_unless_one(device_source):
    with pytest.raises(PatchError, match="no 'frames = N' literal"):
        patch_device_2d(
            device_source,
            [FramesEdit("front", "DeviceName", "Tape_Horizontal_1frames", 2)],
        )
    # frames == 1 with the field absent is simply already true
    result = patch_device_2d(
        device_source,
        [FramesEdit("front", "DeviceName", "Tape_Horizontal_1frames", 1)],
    )
    assert not result.dirty


def test_frames_below_one_refuses(device_source):
    with pytest.raises(PatchError, match=">= 1"):
        patch_device_2d(
            device_source,
            [FramesEdit("front", "knob_threshold", "Knob_65x65_61frames", 0)],
        )


def test_duplicate_node_in_a_panel_refuses():
    source = (
        'format_version = "2.0"\n'
        "front = {\n"
        "  a = { offset = { 1, 2 }, { path = \"a\", frames = 2 } },\n"
        "  { a = { offset = { 3, 4 }, { path = \"a2\", frames = 2 } } },\n"
        "}\n"
    )
    with pytest.raises(PatchError, match="anchors in the panel"):
        patch_device_2d(source, [OffsetEdit("front", "a", 9, 9)])


def test_offset_expression_refuses():
    source = (
        'format_version = "2.0"\n'
        "front = { a = { offset = { 10 + 5, 20 }, { path = \"a\" } } }\n"
    )
    with pytest.raises(PatchError, match="two plain number literals"):
        patch_device_2d(source, [OffsetEdit("front", "a", 1, 2)])


def test_all_refusal_reasons_are_collected(device_source):
    with pytest.raises(PatchError) as excinfo:
        patch_device_2d(
            device_source,
            [
                OffsetEdit("front", "nope", 1, 2),
                OffsetEdit("front", "Panel_front_bg", 1, 2),
            ],
        )
    assert len(excinfo.value.reasons) == 2


def test_unparseable_source_raises_config_error():
    with pytest.raises(LuaConfigError):
        patch_device_2d("front = {", [OffsetEdit("front", "a", 1, 2)])


# ---------------------------------------------------------------------------
# hostile formatting: comment/string decoys, spacing, separators
# ---------------------------------------------------------------------------


def test_comment_decoys_are_never_matched(hostile_source):
    result = patch_device_2d(hostile_source, [OffsetEdit("front", "knob", 0, 25)])
    assert "offset={0,25}" in result.source.replace(" ", "")
    # both decoys intact
    assert "offset = { 111, 222 }" in result.source
    assert "offset = { 1, 2 }, frames = 9" in result.source
    assert read_device_2d_text(result.source).node("front", "knob").offset == (0, 25)


def test_string_content_is_not_code(hostile_source):
    # 'label -- not a comment' must stay a string; the node still patches.
    result = patch_device_2d(hostile_source, [OffsetEdit("front", "label", 70, 80)])
    assert 'path = "label -- not a comment"' in result.source
    assert read_device_2d_text(result.source).node("front", "label").offset == (70, 80)


def test_substring_node_names_do_not_collide(hostile_source):
    result = patch_device_2d(
        hostile_source, [FramesEdit("front", "knob_big", "knob_big", 32)]
    )
    device = read_device_2d_text(result.source)
    assert device.node("front", "knob_big").frames == 32
    assert device.node("front", "knob").frames == 61


def test_semicolon_separator_and_spacing_survive(hostile_source):
    result = patch_device_2d(hostile_source, [OffsetEdit("front", "knob_big", 110, 210)])
    assert "offset = { 110 , 210 } ;" in result.source


def test_quoted_key_and_multiline_offset(hostile_source):
    result = patch_device_2d(hostile_source, [OffsetEdit("front", "quoted_node", 6, 16)])
    device = read_device_2d_text(result.source)
    assert device.node("front", "quoted_node").offset == (6, 16)
    assert "-- x" in result.source and "-- y" in result.source  # inline comments kept


def test_negative_offsets_patch(hostile_source):
    result = patch_device_2d(hostile_source, [OffsetEdit("front", "knob", -20, -5)])
    assert read_device_2d_text(result.source).node("front", "knob").offset == (-20, -5)


# ---------------------------------------------------------------------------
# scene → edits (compute_device_edits, node_base_offset)
# ---------------------------------------------------------------------------


def test_node_base_offset_folds_group_offsets(device_source):
    device = read_device_2d_text(device_source)
    assert node_base_offset(device, "front", "lamp_silence") == (300.0, 100.0)
    assert node_base_offset(device, "front", "knob_threshold") == (0.0, 0.0)
    assert node_base_offset(device, "front", "missing") is None


def test_compute_edits_empty_when_in_sync(device_source):
    device = read_device_2d_text(device_source)
    element = ElementData(
        node="knob_threshold", path="Knob_65x65_61frames", kind="knob", frames=61,
        placements=(Placement("front", "knob_threshold", 950, 120),),
    )
    edits, notes = compute_device_edits(device, [element])
    assert edits == [] and notes == []


def test_compute_edits_exports_the_scenes_position_not_the_stored_one(device_source):
    # The element's stored mirror still agrees with the Lua; its registration
    # empty has been dragged. Export must write where the empty is — that is
    # the whole reposition-in-Blender workflow.
    device = read_device_2d_text(device_source)
    element = ElementData(
        node="knob_threshold", path="Knob_65x65_61frames", kind="knob", frames=61,
        placements=(Placement("front", "knob_threshold", 950, 120),),
        derived_placements=(Placement("front", "knob_threshold", 900, 130),),
    )
    edits, notes = compute_device_edits(device, [element])
    assert edits == [OffsetEdit("front", "knob_threshold", 900, 130)] and notes == []


def test_compute_edits_converts_absolute_to_relative(device_source):
    device = read_device_2d_text(device_source)
    # lamp_silence moved to absolute (335, 105); its group sits at (300, 100).
    element = ElementData(
        node="lamp_silence", path="Lamp_15x15_2frames", kind="lamp", frames=2,
        placements=(Placement("front", "lamp_silence", 335, 105),),
    )
    edits, _ = compute_device_edits(device, [element])
    assert edits == [OffsetEdit("front", "lamp_silence", 35, 5)]


def test_compute_edits_emits_frames_changes(device_source):
    device = read_device_2d_text(device_source)
    element = ElementData(
        node="knob_threshold", path="Knob_65x65_61frames", kind="knob", frames=31,
        placements=(Placement("front", "knob_threshold", 950, 120),),
    )
    edits, _ = compute_device_edits(device, [element])
    assert edits == [FramesEdit("front", "knob_threshold", "Knob_65x65_61frames", 31)]


def test_compute_edits_notes_unknown_nodes(device_source):
    device = read_device_2d_text(device_source)
    element = ElementData(
        node="new_knob", path="New_Knob", kind="knob", frames=61,
        placements=(Placement("front", "new_knob", 10, 10),),
    )
    edits, notes = compute_device_edits(device, [element])
    assert edits == []
    assert notes and "new_knob" in notes[0]


def test_compute_edits_covers_every_placement(device_source):
    device = read_device_2d_text(device_source)
    # The DeviceName tape appears on all four panels; a frames change (were it
    # multi-frame) must reach each placement's node independently — here we
    # move only the folded_front copy instead.
    element = ElementData(
        node="DeviceName", path="Tape_Horizontal_1frames", kind="static", frames=1,
        placements=(
            Placement("front", "DeviceName", 1665, 25),
            Placement("back", "DeviceName", 1665, 25),
            Placement("folded_front", "DeviceName", 650, 55),
            Placement("folded_back", "DeviceName", 660, 50),
        ),
    )
    edits, _ = compute_device_edits(device, [element])
    assert edits == [OffsetEdit("folded_front", "DeviceName", 650, 55)]
    # ...and the resulting patch is scoped to that one panel.
    result = patch_device_2d(device_source, edits)
    device_after = read_device_2d_text(result.source)
    assert device_after.node("folded_front", "DeviceName").offset == (650, 55)
    assert device_after.node("front", "DeviceName").offset == (1665, 25)
