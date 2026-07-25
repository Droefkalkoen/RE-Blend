"""World calibration math (§4.4) and the SDK panel geometry constants."""

import pytest

from reblend.model import calibration as cal


def test_panel_sizes():
    assert cal.panel_size_px("front", rack_units=2) == cal.PanelSize(3770, 690)
    assert cal.panel_size_px("back", rack_units=1) == cal.PanelSize(3770, 345)
    # Folded panels are a fixed 150 px regardless of rack height — a stated
    # requirement of the GUI design guidelines, not something RE2DRender
    # enforces (it echoes back whatever backdrop size it is handed).
    assert cal.panel_size_px("folded_front", rack_units=3) == cal.PanelSize(3770, 150)
    assert cal.panel_size_px("folded_back") == cal.PanelSize(3770, 150)


def test_bad_rack_units_raise():
    with pytest.raises(ValueError):
        cal.panel_size_px("front", rack_units=0)


def test_rack_units_from_backdrop_height():
    assert cal.rack_units_for_height(345) == 1
    assert cal.rack_units_for_height(690) == 2
    assert cal.rack_units_for_height(700) is None
    assert cal.rack_units_for_height(0) is None


def test_ortho_scale_frames_the_longer_side():
    assert cal.ortho_scale(65, 65, ppb=100.0) == pytest.approx(0.65)
    assert cal.ortho_scale(25, 60, ppb=100.0) == pytest.approx(0.60)


def test_ortho_scale_rejects_degenerate_input():
    with pytest.raises(ValueError):
        cal.ortho_scale(0, 65)
    with pytest.raises(ValueError):
        cal.ortho_scale(65, 65, ppb=0)


def test_world_roundtrip():
    world = cal.panel_px_to_world(950, 120, ppb=100.0)
    assert world == pytest.approx((9.5, 0.0, -1.2))
    assert cal.world_to_panel_px(world, ppb=100.0) == pytest.approx((950, 120))


def test_origin_offset_modes():
    # top-left is the identity; centre/top-centre shift by half the panel
    assert cal.origin_offset_px(cal.ORIGIN_TOP_LEFT, 3770, 690) == (0.0, 0.0)
    assert cal.origin_offset_px(cal.ORIGIN_TOP_CENTER, 3770, 690) == (1885.0, 0.0)
    assert cal.origin_offset_px(cal.ORIGIN_CENTER, 3770, 690) == (1885.0, 345.0)
    # unknown mode degrades to the identity offset
    assert cal.origin_offset_px("nonsense", 3770, 690) == (0.0, 0.0)


def test_world_origin_shifts_placement():
    # the panel centre maps to the world origin under ORIGIN_CENTER
    origin = cal.origin_offset_px(cal.ORIGIN_CENTER, 3770, 690)
    assert cal.panel_px_to_world(1885, 345, ppb=100.0, origin=origin) == \
        pytest.approx((0.0, 0.0, 0.0))
    # and the round trip still recovers the original panel pixel
    world = cal.panel_px_to_world(950, 120, ppb=100.0, origin=origin)
    assert cal.world_to_panel_px(world, ppb=100.0, origin=origin) == \
        pytest.approx((950, 120))


def test_axis_vector():
    assert cal.axis_vector("neg_y") == (0.0, -1.0, 0.0)
    assert cal.axis_vector("pos_z") == (0.0, 0.0, 1.0)
    assert cal.axis_vector("pos_x") == (1.0, 0.0, 0.0)
    # unknown names fall back to the default front-view axis
    assert cal.axis_vector("bogus") == cal.axis_vector(cal.DEFAULT_CAMERA_AXIS)
    # every named axis feeds dominant_axis cleanly (used by the knob rig)
    for name in cal.AXIS_VECTORS:
        index, sign = cal.dominant_axis(cal.axis_vector(name))
        assert 0 <= index <= 2 and sign in (1.0, -1.0)


def test_dominant_axis():
    assert cal.dominant_axis((0.0, -1.0, 0.0)) == (1, -1.0)
    assert cal.dominant_axis((0.0, 0.1, 0.9)) == (2, 1.0)
    assert cal.dominant_axis((-1.0, 0.0, 0.0)) == (0, -1.0)
    with pytest.raises(ValueError):
        cal.dominant_axis((0.0, 0.0, 0.0))


def test_element_center():
    # device_2D offset is the frame's top-left; registration sits at centre
    assert cal.element_center_px(950, 120, 65, 65) == (982.5, 152.5)


def test_element_offset_inverts_center():
    # import places by centre, export recovers the offset — exact round trip
    assert cal.element_offset_px(982.5, 152.5, 65, 65) == (950, 120)
    for offset in ((0, 0), (1885, 172), (37, 41)):
        center = cal.element_center_px(*offset, 25, 60)
        assert cal.element_offset_px(*center, 25, 60) == offset
