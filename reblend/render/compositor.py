"""Panel compositing and QA sheets: preview the layout before export (§5.3, §5.4).

Two pure-numpy builders over the same frame data the stitcher handles:

- :func:`composite_panel` — alpha-over each element's chosen frame at its
  panel-pixel offset onto a canvas, the "does the layout read?" check that
  mirrors RE2DPreview but runs pre-export and per-state (the state playground
  picks the frame per element).
- :func:`contact_sheet` — all frames of one strip laid out as a grid for
  at-a-glance QA of multi-state controls and sweep smoothness.

Frames are ``(H, W, 4)`` float straight-alpha RGBA in top-down row order,
same convention as :mod:`reblend.render.stitcher`. Pure numpy on purpose: the
geometry and the over-operator are exercised by the CI suite without Blender.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .stitcher import StitchError, split_strip

__all__ = ["CompositeLayer", "alpha_over", "composite_panel", "contact_sheet"]


@dataclass(frozen=True)
class CompositeLayer:
    """One strip placed on the panel: which frame to show, and where.

    ``x``/``y`` are the frame's top-left corner in panel px (the device_2D
    ``offset`` convention). ``frame`` indexes into the strip, clamped by the
    caller's data — a frame outside the strip is an error, not a wrap.
    """

    strip: np.ndarray
    frame_h: int
    frame: int = 0
    x: float = 0.0
    y: float = 0.0

    def frame_pixels(self) -> np.ndarray:
        frames = split_strip(np.asarray(self.strip, dtype=np.float32), self.frame_h)
        if not 0 <= self.frame < len(frames):
            raise StitchError(
                f"frame {self.frame} out of range — strip has {len(frames)} frames"
            )
        return frames[self.frame]


def alpha_over(canvas: np.ndarray, layer: np.ndarray, x: int, y: int) -> None:
    """Straight-alpha *over* of ``layer`` onto ``canvas`` at (x, y), in place.

    Both are straight-alpha RGBA; the result stays straight-alpha (colour is
    the coverage-weighted average, not a premultiplied sum). Regions falling
    outside the canvas are clipped, matching what a physical panel would show.
    """
    ch, cw = canvas.shape[0], canvas.shape[1]
    lh, lw = layer.shape[0], layer.shape[1]
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + lw, cw), min(y + lh, ch)
    if x0 >= x1 or y0 >= y1:
        return

    src = np.asarray(layer, dtype=np.float32)[y0 - y : y1 - y, x0 - x : x1 - x]
    dst = canvas[y0:y1, x0:x1]

    src_a = src[..., 3:4]
    dst_a = dst[..., 3:4]
    out_a = src_a + dst_a * (1.0 - src_a)
    out_rgb = src[..., :3] * src_a + dst[..., :3] * dst_a * (1.0 - src_a)
    np.divide(out_rgb, out_a, out=out_rgb, where=out_a > 0.0)

    dst[..., :3] = out_rgb
    dst[..., 3:4] = out_a


def composite_panel(
    width: int, height: int, layers: Sequence[CompositeLayer]
) -> np.ndarray:
    """Composite layers (backdrop first) onto a ``width × height`` canvas.

    The caller orders the layers; painting order is list order, so backdrops
    go first exactly as they sit lowest in Reason. Offsets are rounded to
    whole pixels — panel placement is integral in the RE contract.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"canvas must be positive, got {width}x{height}")
    canvas = np.zeros((height, width, 4), dtype=np.float32)
    for layer in layers:
        alpha_over(canvas, layer.frame_pixels(), round(layer.x), round(layer.y))
    return canvas


def half_size(canvas: np.ndarray) -> np.ndarray:
    """A 2x2 box downscale in premultiplied space: roughly what the 0.5x asset
    set looks like (RE2DRender makes the real one; this is only a preview).
    An odd last row or column is dropped."""
    h, w = canvas.shape[0] // 2 * 2, canvas.shape[1] // 2 * 2
    c = canvas[:h, :w].astype(np.float32)
    pre = c.copy()
    pre[..., :3] *= c[..., 3:4]
    small = pre.reshape(h // 2, 2, w // 2, 2, 4).mean(axis=(1, 3))
    alpha = small[..., 3:4]
    out = small.copy()
    out[..., :3] = np.divide(small[..., :3], alpha, out=np.zeros_like(small[..., :3]),
                             where=alpha > 0)
    return out


def contact_sheet(
    strip: np.ndarray, frame_h: int, columns: int = 0, gap: int = 4
) -> np.ndarray:
    """Lay a strip's frames out as a grid, row-major, frame 0 top-left (§5.4).

    ``columns = 0`` picks a near-square grid. The ``gap`` between cells stays
    fully transparent so frame borders are unmistakable — an overflow that
    bleeds into the gap is visible instead of merging into the neighbour.
    """
    frames = split_strip(np.asarray(strip, dtype=np.float32), frame_h)
    count = len(frames)
    if columns <= 0:
        columns = math.ceil(math.sqrt(count))
    columns = min(columns, count)
    rows = math.ceil(count / columns)
    if gap < 0:
        raise ValueError(f"gap must be >= 0, got {gap}")

    frame_w = frames[0].shape[1]
    sheet = np.zeros(
        (
            rows * frame_h + (rows - 1) * gap,
            columns * frame_w + (columns - 1) * gap,
            4,
        ),
        dtype=np.float32,
    )
    for index, frame in enumerate(frames):
        row, col = divmod(index, columns)
        top = row * (frame_h + gap)
        left = col * (frame_w + gap)
        sheet[top : top + frame_h, left : left + frame_w] = frame
    return sheet
