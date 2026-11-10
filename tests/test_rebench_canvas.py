"""RE Bench phase 2: sidecar, edit operations, rendering, the canvas server,
the CLI commands and the MCP server."""

import io
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from rebench.agent import project_load, rules_check, view_render
from rebench.cli import main
from rebench.mcp_server import handle, run_stdio
from rebench.ops import OpError, apply_ops
from rebench.project import BenchProject
from rebench.render import render_svg
from rebench.server import make_handler
from rebench.sidecar import Sidecar
from rebench.profile import load_profile
from test_rebench import make_project


def _lua(root):
    return (root / "GUI2D" / "device_2D.lua").read_text()


# --- sidecar ---------------------------------------------------------------

def test_no_sidecar_is_an_empty_one(tmp_path):
    root = make_project(tmp_path)
    side = Sidecar.load(root)
    assert side.sprites == {} and side.labels == []


def test_sidecar_round_trips(tmp_path):
    root = make_project(tmp_path)
    apply_ops(root, [
        {"op": "set_frame_size", "sprite": "Knob", "w": 120, "h": 120},
        {"op": "label", "id": "l1", "panel": "front", "text": "GAIN", "x": 1000, "y": 60,
         "size_px": 48},
    ])
    data = json.loads((root / "GUI2D" / "rebench.json").read_text())
    assert data["version"] == 1
    side = Sidecar.load(root)
    assert side.sprites["Knob"].frame_w == 120
    assert side.label("l1").text == "GAIN"


def test_sidecar_frame_size_wins_over_the_png(tmp_path):
    root = make_project(tmp_path)
    apply_ops(root, [{"op": "set_frame_size", "sprite": "Knob", "w": 120, "h": 120}])
    spec = next(s for s in BenchProject.load(root).link.specs if s.path == "Knob")
    assert (spec.frame_w, spec.frame_h) == (120, 120)


# --- text-size rule --------------------------------------------------------

def test_small_functional_label_warns(tmp_path):
    root = make_project(tmp_path)
    result = apply_ops(root, [{"op": "label", "id": "l1", "panel": "front", "text": "GAIN",
                               "x": 1000, "y": 60, "size_px": 30}], dry_run=True)
    assert [f.code for f in result.added] == ["text-size"]
    assert result.added[0].source == "guide"


def test_large_or_decorative_label_does_not_warn(tmp_path):
    root = make_project(tmp_path)
    result = apply_ops(root, [
        {"op": "label", "id": "l1", "panel": "front", "text": "GAIN", "x": 1000, "y": 60,
         "size_px": 48},
        {"op": "label", "id": "l2", "panel": "front", "text": "made in NL", "x": 2000, "y": 60,
         "size_px": 20, "functional": False},
    ], dry_run=True)
    assert result.added == []


# --- edit operations -------------------------------------------------------

def test_dry_run_writes_nothing_and_reports_the_new_finding(tmp_path):
    root = make_project(tmp_path)
    before = _lua(root)
    result = apply_ops(root, [{"op": "move", "panel": "front", "node": "Knob", "x": 3620,
                               "y": 100}], dry_run=True)
    assert not result.written
    assert _lua(root) == before
    assert not (root / "GUI2D" / "rebench.json").exists()
    assert "rack-ear" in [f.code for f in result.added]


def test_apply_writes_the_offset_and_resolves(tmp_path):
    root = make_project(tmp_path, knob_x=3620)
    assert "rack-ear" in [f["code"] for f in rules_check(root)["findings"]]
    result = apply_ops(root, [{"op": "nudge", "panel": "front", "node": "Knob", "dx": -620}])
    assert result.written
    assert "{ 3000, 100 }" in _lua(root)
    assert "rack-ear" in [f.code for f in result.resolved]
    assert rules_check(root)["findings"] == []


def test_a_refused_batch_writes_nothing(tmp_path):
    root = make_project(tmp_path)
    before = _lua(root)
    with pytest.raises(OpError) as exc:
        apply_ops(root, [
            {"op": "move", "panel": "front", "node": "Knob", "x": 900, "y": 100},
            {"op": "move", "panel": "front", "node": "Missing", "x": 1, "y": 1},
        ])
    assert "Missing" in exc.value.reasons[0]
    assert _lua(root) == before


def test_set_frames_patches_lua(tmp_path):
    root = make_project(tmp_path)
    apply_ops(root, [{"op": "set_frames", "panel": "front", "node": "Knob", "frames": 31}])
    assert "frames = 31" in _lua(root)


def test_remove_label(tmp_path):
    root = make_project(tmp_path)
    apply_ops(root, [{"op": "label", "id": "l1", "panel": "front", "text": "A", "x": 0,
                      "y": 0, "size_px": 48}])
    apply_ops(root, [{"op": "remove_label", "id": "l1"}])
    assert Sidecar.load(root).labels == []
    with pytest.raises(OpError):
        apply_ops(root, [{"op": "remove_label", "id": "l1"}])


# --- rendering -------------------------------------------------------------

