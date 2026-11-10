"""RE Bench phase 1: the rule catalogue, the layout rules and the check command."""

import json
import re
import struct
import zlib
from pathlib import Path

import pytest

from rebench import RULES, Profile, run_checks
from rebench.cli import main

REPO = Path(__file__).resolve().parent.parent


def _png(path: Path, w: int, h: int) -> None:
    raw = b"".join(b"\x00" + b"\x00" * (w * 4) for _ in range(h))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


# A minimal, legal 1U effect. Each test patches one thing in it.
DEVICE_2D = """
format_version = "2.0"
front = {
  Panel_Front = {{ path = "Panel_Front" }},
  {
    Tape = { offset = { 200, 140 }, { path = "Tape" } },
    Bypass = { offset = { 200, 30 }, { path = "Bypass", frames = 3 } },
    Knob = { offset = { KNOB_X, 100 }, { path = "Knob", frames = 61 } },
  },
}
folded_front = {
  Panel_Folded_Front = {{ path = "Panel_Folded_Front" }},
  {
    Tape_F = { offset = { 500, 40 }, { path = "Tape" } },
    Bypass_F = { offset = { 200, 20 }, { path = "Bypass", frames = 3 } },
  },
}
back = {
  Panel_Back = {{ path = "Panel_Back" }},
  {
    Tape_B = { offset = { 200, 40 }, { path = "Tape" } },
    InL = { offset = { 600, 120 }, { path = "Jack", frames = 3 } },
    Placeholder = { offset = { 1700, 120 }, { path = "Placeholder" } },
  },
}
folded_back = {
  Panel_Folded_Back = {{ path = "Panel_Folded_Back" }},
  CableOrigin = { offset = { 260, 75 } },
  {
    Tape_FB = { offset = { 500, 40 }, { path = "Tape" } },
  },
}
"""

HDGUI_2D = """
format_version = "2.0"
front = jbox.panel{ graphics = { node = "Panel_Front" }, widgets = {
  jbox.device_name{ graphics = { node = "Tape" } },
  jbox.sequence_fader{ graphics = { node = "Bypass" }, value = "/custom_properties/builtin_onoffbypass", handle_size = 0 },
  jbox.analog_knob{ graphics = { node = "Knob" }, value = "/custom_properties/gain" },
}}
folded_front = jbox.panel{ graphics = { node = "Panel_Folded_Front" }, widgets = {
  jbox.device_name{ graphics = { node = "Tape_F" } },
  jbox.sequence_fader{ graphics = { node = "Bypass_F" }, value = "/custom_properties/builtin_onoffbypass", handle_size = 0 },
}}
back = jbox.panel{ graphics = { node = "Panel_Back" }, widgets = {
  jbox.device_name{ graphics = { node = "Tape_B" } },
  jbox.audio_input_socket{ graphics = { node = "InL" }, socket = "/audio_inputs/L" },
  jbox.placeholder{ graphics = { node = "Placeholder" } },
}}
folded_back = jbox.panel{ graphics = { node = "Panel_Folded_Back" }, cable_origin = { node = "CableOrigin" }, widgets = {
  jbox.device_name{ graphics = { node = "Tape_FB" } },
}}
"""

SIZES = {
    "Panel_Front": (3770, 345), "Panel_Back": (3770, 345),
    "Panel_Folded_Front": (3770, 150), "Panel_Folded_Back": (3770, 150),
    "Tape": (400, 65), "Bypass": (95, 330), "Knob": (100, 6100),
    "Jack": (95, 315), "Placeholder": (300, 100),
}


def make_project(tmp_path, device_type="creative_fx", knob_x=1000, device=None, hdgui=None):
    gui = tmp_path / "GUI2D"
    gui.mkdir()
    (tmp_path / "info.lua").write_text(f'format_version = "1.0"\ndevice_type = "{device_type}"\n')
    (gui / "device_2D.lua").write_text((device or DEVICE_2D).replace("KNOB_X", str(knob_x)))
    (gui / "hdgui_2D.lua").write_text(hdgui or HDGUI_2D)
    for name, (w, h) in SIZES.items():
        _png(gui / f"{name}.png", w, h)
    return tmp_path


def codes(result):
    return sorted({f.code for f in result.findings})


def test_a_legal_effect_has_no_findings(tmp_path):
    result = run_checks(make_project(tmp_path))
    assert result.findings == []


