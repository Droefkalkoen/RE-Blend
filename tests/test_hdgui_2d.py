"""Reading hdgui_2D.lua: jbox recorder, widget bindings, verbatim attributes."""

import pytest

from reblend.project.lua_reader import LuaConfigError, read_hdgui_2d


@pytest.fixture
def hdgui(silence_detector):
    return read_hdgui_2d(silence_detector / "GUI2D" / "hdgui_2D.lua")


def test_format_version(hdgui):
    assert hdgui.format_version == "2.0"


def test_panels_and_backgrounds(hdgui):
    assert set(hdgui.panels) == {"front", "back", "folded_front", "folded_back"}
    assert hdgui.panels["front"].graphics_node == "Panel_front_bg"
    assert hdgui.panels["folded_back"].graphics_node == "Panel_folded_back_bg"


def test_knob_widget(hdgui):
    knob, = [w for w in hdgui.panels["front"].widgets if w.kind == "analog_knob"]
    assert knob.node == "knob_threshold"
    assert knob.value == "/custom_properties/threshold"


def test_sequence_fader_on_folded_front(hdgui):
    fader, = [w for w in hdgui.panels["folded_front"].widgets if w.kind == "sequence_fader"]
    assert fader.node == "OnOffBypass"
    assert fader.value == "/custom_properties/builtin_onoffbypass"
    assert fader.attrs["handle_size"] == 0
    assert fader.attrs["inverted"] is False


def test_unknown_attributes_preserved(hdgui):
    deco, = [w for w in hdgui.panels["front"].widgets if w.kind == "static_decoration"]
    assert deco.attrs["blend_mode"] == "luminance"
    # jbox.ui_text("...") is a non-table constructor call; the recorder keeps
    # both the constructor name and its argument.
    assert deco.attrs["ui_name"] == {"__jbox": "ui_text", "value": "logo plate"}


def test_widget_without_value_binding(hdgui):
    name_widgets = hdgui.widgets_for_node("DeviceName")
    assert len(name_widgets) == 4  # one per panel, per acceptance checklist
    assert all(w.kind == "device_name" and w.value is None for w in name_widgets)


def test_panel_level_attrs_preserved(hdgui):
    assert hdgui.panels["folded_back"].attrs["cable_origin"] == {"node": "CableOrigin"}
    assert "cable_origin" not in hdgui.panels["back"].attrs


def test_main_node_spelling_is_accepted(hdgui):
    """static_decoration and custom_display name their node `main_node`.

    Reading only `graphics.node` would silently detach them from the node.
    """
    deco, = [w for w in hdgui.panels["front"].widgets if w.kind == "static_decoration"]
    assert deco.attrs["graphics"] == {"main_node": "logo_plate"}
    assert deco.node == "logo_plate"
    assert [w.kind for w in hdgui.widgets_for_node("logo_plate")] == ["static_decoration"]


def test_widgets_for_node_across_panels(hdgui):
    sockets = hdgui.widgets_for_node("MainInLeft")
    assert [w.kind for w in sockets] == ["audio_input_socket"]
    assert sockets[0].attrs["socket"] == "/audio_inputs/InLeft"


def test_non_panel_global_raises(tmp_path):
    bad = tmp_path / "hdgui_2D.lua"
    bad.write_text('format_version = "2.0"\nfront = { widgets = {} }', encoding="utf-8")
    with pytest.raises(LuaConfigError, match="jbox.panel"):
        read_hdgui_2d(bad)


def test_non_constructor_widget_raises(tmp_path):
    bad = tmp_path / "hdgui_2D.lua"
    bad.write_text(
        'format_version = "2.0"\n'
        'front = jbox.panel{ graphics = { node = "Bg" }, widgets = { { node = "x" } } }',
        encoding="utf-8",
    )
    with pytest.raises(LuaConfigError, match="not a jbox constructor"):
        read_hdgui_2d(bad)
