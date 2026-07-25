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
