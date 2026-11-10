"""Knob driver direction (Skou feedback #17): frame 0 at the minimum, about
7 o'clock, turning clockwise on screen as the frame rises, for any camera axis."""

import math

import pytest

from reblend.model import calibration as cal


def _rot(index, angle, v):
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = v
    if index == 0:
        return (x, y * c - z * s, y * s + z * c)
    if index == 1:
        return (x * c + z * s, y, -x * s + z * c)
    return (x * c - y * s, x * s + y * c, z)


# For each camera axis (panel -> viewer): the world "up" and "right" the viewer sees.
_VIEWS = {
    "neg_y": ((0, 0, 1), (1, 0, 0)),   # Blender front view
    "pos_y": ((0, 0, 1), (-1, 0, 0)),  # back view
    "pos_z": ((0, 1, 0), (1, 0, 0)),   # top view
    "neg_z": ((0, 1, 0), (-1, 0, 0)),  # bottom view
    "pos_x": ((0, 0, 1), (0, 1, 0)),   # right view
    "neg_x": ((0, 0, 1), (0, -1, 0)),  # left view
}


def _clock_deg(axis_name, frame, frames=61):
    """Screen angle of a pointer that starts at 12 o'clock, clockwise from 12."""
    up, right = _VIEWS[axis_name]
    index, expr = cal.turntable_expression(frames, 300.0, cal.axis_vector(axis_name))
    angle = eval(expr, {"radians": math.radians, "frame": frame})
    p = _rot(index, angle, up)
    sx = sum(a * b for a, b in zip(p, right))
    sy = sum(a * b for a, b in zip(p, up))
    return math.degrees(math.atan2(sx, sy))


@pytest.mark.parametrize("axis_name", sorted(_VIEWS))
def test_frame0_at_seven_oclock_and_clockwise(axis_name):
    assert _clock_deg(axis_name, 0) == pytest.approx(-150.0)
    assert _clock_deg(axis_name, 30) == pytest.approx(0.0, abs=1e-9)
    assert _clock_deg(axis_name, 60) == pytest.approx(150.0)
    assert _clock_deg(axis_name, 31) > _clock_deg(axis_name, 30)


def test_default_axis_matches_the_pilots_hand_fix():
    index, expr = cal.turntable_expression(61)
    assert index == 1
    assert expr == "radians(1.0 * (-150.0 + 300.0 * frame / 60))"


def test_needs_two_frames():
    with pytest.raises(ValueError):
        cal.turntable_expression(1)