def test_every_validation_code_is_in_the_catalogue():
    source = (REPO / "reblend/project/validation.py").read_text()
    emitted = set(re.findall(r'report\.add\(\s*\w+,\s*"([a-z-]+)"', source, re.S))
    assert emitted and emitted <= set(RULES)


def test_every_rule_names_a_known_source():
    assert {r.source for r in RULES.values()} <= {"req", "spec", "guide", "obs", "house", "tool"}


def test_fold_arrow_overlap_is_an_error_now_it_is_measured(tmp_path):
    root = make_project(tmp_path, device=DEVICE_2D.replace("offset = { 500, 40 }, { path = \"Tape\" } },\n    Bypass_F",
                                                           "offset = { 60, 40 }, { path = \"Tape\" } },\n    Bypass_F"))
    found = [f for f in run_checks(root).findings if f.code == "fold-arrow"]
    assert [(f.severity, f.panel, f.subject, f.source) for f in found] == [
        ("error", "folded_front", "Tape_F", "req")]
    # A profile that marks its keep-out unmeasured only warns.
    provisional = run_checks(root, Profile(fold_arrow_measured=False))
    assert [f.severity for f in provisional.findings if f.code == "fold-arrow"] == ["warning"]


def test_controls_on_the_rack_ear_are_flagged_but_the_tape_is_not(tmp_path):
    root = make_project(tmp_path, knob_x=3640,
                        device=DEVICE_2D.replace("offset = { 200, 140 }", "offset = { 30, 140 }"))
    found = [(f.code, f.subject) for f in run_checks(root).findings if f.code == "rack-ear"]
    assert found == [("rack-ear", "Knob")]


def test_effect_bypass_must_be_top_left(tmp_path):
    root = make_project(tmp_path, device=DEVICE_2D.replace("offset = { 200, 30 }", "offset = { 2000, 30 }"))
    found = [f for f in run_checks(root).findings if f.code == "bypass-top-left"]
    assert [(f.severity, f.panel) for f in found] == [("error", "front")]


def test_bypass_rule_only_applies_to_effects(tmp_path):
    root = make_project(tmp_path, device_type="instrument",
                        device=DEVICE_2D.replace("offset = { 200, 30 }", "offset = { 2000, 30 }"))
    assert "bypass-top-left" not in codes(run_checks(root))


def test_tape_must_be_on_every_panel(tmp_path):
    hd = HDGUI_2D.replace('jbox.device_name{ graphics = { node = "Tape_FB" } },', "")
    found = [f for f in run_checks(make_project(tmp_path, hdgui=hd)).findings if f.code == "device-name"]
    assert [(f.severity, f.panel) for f in found] == [("error", "folded_back")]


def test_sockets_belong_on_the_back(tmp_path):
    hd = HDGUI_2D.replace(
        'jbox.analog_knob{ graphics = { node = "Knob" }, value = "/custom_properties/gain" },',
        'jbox.audio_output_socket{ graphics = { node = "Knob" }, socket = "/audio_outputs/L" },')
    assert "socket-panel" in codes(run_checks(make_project(tmp_path, hdgui=hd)))


def test_socket_sprites_keep_three_frames(tmp_path):
    dev = DEVICE_2D.replace('{ path = "Jack", frames = 3 }', '{ path = "Jack" }')
    result = run_checks(make_project(tmp_path, device=dev))
    found = [f for f in result.findings if f.code == "sdk-part-frames"]
    assert [(f.subject, f.source) for f in found] == [("InL", "obs")]


def test_cli_json_and_exit_code(tmp_path, capsys):
    root = make_project(tmp_path, device=DEVICE_2D.replace("offset = { 200, 30 }", "offset = { 2000, 30 }"))
    assert main(["check", str(root), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["errors"] == 1
    assert out["findings"][0]["code"] == "bypass-top-left"
    assert out["findings"][0]["source"] == "req"


def test_cli_passes_a_clean_project(tmp_path, capsys):
    assert main(["check", str(make_project(tmp_path))]) == 0
    assert "0 error(s), 0 warning(s)" in capsys.readouterr().out


def test_scene_only_codes_are_skipped(silence_detector):
    result = run_checks(silence_detector)
    assert not {"states", "rotor", "shadow-owner"} & set(codes(result))
