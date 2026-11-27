"""Panel compositing and contact sheets (§5.3, §5.4): geometry and the
straight-alpha over operator, without Blender."""

import numpy as np
import pytest

from reblend.render import compositor
from reblend.render.compositor import CompositeLayer, alpha_over, composite_panel, contact_sheet
from reblend.render.stitcher import StitchError, stitch


def solid(h, w, rgba):
    frame = np.zeros((h, w, 4), dtype=np.float32)
    frame[:] = rgba
    return frame


# ---------------------------------------------------------------------------
# alpha_over
# ---------------------------------------------------------------------------


def test_opaque_layer_replaces_canvas():
    canvas = solid(4, 4, (0.2, 0.2, 0.2, 1.0))
    alpha_over(canvas, solid(2, 2, (1.0, 0.0, 0.0, 1.0)), 1, 1)
    assert np.allclose(canvas[1:3, 1:3], (1.0, 0.0, 0.0, 1.0))
    assert np.allclose(canvas[0, 0], (0.2, 0.2, 0.2, 1.0))


def test_transparent_layer_changes_nothing():
    canvas = solid(4, 4, (0.2, 0.4, 0.6, 1.0))
    before = canvas.copy()
    alpha_over(canvas, solid(4, 4, (1.0, 1.0, 1.0, 0.0)), 0, 0)
    assert np.array_equal(canvas, before)


def test_half_alpha_blends_straight():
    canvas = solid(1, 1, (0.0, 0.0, 0.0, 1.0))
    alpha_over(canvas, solid(1, 1, (1.0, 1.0, 1.0, 0.5)), 0, 0)
    # straight-alpha over on opaque black: rgb = 0.5, alpha = 1
    assert np.allclose(canvas[0, 0], (0.5, 0.5, 0.5, 1.0))


def test_over_transparent_canvas_keeps_straight_colour():
    # Compositing onto nothing must not premultiply the stored colour.
    canvas = np.zeros((1, 1, 4), dtype=np.float32)
    alpha_over(canvas, solid(1, 1, (1.0, 0.5, 0.25, 0.5)), 0, 0)
    assert np.allclose(canvas[0, 0], (1.0, 0.5, 0.25, 0.5))


def test_layer_clips_at_canvas_edges():
    canvas = np.zeros((4, 4, 4), dtype=np.float32)
    alpha_over(canvas, solid(3, 3, (1.0, 1.0, 1.0, 1.0)), 2, 2)
    assert np.allclose(canvas[2:4, 2:4, 3], 1.0)
    assert np.allclose(canvas[:2, :, 3], 0.0)
    # fully outside: a no-op, not an exception
    alpha_over(canvas, solid(2, 2, (1, 1, 1, 1.0)), 10, 10)
    alpha_over(canvas, solid(2, 2, (1, 1, 1, 1.0)), -5, -5)


# ---------------------------------------------------------------------------
# composite_panel
# ---------------------------------------------------------------------------


def strip_of(colors, h=2, w=2):
    return stitch([solid(h, w, c) for c in colors])


def test_composite_places_the_chosen_frame_at_its_offset():
    strip = strip_of([(1, 0, 0, 1), (0, 1, 0, 1)])
    canvas = composite_panel(
        8, 6, [CompositeLayer(strip, frame_h=2, frame=1, x=3, y=2)]
    )
    assert canvas.shape == (6, 8, 4)
    assert np.allclose(canvas[2:4, 3:5], (0, 1, 0, 1))
    assert np.allclose(canvas[0, 0], 0.0)


def test_layer_order_paints_backdrop_first():
    backdrop = strip_of([(0.1, 0.1, 0.1, 1.0)], h=4, w=4)
    lamp = strip_of([(1, 0, 0, 1)], h=2, w=2)
    canvas = composite_panel(
        4, 4,
        [CompositeLayer(backdrop, 4), CompositeLayer(lamp, 2, 0, 1, 1)],
    )
    assert np.allclose(canvas[1:3, 1:3], (1, 0, 0, 1))
    assert np.allclose(canvas[0, 0], (0.1, 0.1, 0.1, 1.0))


def test_frame_out_of_range_raises():
    strip = strip_of([(1, 1, 1, 1)])
    with pytest.raises(StitchError, match="out of range"):
        composite_panel(4, 4, [CompositeLayer(strip, 2, frame=1)])


def test_bad_canvas_raises():
    with pytest.raises(ValueError):
        composite_panel(0, 4, [])


def test_offsets_round_to_pixels():
    strip = strip_of([(1, 1, 1, 1)])
    canvas = composite_panel(6, 6, [CompositeLayer(strip, 2, 0, 1.6, 0.4)])
    assert np.allclose(canvas[0:2, 2:4, 3], 1.0)


# ---------------------------------------------------------------------------
# contact_sheet
# ---------------------------------------------------------------------------


def test_contact_sheet_geometry_near_square():
    frames = [solid(5, 10, (i / 61, 0, 0, 1)) for i in range(61)]
    sheet = contact_sheet(stitch(frames), frame_h=5, gap=4)
    # 61 frames -> 8 columns, 8 rows
    assert sheet.shape == (8 * 5 + 7 * 4, 8 * 10 + 7 * 4, 4)


def test_contact_sheet_row_major_order_and_gap():
    frames = [solid(2, 2, (i / 3, 0, 0, 1)) for i in range(4)]
    sheet = contact_sheet(stitch(frames), frame_h=2, columns=2, gap=1)
    assert sheet.shape == (5, 5, 4)
    assert np.allclose(sheet[0, 0, 0], 0.0)        # frame 0 top-left
    assert np.allclose(sheet[0, 3, 0], 1 / 3)      # frame 1 right of it
    assert np.allclose(sheet[3, 0, 0], 2 / 3)      # frame 2 next row
    assert np.allclose(sheet[3, 3, 0], 1.0)        # frame 3
    assert np.allclose(sheet[2, :, 3], 0.0)        # gap row fully transparent
    assert np.allclose(sheet[:, 2, 3], 0.0)        # gap column too


def test_contact_sheet_single_frame():
    sheet = contact_sheet(solid(3, 4, (1, 1, 1, 1)), frame_h=3)
    assert sheet.shape == (3, 4, 4)


def test_contact_sheet_columns_capped_to_frame_count():
    frames = [solid(2, 2, (1, 1, 1, 1)) for _ in range(3)]
    sheet = contact_sheet(stitch(frames), frame_h=2, columns=10, gap=0)
    assert sheet.shape == (2, 6, 4)


def test_contact_sheet_rejects_negative_gap():
    with pytest.raises(ValueError, match="gap"):
        contact_sheet(solid(2, 2, (1, 1, 1, 1)), frame_h=2, gap=-1)


def test_module_exports():
    assert set(compositor.__all__) == {
        "CompositeLayer", "alpha_over", "composite_panel", "contact_sheet"
    }


def test_half_size_box_filters_in_premultiplied_space():
    import numpy as np

    from reblend.render.compositor import half_size

    canvas = np.zeros((4, 5, 4), dtype=np.float32)
    canvas[0, 0] = (1.0, 0.0, 0.0, 1.0)   # one red pixel in the first 2x2 block
    small = half_size(canvas)
    assert small.shape == (2, 2, 4)
    assert small[0, 0].tolist() == pytest.approx([1.0, 0.0, 0.0, 0.25])
    assert small[1, 1].tolist() == [0.0, 0.0, 0.0, 0.0]