def test_svg_marks_reserved_areas_and_findings(tmp_path):
    root = make_project(tmp_path, knob_x=3620)
    project = BenchProject.load(root)
    profile = load_profile(root)
    findings = apply_ops(root, [], dry_run=True).check.findings
    svg = render_svg(project, "front", findings, profile)
    assert 'viewBox="0 0 3770 345"' in svg
    assert 'class="reserved"' in svg
    assert "#e5484d" in svg or "#f5a524" in svg  # the knob is outlined as a finding
    assert 'data-node="Knob"' in svg
    plain = render_svg(project, "front", findings, profile, overlays=False)
    assert 'class="reserved"' not in plain


def test_view_render_writes_svg(tmp_path):
    root = make_project(tmp_path)
    out = tmp_path / "front.svg"
    reply = view_render(root, "folded_front", out=out)
    assert out.read_text().startswith("<svg")
    assert reply["size"] == [3770, 150]


def test_view_render_rejects_unknown_panel(tmp_path):
    root = make_project(tmp_path)
    with pytest.raises(ValueError):
        view_render(root, "side")


def test_project_load_lists_nodes(tmp_path):
    root = make_project(tmp_path)
    data = project_load(root)
    knob = next(n for n in data["panels"]["front"]["nodes"] if n["node"] == "Knob")
    assert (knob["x"], knob["y"], knob["w"], knob["h"], knob["frames"]) == (1000, 100, 100, 100, 61)


# --- canvas server ---------------------------------------------------------

@pytest.fixture
def server(tmp_path):
    root = make_project(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(root))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req)


def test_server_page_state_and_sprites(server):
    root, base = server
    assert b"RE Bench" in urllib.request.urlopen(base + "/").read()
    state = json.load(urllib.request.urlopen(base + "/api/state?panel=back"))
    assert state["panel"] == "back"
    assert set(state["panels"]) == {"front", "back", "folded_front", "folded_back"}
    assert "/gui2d/Jack.png" in state["svg"]
    png = urllib.request.urlopen(base + "/gui2d/Jack.png").read()
    assert png.startswith(b"\x89PNG")
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(base + "/gui2d/..%2Fdevice_2D.lua")


def test_server_preview_does_not_write_and_write_does(server):
    root, base = server
    before = _lua(root)
    ops = [{"op": "move", "panel": "front", "node": "Knob", "x": 3620, "y": 100}]
    preview = json.load(_post(base + "/api/preview", {"ops": ops, "panel": "front"}))
    assert "rack-ear" in [f["code"] for f in preview["added"]]
    assert _lua(root) == before
    written = json.load(_post(base + "/api/write", {"ops": ops}))
    assert written["written"]
    assert "{ 3620, 100 }" in _lua(root)


def test_server_refusal_is_422(server):
    _, base = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base + "/api/write", {"ops": [{"op": "bogus"}]})
    assert exc.value.code == 422
    assert json.load(exc.value)["reasons"]


# --- CLI -------------------------------------------------------------------

def test_cli_apply_dry_run_json(tmp_path, capsys):
    root = make_project(tmp_path)
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([{"op": "nudge", "panel": "front", "node": "Knob", "dx": 2620}]))
    before = _lua(root)
    assert main(["apply", str(root), str(ops), "--dry-run", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert not out["written"] and out["added"][0]["code"] == "rack-ear"
    assert _lua(root) == before


def test_cli_apply_refused_exits_3(tmp_path, capsys):
    root = make_project(tmp_path)
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([{"op": "move", "panel": "front", "node": "Nope", "x": 0, "y": 0}]))
    assert main(["apply", str(root), str(ops)]) == 3


def test_cli_render_svg(tmp_path, capsys):
    root = make_project(tmp_path)
    out = tmp_path / "back.svg"
    assert main(["render", str(root), "--panel", "back", "--out", str(out)]) == 0
    assert out.read_text().startswith("<svg")


# --- MCP -------------------------------------------------------------------

def test_mcp_lists_the_design_operations():
    reply = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in reply["result"]["tools"]}
    assert names == {"project_load", "rules_check", "layout_dry_run", "layout_apply",
                     "view_render"}


def test_mcp_stdio_round_trip(tmp_path):
    root = make_project(tmp_path)
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "layout_dry_run",
            "arguments": {"ops": [{"op": "move", "panel": "front", "node": "Knob",
                                   "x": 3620, "y": 100}]}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "layout_apply", "arguments": {"ops": [{"op": "bogus"}]}}},
    ]
    stdin = io.StringIO("".join(json.dumps(l) + "\n" for l in lines))
    stdout = io.StringIO()
    run_stdio(default_root=str(root), stdin=stdin, stdout=stdout)
    replies = [json.loads(l) for l in stdout.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3]  # the notification gets no reply
    assert replies[0]["result"]["serverInfo"]["name"] == "rebench"
    dry = json.loads(replies[1]["result"]["content"][0]["text"])
    assert dry["added"][0]["code"] == "rack-ear" and not dry["written"]
    assert replies[2]["result"]["isError"]


def test_mcp_needs_a_project():
    reply = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "rules_check", "arguments": {}}})
    assert reply["result"]["isError"]
