import pytest

from reblend.project.palette import PaletteError, read_palette, srgb_to_linear


def test_json_dict_and_list(tmp_path):
    a = tmp_path / "a.json"
    a.write_text('{"Amber Signal": "#e0a24b", "Graphite": "1A2026"}')
    assert [(s.name, s.hex) for s in read_palette(a)] == [
        ("Amber Signal", "#E0A24B"), ("Graphite", "#1A2026")]
    b = tmp_path / "b.json"
    b.write_text('[{"name": "Fog", "hex": "#D0D8DC"}]')
    assert read_palette(b)[0].srgb == pytest.approx((0xD0 / 255, 0xD8 / 255, 0xDC / 255))


def test_text_lines_like_theme_colors(tmp_path):
    p = tmp_path / "theme_colors.txt"
    p.write_text("Brand palette\n\nGraphite      #1A2026   page background\n"
                 "| Amber Signal | `#E0A24B` | the accent |\n")
    assert [(s.name, s.hex) for s in read_palette(p)] == [
        ("Graphite", "#1A2026"), ("Amber Signal", "#E0A24B")]


def test_errors(tmp_path):
    p = tmp_path / "p.json"
    p.write_text('{"A": "red"}')
    with pytest.raises(PaletteError):
        read_palette(p)
    p.write_text("[]")
    with pytest.raises(PaletteError):
        read_palette(p)
    with pytest.raises(PaletteError):
        read_palette(tmp_path / "missing.json")


def test_linear_conversion():
    assert srgb_to_linear(0.0) == 0.0
    assert srgb_to_linear(1.0) == pytest.approx(1.0)
    assert srgb_to_linear(0.5) == pytest.approx(0.21404, abs=1e-4)
