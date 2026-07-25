"""Project import (read-only, §6.1): correlating device_2D + hdgui_2D into specs."""

import shutil

import pytest

from reblend.model import kinds
from reblend.project.link import load_project
from reblend.project.png_meta import write_rgba_png


@pytest.fixture
def project(silence_detector):
    return load_project(silence_detector)


def by_path(project):
    return {spec.path: spec for spec in project.specs}


def test_one_element_per_sprite_path(project):
    assert set(by_path(project)) == {
        "Panel_Front",
        "Panel_Back",
        "Panel_Folded_Front",
        "Panel_Folded_Back",
        "Knob_65x65_61frames",
        "Button_50x35_2frames",
        "Lamp_15x15_2frames",
        "Tape_Horizontal_1frames",
        "Fader_25x60_3frames",
        "SharedAudioJack",
        "Logo_120x40_1frames",
        "Placeholder",
    }


def test_kinds_and_frames(project):
    specs = by_path(project)
    assert (specs["Knob_65x65_61frames"].kind, specs["Knob_65x65_61frames"].frames) == (kinds.KNOB, 61)
    assert specs["Button_50x35_2frames"].kind == kinds.BUTTON_TOGGLE
    assert specs["Lamp_15x15_2frames"].kind == kinds.LAMP
    assert specs["Fader_25x60_3frames"].kind == kinds.FADER_HANDLE
    assert specs["SharedAudioJack"].kind == kinds.SOCKET
    assert specs["Logo_120x40_1frames"].kind == kinds.STATIC
    # Reason draws the tape and the placeholder itself: their art is the
    # SDK's, so RE-Blend must not render over it.
    assert specs["Tape_Horizontal_1frames"].kind == kinds.SDK_SUPPLIED
    assert specs["Placeholder"].kind == kinds.SDK_SUPPLIED
    for panel_sheet in ("Panel_Front", "Panel_Back", "Panel_Folded_Front", "Panel_Folded_Back"):
        assert specs[panel_sheet].kind == kinds.BACKDROP


def test_shared_sheet_collects_all_placements(project):
    lamp = by_path(project)["Lamp_15x15_2frames"]
    placed = [(p.node, p.x, p.y) for p in lamp.placements]
    # nested group offset (300, 100) folded into absolute positions
    assert placed == [("lamp_signal", 300, 100), ("lamp_silence", 330, 100)]
    assert lamp.node == "lamp_signal"
    assert lamp.panels == ("front",)


def test_multi_panel_element(project):
    tape = by_path(project)["Tape_Horizontal_1frames"]
    assert tape.panels == ("front", "back", "folded_front", "folded_back")
    assert all(p.node == "DeviceName" for p in tape.placements)


def test_point_nodes_are_not_elements(project):
    # CableOrigin has an offset but no graphics: a coordinate, not a sheet.
    assert "CableOrigin" not in {p.node for s in project.specs for p in s.placements}


def test_frame_size_unknown_without_pngs(project):
    knob = by_path(project)["Knob_65x65_61frames"]
    assert (knob.frame_w, knob.frame_h) == (0, 0)


def test_widget_kinds_recorded(project):
    specs = by_path(project)
    assert "analog_knob" in specs["Knob_65x65_61frames"].widget_kinds
    assert "sequence_meter" in specs["Lamp_15x15_2frames"].widget_kinds
    assert "static_decoration" in specs["Logo_120x40_1frames"].widget_kinds


def test_motherboard_steps_loaded(project):
    assert project.property_steps["/custom_properties/mode"] == 4
    assert project.property_steps["/custom_properties/builtin_onoffbypass"] == 3


def test_frame_size_probed_from_existing_sheet(silence_detector, tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(silence_detector, root)
    write_rgba_png(root / "GUI2D" / "Knob_65x65_61frames.png", 65, 65 * 61, bytes(65 * 65 * 61 * 4))
    knob = load_project(root).spec("Knob_65x65_61frames")
    assert (knob.frame_w, knob.frame_h) == (65, 65)


def test_indivisible_sheet_leaves_size_unknown(silence_detector, tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(silence_detector, root)
    # 100 rows over 61 frames does not divide: probing must not guess.
    write_rgba_png(root / "GUI2D" / "Knob_65x65_61frames.png", 65, 100, bytes(65 * 100 * 4))
    knob = load_project(root).spec("Knob_65x65_61frames")
    assert (knob.frame_w, knob.frame_h) == (0, 0)


def test_element_data_bridge(project):
    data = by_path(project)["Fader_25x60_3frames"].to_element_data()
    assert data.node == "OnOffBypass"
    assert data.kind == kinds.FADER_HANDLE
    assert data.frames == 3
    assert data.placements[0].panel == "folded_front"
