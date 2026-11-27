"""Rendering, the manifest, dirty tracking and the headless CLI, in a real
(module) Blender with Cycles at one sample."""

import json

import pytest

bpy = pytest.importorskip("bpy")

import reblend  # noqa: E402
from reblend.project.png_meta import read_png_meta  # noqa: E402
from reblend.render import fingerprint  # noqa: E402
from reblend.project.sidecar import Sidecar, SpriteInfo  # noqa: E402

LAMP = "Lamp_15x15_2frames"


@pytest.fixture
def lamp_scene(imported):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    col = bpy.data.collections[LAMP]
    col["re_frame_w"], col["re_frame_h"] = 15, 15
    mesh = bpy.data.meshes.new("lamp_cap")
    mesh.from_pydata([(-0.02, 0, -0.02), (0.02, 0, -0.02), (0.02, 0, 0.02), (-0.02, 0, 0.02)],
                     [], [(0, 1, 2, 3)])
    cap = bpy.data.objects.new("lamp_cap", mesh)
    col.objects.link(cap)
    cap.location = bpy.data.objects[col["re_registration"]].matrix_world.translation
    return imported, cap


def test_render_writes_sheet_manifest_and_state(lamp_scene, tmp_path):
    project, _cap = lamp_scene
    report = tmp_path / "report.json"
    code = reblend.cli(["render", "--element", LAMP, "--json", "--report", str(report)])
    assert code == 0
    meta = read_png_meta(project / "GUI2D" / f"{LAMP}.png")
    assert (meta.width, meta.height) == (15, 30)
    assert bpy.context.scene.reblend.render_state == "DONE"
    manifest = fingerprint.Manifest.load(project / "GUI2D")
    assert manifest.sheets[LAMP].frames == 2
    result = json.loads(report.read_text())
    assert result["exit_code"] == 0 and result["rendered"][0]["ok"]


def test_dirty_tracks_edits_and_overwritten_sheets(lamp_scene):
    project, cap = lamp_scene
    assert reblend.cli(["render", "--element", LAMP]) == 0
    from reblend.ui import operators

    def dirty():
        return {c.name: r for c, r in operators.dirty_elements(bpy.context, project)}

    assert LAMP not in dirty()
    cap.location.x += 0.001
    assert dirty()[LAMP] == "its inputs changed"
    cap.location.x -= 0.001
    assert LAMP not in dirty()
    (project / "GUI2D" / f"{LAMP}.png").write_bytes(b"not a png")
    assert "changed since it was rendered" in dirty()[LAMP]


def test_render_dirty_renders_only_stale_sheets(lamp_scene, tmp_path):
    project, _cap = lamp_scene
    for name in [c.name for c in bpy.data.collections]:
        col = bpy.data.collections[name]
        if col.get("re_kind") not in (None, "lamp"):
            col["re_kind"] = "text_bounds"  # keep the run to the one lamp
    report = tmp_path / "r.json"
    assert reblend.cli(["render", "--dirty", "--report", str(report)]) == 0
    assert [r["element"] for r in json.loads(report.read_text())["rendered"]] == [LAMP]
    assert reblend.cli(["render", "--dirty", "--report", str(report)]) == 0
    assert json.loads(report.read_text())["rendered"] == []


def test_derived_sheet_is_cut_from_its_source(lamp_scene):
    project, _cap = lamp_scene
    side = Sidecar.load(project)
    # Pretend the button sheet is a crop of the lamp (frames must agree: 2).
    side.sprites["Button_50x35_2frames"] = SpriteInfo(derive_from=LAMP, crop=[5, 5, 10, 10])
    side.save(project)
    assert reblend.cli(["render", "--element", LAMP]) == 0
    meta = read_png_meta(project / "GUI2D" / "Button_50x35_2frames.png")
    assert (meta.width, meta.height) == (10, 20)
    rec = fingerprint.Manifest.load(project / "GUI2D").sheets[LAMP]
    assert "Button_50x35_2frames" in rec.derived


def test_unknown_element_is_a_usage_error(lamp_scene):
    assert reblend.cli(["render", "--element", "nope"]) == 2


def test_validate_exit_code_follows_findings(lamp_scene):
    assert reblend.cli(["validate"]) == 0          # warnings only
    assert reblend.cli(["validate", "--strict"]) == 1


def test_background_command_line(tmp_path):
    from reblend.ui import operators

    cmd = operators.background_command("/opt/blender", tmp_path / "x.blend", tmp_path,
                                       "ACTIVE", tmp_path / "r.json", element="Knob")
    assert cmd[:3] == ["/opt/blender", "-b", str(tmp_path / "x.blend")]
    assert cmd[cmd.index("--python") + 1].endswith("run_cli.py")
    assert cmd[cmd.index("--") + 1] == "render"
    assert cmd[-2:] == ["--element", "Knob"]
    assert "--dirty" in operators.background_command("b", tmp_path, tmp_path, "DIRTY", tmp_path)


def test_run_cli_script_finds_the_loaded_package():
    import runpy
    import sys
    from pathlib import Path

    script = Path(reblend.__file__).parent / "run_cli.py"
    module = runpy.run_path(str(script), run_name="not_main")
    assert module["_package"]() is sys.modules["reblend"]
