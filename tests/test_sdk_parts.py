"""Resolving the SDK's stock 2D parts (:mod:`reblend.project.sdk_parts`).

RE-Blend never renders these: Reason fixes their appearance and the GUI
design guidelines make using the Reason Studios-supplied image a requirement.
The job here is only to find the right file under whatever SDK layout the
user points at, and to copy it in under the sprite name device_2D.lua uses.
"""

import pytest

from reblend.model import kinds
from reblend.project import sdk_parts
from reblend.project.png_meta import write_rgba_png


def put_png(path, w=105, h=105):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_rgba_png(path, w, h, bytes(w * h * 4))
    return path


@pytest.fixture
def sdk_root(tmp_path):
    """An SDK laid out the way the shipped one is."""
    root = tmp_path / "SDK"
    put_png(root / "RE2DRender" / "Images" / "Cable_Attachment_Audio.png")
    put_png(root / "RE2DRender" / "Images" / "Placeholder.png", 300, 100)
    put_png(root / "RE2DRender" / "Images" / "Tape_Horizontal.png", 390, 40)
    return root


def test_every_fixed_appearance_widget_has_a_stock_part():
    """Each widget the spec says you cannot restyle must resolve to a part."""
    for widget, kind in kinds.WIDGET_KINDS.items():
        if kinds.is_sdk_supplied(kind):
            assert sdk_parts.stock_part_for_widget(widget) is not None, widget
    # ...and nothing else claims one.
    for widget in sdk_parts.STOCK_PARTS:
        assert kinds.is_sdk_supplied(kinds.WIDGET_KINDS[widget]), widget


def test_finds_part_in_the_canonical_images_folder(sdk_root):
    part = sdk_parts.stock_part_for_widget("audio_input_socket")
    found = sdk_parts.find_stock_image(part, sdk_root)
    assert found == sdk_root / "RE2DRender" / "Images" / "Cable_Attachment_Audio.png"


def test_finds_part_by_alias(tmp_path):
    """Example devices ship the same jack under their own name."""
    root = tmp_path / "SDK"
    put_png(root / "Examples" / "SilenceDetectionEffect" / "GUI2D" / "SharedAudioJack.png")
    part = sdk_parts.stock_part_for_widget("audio_output_socket")
    found = sdk_parts.find_stock_image(part, root)
    assert found is not None and found.name == "SharedAudioJack.png"


def test_prefers_the_shallowest_match(tmp_path):
    root = tmp_path / "SDK"
    deep = put_png(root / "Examples" / "Demo" / "GUI2D" / "Placeholder.png", 300, 100)
    shallow = put_png(root / "RE2DRender" / "Images" / "Placeholder.png", 300, 100)
    part = sdk_parts.stock_part_for_widget("placeholder")
    assert sdk_parts.find_stock_image(part, root) == shallow
    assert deep.is_file()  # untouched


def test_missing_part_returns_none(tmp_path):
    part = sdk_parts.stock_part_for_widget("cv_trim_knob")
    assert sdk_parts.find_stock_image(part, tmp_path) is None
    assert sdk_parts.find_stock_image(part, tmp_path / "nope") is None


def test_install_copies_under_the_projects_sprite_name(sdk_root, tmp_path):
    """device_2D.lua already names the sheet; the copy must take that name."""
    gui2d = tmp_path / "device" / "GUI2D"
    part = sdk_parts.stock_part_for_widget("audio_input_socket")
    written = sdk_parts.install_stock_part(part, sdk_root, gui2d, "SharedAudioJack")
    assert written == gui2d / "SharedAudioJack.png"
    assert written.read_bytes() == (
        sdk_root / "RE2DRender" / "Images" / "Cable_Attachment_Audio.png"
    ).read_bytes()


def test_install_without_the_part_names_the_sdk_root(tmp_path):
    part = sdk_parts.stock_part_for_widget("patch_browse_group")
    with pytest.raises(FileNotFoundError, match="patch browse group"):
        sdk_parts.install_stock_part(part, tmp_path, tmp_path / "GUI2D", "Browse")


