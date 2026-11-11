"""RE Bench phase 3: templates, add_control stubs, grey-box sprites, and
RE-Blend reading the sidecar."""

import json
import os
import re
import shutil
import subprocess
import zlib
from pathlib import Path

import pytest

from reblend.model.blockout import blockout_mesh
from reblend.project.link import load_project
from reblend.project.lua_reader import _execute_sandboxed
from reblend.project.png_meta import read_png_meta
from rebench.agent import rules_check
from rebench.cli import main
from rebench.greybox import build_greybox
from rebench.new import DeviceIdentity, new_device
from rebench.ops import OpError, apply_ops
from rebench.sidecar import Sidecar
from test_rebench import _png, make_project


@pytest.fixture
def device(tmp_path):
    root = tmp_path / "grit"
    new_device(root, DeviceIdentity("Grit Distortion", "Terp Audio"))
    return root


def _ui_text_keys(source: str) -> set[str]:
    return set(re.findall(r'jbox\.ui_text\(\s*"([^"]+)"\s*\)', source))


def _texts(root: Path) -> dict:
    return _execute_sandboxed(root / "Resources/English/texts.lua")["texts"]


def _rows(png: Path):
    """Decode an 8-bit RGBA PNG written without filters (as greybox writes them)."""
    data = png.read_bytes()
    meta = read_png_meta(png)
    idat, i = b"", 8
    while i < len(data):
        n = int.from_bytes(data[i:i + 4], "big")
        if data[i + 4:i + 8] == b"IDAT":
            idat += data[i + 8:i + 8 + n]
        i += 12 + n
    raw = zlib.decompress(idat)
    stride = meta.width * 4 + 1
    return [raw[r * stride + 1:(r + 1) * stride] for r in range(meta.height)]


# --- rebench new -----------------------------------------------------------

def test_new_device_passes_every_rule(device):
    assert rules_check(device)["findings"] == []


def test_new_device_files_load_and_agree(device):
    info = _execute_sandboxed(device / "info.lua")
    assert info["device_type"] == "creative_fx"
    assert info["long_name"] == "Grit Distortion"
    assert info["product_id"] == "com.terpaudio.GritDistortion"
    for name in ("motherboard_def.lua", "realtime_controller.lua"):
        _execute_sandboxed(device / name)
    used = _ui_text_keys((device / "motherboard_def.lua").read_text())
    assert used <= set(_texts(device))
    assert (device / ".gitignore").is_file()
    assert "@@" not in "".join(p.read_text() for p in device.rglob("*")
                               if p.is_file() and p.suffix in {".lua", ".cpp", ".h", ".txt", ".md"})


def test_new_device_has_art_for_every_sheet(device):
    link = load_project(device)
    for spec in link.specs:
        png = device / "GUI2D" / f"{spec.path}.png"
        meta = read_png_meta(png)
        assert meta.is_8bit_rgba
        if spec.frame_w:
            assert (meta.width, meta.height) == (spec.frame_w, spec.frame_h * spec.frames)


def test_new_device_two_units(tmp_path):
    root = tmp_path / "tall"
    new_device(root, DeviceIdentity("Tall One", "Terp Audio", height_ru=2))
    assert read_png_meta(root / "GUI2D/Panel_Front.png").height == 690
    assert rules_check(root)["findings"] == []
    knob = next(s for s in load_project(root).specs if s.path == "knob_level")
    assert knob.placements[0].y == 690 // 2 - 60


def test_new_device_refuses_a_non_empty_folder(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "keep.txt").write_text("mine")
    with pytest.raises(FileExistsError):
        new_device(tmp_path / "x", DeviceIdentity("A", "B"))


def test_new_device_escapes_lua_strings(tmp_path):
    root = tmp_path / "q"
    new_device(root, DeviceIdentity('Say "Hi"', "Back\\slash"))
    info = _execute_sandboxed(root / "info.lua")
    assert info["long_name"] == 'Say "Hi"'
    assert info["manufacturer"] == "Back\\slash"


@pytest.mark.skipif(not shutil.which("g++") or not os.environ.get("REBENCH_JUKEBOX_DIR"),
                    reason="needs g++ and REBENCH_JUKEBOX_DIR (the SDK's API folder)")
