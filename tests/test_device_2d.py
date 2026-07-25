"""Reading device_2D.lua: nodes, offsets, frame counts, nesting, CableOrigin."""

import pytest

from reblend.project.lua_reader import Graphic, LuaConfigError, read_device_2d


@pytest.fixture
def device(silence_detector):
    return read_device_2d(silence_detector / "GUI2D" / "device_2D.lua")


def test_format_version(device):
    assert device.format_version == "2.0"


def test_all_four_panels_present(device):
    assert set(device.panels) == {"front", "back", "folded_front", "folded_back"}


def test_knob_node(device):
    # Widget nodes live inside the panel's unnamed group, so they are reached
    # by name lookup, not by indexing the panel's top-level dict.
    knob = device.node("front", "knob_threshold")
    assert knob.offset == (950, 120)
    assert knob.graphics == [Graphic(path="Knob_65x65_61frames", frames=61)]
    assert knob.frames == 61


def test_frames_defaults_to_one(device):
    bg = device.panels["front"]["Panel_front_bg"]
    assert bg.offset is None
    assert bg.frames == 1
    assert bg.graphics[0].path == "Panel_Front"


def test_widget_nodes_live_in_unnamed_group(device):
    # The SDK convention: the backdrop is a named top-level entry, but widget
    # nodes are collected inside one unnamed sub-table (an integer key), which
    # the parser models as a single anonymous group node.
    top = device.panels["front"]
    assert "knob_threshold" not in top  # not at the top level...
    groups = [node for node in top.values() if node.anonymous]
    assert len(groups) == 1
    assert set(groups[0].children) == {
        "knob_threshold",
        "SilenceSwitch",
        "lamp_group",
        "logo_plate",
        "DeviceName",
    }


def test_nested_nodes(device):
    group = device.node("front", "lamp_group")
    assert group.offset == (300, 100)
    assert set(group.children) == {"lamp_signal", "lamp_silence"}
    assert group.children["lamp_silence"].offset == (30, 0)
    assert group.children["lamp_silence"].frames == 2


def test_node_lookup_finds_nested(device):
    assert device.node("front", "lamp_silence") is not None
    assert device.node("front", "lamp_silence").frames == 2
    assert device.node("front", "nonexistent") is None


def test_cable_origin_is_named_top_level_node_without_graphics(device):
    # CableOrigin stays a named top-level entry (like the backdrop), alongside
    # the unnamed widget group. It belongs to folded_back only: the spec says
    # cable_origin "must not be present in any other panel specification".
    assert "CableOrigin" not in device.panels["back"]
    origin = device.panels["folded_back"]["CableOrigin"]
    assert origin.offset == (1885, 65)
    assert origin.graphics == []
    assert not origin.anonymous


def test_walk_covers_descendants(device):
    names = {node.name for node in device.node("front", "lamp_group").walk()}
    assert names == {"lamp_group", "lamp_signal", "lamp_silence"}


def test_flat_panel_without_group_still_parses(tmp_path):
    # Backward compatibility: a panel written as a flat map of named nodes
    # (no unnamed group) must still read.
    flat = tmp_path / "device_2D.lua"
    flat.write_text(
        'format_version = "2.0"\n'
        'front = {\n'
        '  bg = {{ path = "Bg" }},\n'
        '  k = { offset = {1, 2}, { path = "K", frames = 3 } },\n'
        "}\n",
        encoding="utf-8",
    )
    device = read_device_2d(flat)
    assert set(device.panels["front"]) == {"bg", "k"}
    assert device.node("front", "k").offset == (1, 2)


def test_multiple_unnamed_groups_in_one_panel(tmp_path):
    # More than one unnamed sub-table is legal; every named child must be found.
    multi = tmp_path / "device_2D.lua"
    multi.write_text(
        'format_version = "2.0"\n'
        'front = {\n'
        '  bg = {{ path = "Bg" }},\n'
        '  { a = { offset = {0, 0}, { path = "A" } } },\n'
        '  { b = { offset = {1, 1}, { path = "B" } } },\n'
        "}\n",
        encoding="utf-8",
    )
    device = read_device_2d(multi)
    assert device.node("front", "a") is not None
    assert device.node("front", "b") is not None
    assert sum(node.anonymous for node in device.panels["front"].values()) == 2


def test_missing_file_raises(tmp_path):
    with pytest.raises(LuaConfigError, match="cannot read file"):
        read_device_2d(tmp_path / "device_2D.lua")


def test_syntax_error_raises(tmp_path):
    bad = tmp_path / "device_2D.lua"
    bad.write_text('format_version = "2.0"\nfront = {', encoding="utf-8")
    with pytest.raises(LuaConfigError, match="Lua error"):
        read_device_2d(bad)


def test_missing_format_version_raises(tmp_path):
    bad = tmp_path / "device_2D.lua"
    bad.write_text("front = {}", encoding="utf-8")
    with pytest.raises(LuaConfigError, match="format_version"):
        read_device_2d(bad)


def test_invalid_frames_raises(tmp_path):
    bad = tmp_path / "device_2D.lua"
    bad.write_text(
        'format_version = "2.0"\n'
        'front = { k = { offset = {0, 0}, { path = "x", frames = 0 } } }',
        encoding="utf-8",
    )
    with pytest.raises(LuaConfigError, match="invalid frames"):
        read_device_2d(bad)
