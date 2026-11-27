"""Minimal pure-Python PNG metadata reading and RGBA writing.

The validation report (§6.3) needs to check a written sheet's dimensions and
bit depth against ``re_frame_w × re_frame_h × re_frames`` without depending on
an image library — Blender is not importable in CI and Pillow is not a
dependency. A PNG's ``IHDR`` chunk is 13 bytes at a fixed offset, so reading
it directly is trivial and total.

The writer emits the exact flavour the SDK consumes (§1, §5.2): 8-bit RGBA
with straight alpha (PNG's only alpha semantics — unassociated by
specification). It exists for test fixtures and placeholder art; the real
render path writes through Blender and *verifies* with :func:`read_png_meta`.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["PngError", "PngMeta", "read_png_meta", "write_rgba_png"]

_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: PNG colour types (spec §11.2.2).
COLOR_TYPE_RGB = 2
COLOR_TYPE_RGBA = 6


class PngError(Exception):
    """A file is not a readable PNG."""


@dataclass(frozen=True)
class PngMeta:
    """The IHDR facts validation cares about."""

    width: int
    height: int
    bit_depth: int
    color_type: int

    @property
    def is_8bit_rgba(self) -> bool:
        return self.bit_depth == 8 and self.color_type == COLOR_TYPE_RGBA


def read_png_meta(path: Path | str) -> PngMeta:
    """Read a PNG's IHDR without decoding pixel data."""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            head = fh.read(len(_SIGNATURE) + 8 + 13)
    except OSError as exc:
        raise PngError(f"{path}: cannot read file: {exc}") from exc

    if len(head) < len(_SIGNATURE) + 8 + 13 or not head.startswith(_SIGNATURE):
        raise PngError(f"{path}: not a PNG file")
    length, chunk_type = struct.unpack(">I4s", head[8:16])
    if chunk_type != b"IHDR" or length != 13:
        raise PngError(f"{path}: malformed PNG (IHDR not first chunk)")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", head[16:26])
    if width == 0 or height == 0:
        raise PngError(f"{path}: zero-sized image")
    return PngMeta(width=width, height=height, bit_depth=bit_depth, color_type=color_type)


def write_rgba_png(path: Path | str, width: int, height: int, pixels: bytes) -> None:
    """Write an 8-bit straight-alpha RGBA PNG.

    ``pixels`` is top-down row-major RGBA, ``width * height * 4`` bytes.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    expected = width * height * 4
    if len(pixels) != expected:
        raise ValueError(f"expected {expected} bytes of RGBA pixels, got {len(pixels)}")

    stride = width * 4
    raw = b"".join(
        b"\x00" + pixels[row * stride : (row + 1) * stride] for row in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, COLOR_TYPE_RGBA, 0, 0, 0)
    data = (
        _SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )
    Path(path).write_bytes(data)


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    )


def read_png_pixels(path: Path | str):
    """Decode an 8-bit, non-interlaced PNG to a top-down ``(H, W, 4)`` uint8
    RGBA array (grey, grey+alpha, RGB and RGBA; all five row filters).

    Enough for reading SDK stock parts and sprite sheets outside Blender
    (RE Bench's visible-centre alignment); refuses anything else.
    """
    import zlib

    import numpy as np

    data = Path(path).read_bytes()
    if data[:8] != _SIGNATURE:
        raise PngError(f"{path}: not a PNG")
    width, height, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", data[16:29])
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
    if depth != 8 or channels is None or interlace:
        raise PngError(f"{path}: only 8-bit non-interlaced grey/RGB(A) PNGs are read here")
    idat, i = bytearray(), 8
    while i < len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        if data[i + 4:i + 8] == b"IDAT":
            idat += data[i + 8:i + 8 + n]
        i += 12 + n
    raw = np.frombuffer(zlib.decompress(bytes(idat)), dtype=np.uint8)
    stride = width * channels
    rows = raw.reshape(height, stride + 1)
    out = np.zeros((height, stride), dtype=np.int32)
    prev = np.zeros(stride, dtype=np.int32)
    for y in range(height):
        kind, line = rows[y, 0], rows[y, 1:].astype(np.int32)
        if kind == 0:
            cur = line
        elif kind == 2:
            cur = (line + prev) & 0xFF
        elif kind in (1, 3, 4):
            cur = np.zeros(stride, dtype=np.int32)
            for x in range(stride):
                a = cur[x - channels] if x >= channels else 0
                b = prev[x]
                if kind == 1:
                    pred = a
                elif kind == 3:
                    pred = (a + b) >> 1
                else:
                    c = prev[x - channels] if x >= channels else 0
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                cur[x] = (line[x] + pred) & 0xFF
        else:
            raise PngError(f"{path}: bad filter type {kind} on row {y}")
        out[y] = cur
        prev = cur
    pixels = out.reshape(height, width, channels).astype(np.uint8)
    if channels == 4:
        return pixels
    rgba = np.full((height, width, 4), 255, dtype=np.uint8)
    if channels == 1:
        rgba[..., :3] = pixels
    elif channels == 2:
        rgba[..., :3] = pixels[..., :1]
        rgba[..., 3] = pixels[..., 1]
    else:
        rgba[..., :3] = pixels
    return rgba