def test_new_device_cpp_compiles_against_the_sdk_header(device):
    cpp = device / "src/cpp"
    subprocess.run(["g++", "-std=c++17", "-fsyntax-only", "-Wall", "-I", str(cpp),
                    "-I", os.environ["REBENCH_JUKEBOX_DIR"],
                    str(cpp / "Device.cpp"), str(cpp / "JukeboxExports.cpp")], check=True)


def test_cli_new(tmp_path, capsys):
    root = tmp_path / "cli"
    assert main(["new", str(root), "--name", "Cli Fx", "--manufacturer", "Me"]) == 0
    assert "created" in capsys.readouterr().out
    assert main(["new", str(root), "--name", "Again", "--manufacturer", "Me"]) == 2


# --- add_control -----------------------------------------------------------

def test_add_control_writes_all_four_files(device):
    result = apply_ops(device, [
        {"op": "add_control", "panel": "front", "node": "knob_drive", "widget": "analog_knob",
         "x": 1300, "y": 110, "title": "Drive"},
        {"op": "add_control", "panel": "front", "node": "sel_mode", "widget": "sequence_fader",
         "x": 2300, "y": 50, "steps": 4},
        {"op": "add_control", "panel": "front", "node": "btn_boost", "widget": "toggle_button",
         "x": 2600, "y": 120},
    ])
    assert result.written
    mb = (device / "motherboard_def.lua").read_text()
    assert "drive = jbox.number" in mb and "mode = jbox.number" in mb and "boost = jbox.boolean" in mb
    assert _ui_text_keys(mb) <= set(_texts(device))
    assert _texts(device)["drive"] == "Drive"
    tags = [int(t) for t in re.findall(r"property_tag = (\d+)", mb)]
    assert len(tags) == len(set(tags)) and sorted(tags) == [1, 2, 3, 4]
    link = load_project(device)
    mode = link.spec("sel_mode")
    assert mode.frames == 4 and link.property_steps["/custom_properties/mode"] == 4
    widgets = {w.node: w.kind for w in link.hdgui.panels["front"].widgets}
    assert widgets["knob_drive"] == "analog_knob"
    assert Sidecar.load(device).sprites["knob_drive"].frame_w == 120
    # the template's comments survive
    assert "-- Where each sprite sits" in (device / "GUI2D/device_2D.lua").read_text()


def test_add_control_binds_an_existing_property_without_a_stub(device):
    before = (device / "motherboard_def.lua").read_text()
    apply_ops(device, [{"op": "add_control", "panel": "folded_front", "node": "knob_level_f",
                        "widget": "analog_knob", "property": "level", "x": 900, "y": 15}])
    assert (device / "motherboard_def.lua").read_text() == before
    assert "knob_level_f" in (device / "GUI2D/hdgui_2D.lua").read_text()


def test_add_control_refusals_write_nothing(device):
    before = {p: p.read_text() for p in device.rglob("*.lua")}
    for op in (
        {"op": "add_control", "panel": "front", "node": "knob_level", "widget": "analog_knob",
         "x": 0, "y": 0},                                       # node exists
        {"op": "add_control", "panel": "front", "node": "meter", "widget": "sequence_meter",
         "x": 0, "y": 0},                                       # no stub for a meter
        {"op": "add_control", "panel": "front", "node": "bad name", "widget": "analog_knob",
         "x": 0, "y": 0},                                       # not an identifier
    ):
        with pytest.raises(OpError):
            apply_ops(device, [{"op": "add_control", "panel": "front", "node": "knob_ok",
                                "widget": "analog_knob", "x": 1300, "y": 100}, op])
    assert {p: p.read_text() for p in device.rglob("*.lua")} == before


def test_add_control_dry_run_reports_findings(device):
    result = apply_ops(device, [{"op": "add_control", "panel": "front", "node": "knob_edge",
                                 "widget": "analog_knob", "x": 3650, "y": 100}], dry_run=True)
    assert "rack-ear" in {f.code for f in result.added}
    assert "knob_edge" not in (device / "GUI2D/device_2D.lua").read_text()


def test_add_control_on_a_project_without_groups(tmp_path):
    root = make_project(tmp_path)
    (root / "motherboard_def.lua").write_text(
        'format_version = "3.0"\ncustom_properties = jbox.property_set {\n'
        '  document_owner = {\n    properties = {\n    },\n  },\n}\n')
    (root / "Resources/English").mkdir(parents=True)
    (root / "Resources/English/texts.lua").write_text('format_version = "1.0"\ntexts = {}\n')
    apply_ops(root, [{"op": "add_control", "panel": "back", "node": "btn_x",
                      "widget": "momentary_button", "x": 1000, "y": 200}])
    mb = (root / "motherboard_def.lua").read_text()
    assert 'persistence = "none"' in mb
    assert _texts(root)["x"] == "X"
    assert load_project(root).spec("btn_x").placements[0].panel == "back"


