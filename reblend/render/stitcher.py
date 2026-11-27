"""Strip stitching: frames → one vertical sprite sheet (§5.1, §5.2).

Strip geometry is computed, never manual: height is always
``frame_h × frames`` and order is always frame 0 on top. Frames are
``(H, W, 4)`` float RGBA arrays in **top-down** row order (the ``bpy`` I/O
layer flips Blender's bottom-up pixel buffers before they get here).

Pure numpy on purpose — the stitcher is exercised by the CI test suite with
synthetic frames, so a geometry regression can never depend on having
Blender around to notice.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

__all__ = ["StitchError", "stitch", "unpremultiply", "split_strip", "frame_height",
           "crop_frames", "alpha_floor"]


class StitchError(Exception):
    """The frames cannot form a legal strip."""


def stitch(frames: Sequence[np.ndarray]) -> np.ndarray:
    """Stack frames into a vertical strip, frame 0 on top."""
    if not frames:
        raise StitchError("no frames to stitch")
    shape = frames[0].shape
    if len(shape) != 3 or shape[2] != 4:
        raise StitchError(f"frames must be (H, W, 4) RGBA arrays, got {shape}")
    for index, frame in enumerate(frames):
        if frame.shape != shape:
            raise StitchError(
                f"frame {index} is {frame.shape[1]}x{frame.shape[0]}, "
                f"expected {shape[1]}x{shape[0]} — all frames must match"
            )
    return np.concatenate([np.asarray(f, dtype=np.float32) for f in frames], axis=0)


def frame_height(strip_height: int, frames: int) -> int | None:
    """Per-frame height implied by a strip, or None when the contract fails.

    The single authority on "strip height = frameHeight × frameCount" for
    consumers that only have the sheet and a claimed frame count: returns a
    positive height only when ``frames >= 1`` and it divides exactly.
    """
    if frames < 1:
        return None
    height, remainder = divmod(strip_height, frames)
    return height if remainder == 0 and height > 0 else None


def split_strip(strip: np.ndarray, frame_h: int) -> list[np.ndarray]:
    """Inverse of :func:`stitch`: views of each frame, top-down order."""
    height = strip.shape[0]
    if frame_h <= 0 or height % frame_h != 0:
        raise StitchError(f"strip height {height} is not a multiple of frame_h {frame_h}")
    return [strip[top : top + frame_h] for top in range(0, height, frame_h)]


def unpremultiply(pixels: np.ndarray) -> np.ndarray:
    """Convert premultiplied RGBA to straight alpha (risk §10.1 fallback).

    Blender composites premultiplied internally; if a build's PNG path leaks
    associated alpha into the file, dividing colour by coverage restores the
    straight-alpha semantics the SDK requires. Fully transparent pixels keep
    RGB 0. Values are clamped to [0, 1] — with true premultiplied input every
    channel is <= alpha, so clamping only trims float noise.
    """
    pixels = np.asarray(pixels, dtype=np.float32)
    alpha = pixels[..., 3:4]
    out = pixels.copy()
    np.divide(pixels[..., :3], alpha, out=out[..., :3], where=alpha > 0.0)
    np.clip(out[..., :3], 0.0, 1.0, out=out[..., :3])
    return out


def crop_frames(frames: Sequence[np.ndarray], rect: tuple[int, int, int, int]
                ) -> list[np.ndarray]:
    """Cut the same ``(x, y, w, h)`` rectangle out of every frame (a derived
    sheet, Skou feedback #38). Refuses a rectangle outside the frame."""
    x, y, w, h = (int(v) for v in rect)
    if not frames:
        raise StitchError("no frames to crop")
    fh, fw = frames[0].shape[0], frames[0].shape[1]
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > fw or y + h > fh:
        raise StitchError(f"crop {[x, y, w, h]} does not fit inside a {fw}x{fh} frame")
    return [np.ascontiguousarray(f[y:y + h, x:x + w]) for f in frames]


def alpha_floor(pixels: np.ndarray, floor: float) -> np.ndarray:
    """Remove a faint alpha haze: coverage below ``floor`` becomes 0 and the
    rest is stretched back to 0..1 (Skou feedback #19).

    A shadow catcher picks up 2 to 6 % alpha from ambient occlusion across the
    whole frame, so the sheet's rectangle shows as a faint edge on the panel.
    Straight alpha means colour is untouched; only coverage changes.
    """
    if floor <= 0.0:
        return pixels
    if floor >= 1.0:
        raise StitchError("alpha floor must be below 1")
    out = np.array(pixels, dtype=np.float32, copy=True)
    alpha = out[..., 3]
    alpha[:] = np.clip((alpha - floor) / (1.0 - floor), 0.0, 1.0)
    out[..., :3][alpha <= 0.0] = 0.0
    return out
