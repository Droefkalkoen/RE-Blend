"""RE Bench: removing controls and derived sheets (Skou feedback #38)."""

import pytest

from reblend.project.link import load_project
from reblend.project.lua_reader import _execute_sandboxed
from reblend.project.validation import validate_link
from rebench.agent import rules_check
from rebench.new import DeviceIdentity, new_device
from rebench.ops import OpError, apply_ops
from rebench.sidecar import Sidecar


@pytest.fixture
def device(tmp_path):
    root = tmp_path / "grit"
    new_device(root, DeviceIdentity("Grit Distortion", "Terp Audio"))
    return root


def _texts(root):
    return _execute_sandboxed(root / "Resources/English/texts.lua")["texts"]


def _props(root):
    mb = _execute_sandboxed(root / "motherboard_def.lua")
    return set(mb["custom_properties"]["document_owner"]["properties"])


def test_remove_control_undoes_add_control(device):
    before = {p: (device / p).read_text() for p in (
        "GUI2D/device_2D.lua", "GUI2D/hdgui_2D.lua", "motherboard_def.lua",
        "Resources/English/texts.lua")}
    apply_ops(device, [{"op": "add_control", "panel": "front", "node": "knob_drive",
                        "widget": "analog_knob", "x": 900, "y": 110, "title": "Drive"}])
    assert "drive" in _props(device)
    result = apply_ops(device, [{"op": "remove_control", "panel": "front",
                                 "node": "knob_drive", "drop_property": True}])
    assert result.written
    for path, text in before.items():
        assert (device / path).read_text() == text, path
    assert "knob_drive" not in Sidecar.load(device).sprites


def test_remove_control_keeps_a_shared_property(device):
    apply_ops(device, [
        {"op": "add_control", "panel": "front", "node": "knob_drive",
         "widget": "analog_knob", "x": 900, "y": 110},
        {"op": "add_control", "panel": "folded_front", "node": "knob_drive_f",
         "widget": "analog_knob", "x": 900, "y": 15, "property": "drive", "w": 60, "h": 60},
    ])
    result = apply_ops(device, [{"op": "remove_control", "panel": "front",
                                 "node": "knob_drive", "drop_property": True}])
    assert "drive" in _props(device)
    assert any("other widgets still bind it" in a for a in result.applied)
    link = load_project(device)
    assert link.device.node("front", "knob_drive") is None
    assert link.device.node("folded_front", "knob_drive_f") is not None


def test_remove_unknown_control_refuses(device):
    with pytest.raises(OpError, match="no node 'nope'"):
        apply_ops(device, [{"op": "remove_control", "panel": "front", "node": "nope"}])


def test_derived_sheet_needs_no_element(device):
    link = load_project(device)
    fader = next(s for s in link.specs if s.kind == "fader_handle" and
                 s.placements[0].panel == "front")
    folded = next(s for s in link.specs if s.kind == "fader_handle" and
                  s.placements[0].panel == "folded_front")
    crop = [0, 0, folded.frame_w, folded.frame_h]
    apply_ops(device, [{"op": "derive", "sprite": folded.path, "from": fader.path,
                        "crop": crop}])
    link = load_project(device)
    assert folded.path in link.derived
    assert folded.path not in {s.path for s in link.scene_specs}
    # A scene with only the source element: no missing-art for the derived one.
    elements = [s.to_element_data() for s in link.scene_specs]
    codes = {(f.code, f.subject) for f in validate_link(link, elements).findings}
    assert ("missing-art", folded.path) not in codes
    assert rules_check(device)["findings"] == []


def test_derive_crop_outside_the_source_frame_is_an_error(device):
    link = load_project(device)
    fader = next(s for s in link.specs if s.kind == "fader_handle")
    other = next(s for s in link.specs if s.kind == "fader_handle" and s is not fader)
    apply_ops(device, [{"op": "derive", "sprite": other.path, "from": fader.path,
                        "crop": [0, 0, fader.frame_w + 5, 10]}])
    codes = {f["code"] for f in rules_check(device)["findings"]}
    assert "derived" in codes


def test_sidecar_without_derived_sheets_keeps_its_shape(device):
    apply_ops(device, [{"op": "set_frame_size", "sprite": "x", "w": 10, "h": 10}])
    raw = (device / "GUI2D" / "rebench.json").read_text()
    assert "derive_from" not in raw and "crop" not in raw