# --- greybox ---------------------------------------------------------------

def test_greybox_never_overwrites_real_art(device):
    art = device / "GUI2D/knob_level.png"
    _png(art, 120, 120 * 61)  # a render from RE-Blend replaces the placeholder
    marker = art.read_bytes()
    result = build_greybox(device)
    assert "knob_level" in result.kept
    assert art.read_bytes() == marker
    assert "knob_level" not in Sidecar.load(device).greybox
    build_greybox(device)  # and stays kept on later runs
    assert art.read_bytes() == marker


def test_greybox_redraws_its_own_sheets_after_a_resize(device):
    apply_ops(device, [{"op": "set_frame_size", "sprite": "knob_level", "w": 150, "h": 150}])
    build_greybox(device)
    assert read_png_meta(device / "GUI2D/knob_level.png").width == 150


def test_greybox_dry_run_writes_nothing(device):
    (device / "GUI2D/knob_level.png").unlink()
    result = build_greybox(device, dry_run=True)
    assert "knob_level" in result.drawn
    assert not (device / "GUI2D/knob_level.png").exists()


def test_greybox_knob_turns_and_fader_starts_at_the_bottom(device):
    rows = _rows(device / "GUI2D/knob_level.png")
    assert rows[:120] != rows[-120:]  # frame 0 differs from frame 60
    fader = _rows(device / "GUI2D/fader_onoffbypass.png")  # 60 x 240, 3 frames
    frame0 = fader[:240]
    lit = [y for y, row in enumerate(frame0) if bytes([255, 176, 59, 255]) in row]
    assert lit and min(lit) > 120  # the handle's marker sits in the lower half


def test_greybox_copies_sdk_parts(device, tmp_path):
    sdk = tmp_path / "sdk/RE2DRender/Images"
    sdk.mkdir(parents=True)
    shutil.copyfile(device / "GUI2D/Placeholder.png", sdk / "Placeholder.png")
    result = build_greybox(device, sdk_root=tmp_path / "sdk")
    assert "Placeholder" in result.copied
    assert "Placeholder" not in Sidecar.load(device).greybox


# --- RE-Blend reads the sidecar -------------------------------------------

def test_load_project_takes_frame_size_and_shape_from_the_sidecar(device):
    (device / "GUI2D/knob_level.png").unlink()
    spec = load_project(device).spec("knob_level")
    assert (spec.frame_w, spec.frame_h) == (120, 120)
    assert spec.shape == {"type": "knob", "diameter_px": 110}


def test_projects_without_a_sidecar_are_unchanged(tmp_path):
    root = make_project(tmp_path)
    spec = load_project(root).spec("Knob")
    assert (spec.frame_w, spec.frame_h, spec.shape) == (100, 100, {})


def test_sidecar_ignores_keys_it_does_not_know(tmp_path):
    root = make_project(tmp_path)
    (root / "GUI2D/rebench.json").write_text(json.dumps({
        "version": 1, "future": True,
        "sprites": {"Knob": {"frame_w": 80, "frame_h": 80, "zones": ["a"]}},
        "labels": [{"id": "l", "panel": "front", "text": "A", "x": 0, "y": 0, "size_px": 48,
                    "font": "x"}]}))
    assert load_project(root).spec("Knob").frame_w == 80


def test_blockout_meshes():
    knob = blockout_mesh({"type": "knob", "diameter_px": 100}, 120, 120, ppb=100)
    xs = [v[0] for v in knob.verts]
    assert knob.kind == "knob" and max(xs) == pytest.approx(0.5)
    assert min(v[1] for v in knob.verts) < 0  # stands out towards a -Y camera
    fader = blockout_mesh({"type": "fader"}, 60, 240, ppb=100)
    assert max(v[2] for v in fader.verts) < 0  # handle at the bottom (frame 0)
    assert blockout_mesh({}, 100, 100, 100) is None
    assert blockout_mesh({"type": "knob"}, 0, 0, 100) is None
    box = blockout_mesh({"type": "panel-thing"}, 100, 50, ppb=100)
    assert box.kind == "box" and len(box.faces) == 6