# --- sizes and visible centre (Skou feedback #25, #28) ---------------------

def test_check_stock_size_flags_a_low_res_copy(tmp_path):
    part = sdk_parts.STOCK_PARTS["device_name"]
    good = put_png(tmp_path / "good.png", 400, 65)
    small = put_png(tmp_path / "small.png", 200, 33)
    assert sdk_parts.check_stock_size(part, good) is None
    assert "low-res" in sdk_parts.check_stock_size(part, small)


def test_visible_centre_offset_of_off_centre_art():
    import numpy as np

    alpha = np.zeros((10, 20))
    alpha[:, :10] = 1.0  # left half only
    dx, dy = sdk_parts.visible_centre_offset(alpha)
    assert dx == pytest.approx(-5.0) and dy == pytest.approx(0.0)
    assert sdk_parts.visible_centre_offset(np.zeros((4, 4))) == (0.0, 0.0)
    # A strip is cropped to its first frame.
    strip = np.zeros((20, 20))
    strip[:10, :10] = 1.0
    strip[10:, 10:] = 1.0
    assert sdk_parts.visible_centre_offset(strip, 10)[0] == pytest.approx(-5.0)


@pytest.mark.parametrize("filter_type", [0, 1, 2, 3, 4])
def test_read_png_pixels_decodes_every_row_filter(tmp_path, filter_type):
    import struct
    import zlib

    import numpy as np

    from reblend.project.png_meta import read_png_pixels

    rng = np.random.default_rng(filter_type)
    img = rng.integers(0, 256, size=(7, 5, 4), dtype=np.uint8)
    # Encode with one filter type for every row (reference encoder).
    raw = bytearray()
    prev = np.zeros(5 * 4, dtype=np.int32)
    for row in img.reshape(7, -1).astype(np.int32):
        left = np.concatenate([np.zeros(4, dtype=np.int32), row[:-4]])
        upleft = np.concatenate([np.zeros(4, dtype=np.int32), prev[:-4]])
        if filter_type == 0:
            enc = row
        elif filter_type == 1:
            enc = row - left
        elif filter_type == 2:
            enc = row - prev
        elif filter_type == 3:
            enc = row - (left + prev) // 2
        else:
            p = left + prev - upleft
            pa, pb, pc = abs(p - left), abs(p - prev), abs(p - upleft)
            pred = np.where((pa <= pb) & (pa <= pc), left, np.where(pb <= pc, prev, upleft))
            enc = row - pred
        raw.append(filter_type)
        raw += bytes((enc % 256).astype(np.uint8))
        prev = row

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 5, 7, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))
    path = tmp_path / "f.png"
    path.write_bytes(png)
    assert np.array_equal(read_png_pixels(path), img)


def test_sdk_part_size_rule_reports_a_wrong_tape(tmp_path):
    from reblend.project.layout import check_sdk_part_sizes
    from reblend.project.link import load_project

    root = tmp_path
    gui = root / "GUI2D"
    gui.mkdir()
    (gui / "device_2D.lua").write_text(
        'format_version = "2.0"\nfront = { Bg = {{ path = "Bg" }}, '
        '{ Tape = { offset = { 200, 40 }, { path = "Tape" } } } }\n'
        'back = { Bg2 = {{ path = "Bg2" }}, { VTape = { offset = { 40, 40 }, { path = "VTape" } } } }\n')
    (gui / "hdgui_2D.lua").write_text(
        'format_version = "2.0"\nfront = jbox.panel { graphics = { node = "Bg" }, widgets = {'
        ' jbox.device_name { graphics = { node = "Tape" } } } }\n'
        'back = jbox.panel { graphics = { node = "Bg2" }, widgets = {'
        ' jbox.device_name { graphics = { node = "VTape" } } } }\n')
    put_png(gui / "Tape.png", 200, 33)
    put_png(gui / "VTape.png", 65, 400)
    found = check_sdk_part_sizes(load_project(root))
    assert [(f.code, f.subject) for f in found] == [("sdk-part-size", "Tape")]
