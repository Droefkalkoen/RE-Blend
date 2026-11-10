"""``rebench greybox``: flat placeholder sprites, so a new device builds and
opens in Recon before any Blender work.

Every sprite node without a PNG gets a sheet of the right size and frame
count: grey panels with the reserved areas shaded, knobs whose pointer turns
across the frames, faders whose handle travels from the bottom (frame 0, as
the SDK requires), buttons and lamps that light up, and flat boxes for the
rest. SDK parts (sockets, the name tape, the placeholder) are copied from
your SDK when ``sdk_root`` is given, and otherwise drawn at their stock size.

Real art is never overwritten: each sheet this command writes is recorded in
the sidecar's ``greybox`` table with a hash of its bytes, and a PNG is redrawn
only while it still matches (a render from RE-Blend replaces it for good).
Output is 8-bit RGBA with straight alpha, standard library only.
"""

from __future__ import annotations

import hashlib
import math
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from reblend.model import calibration, kinds
from reblend.project import sdk_parts

from reblend.project.lua_reader import LuaConfigError, _execute_sandboxed

from .project import BenchProject
from .sidecar import SpriteInfo

__all__ = ["GreyboxResult", "build_greybox", "encode_png", "write_png", "STOCK_SIZES"]

#: SDK parts: (frame w, frame h, frames) of the stock hi-res images.
STOCK_SIZES = {
    "audio_input_socket": (95, 105, 3),
    "audio_output_socket": (95, 105, 3),
    "cv_input_socket": (75, 85, 3),
    "cv_output_socket": (75, 85, 3),
    "device_name": (400, 65, 1),
    "placeholder": (300, 100, 1),
}

#: Frame size by kind when neither the sidecar nor a PNG gives one.
DEFAULT_SIZES = {
    kinds.KNOB: (120, 120),
    kinds.BUTTON_TOGGLE: (100, 100),
    kinds.BUTTON_MOMENTARY: (100, 100),
    kinds.BUTTON_UPDOWN: (60, 100),
    kinds.FADER_HANDLE: (60, 240),
    kinds.SELECTOR: (60, 240),
    kinds.LAMP: (40, 40),
    kinds.DISPLAY: (400, 150),
    kinds.TEXT_BOUNDS: (200, 60),
    kinds.STATIC: (100, 100),
}

PANEL = (74, 79, 85, 255)
PANEL_DARK = (58, 62, 67, 255)
PART = (120, 126, 133, 255)
PART_LIGHT = (200, 206, 211, 255)
LIT = (255, 176, 59, 255)
SCREEN = (24, 28, 32, 255)
CLEAR = (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# A very small raster
# ---------------------------------------------------------------------------


class Canvas:
    def __init__(self, w: int, h: int, fill=CLEAR) -> None:
        self.w, self.h = w, h
        self.px = bytearray(bytes(fill) * (w * h))

    def rect(self, x0, y0, x1, y1, color) -> None:
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(self.w, int(x1)), min(self.h, int(y1))
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        for y in range(y0, y1):
            i = (y * self.w + x0) * 4
            self.px[i:i + len(row)] = row

    def circle(self, cx, cy, r, color) -> None:
        for y in range(max(0, int(cy - r)), min(self.h, int(cy + r) + 1)):
            dy = y + 0.5 - cy
            if abs(dy) > r:
                continue
            half = math.sqrt(r * r - dy * dy)
            self.rect(cx - half, y, cx + half, y + 1, color)

    def line(self, x0, y0, x1, y1, width, color) -> None:
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for s in range(steps + 1):
            t = s / steps
            self.circle(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, width / 2, color)

    def paste(self, other: "Canvas", ox: int, oy: int) -> None:
        for y in range(other.h):
            src = other.px[y * other.w * 4:(y + 1) * other.w * 4]
            i = ((oy + y) * self.w + ox) * 4
            self.px[i:i + len(src)] = src


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_png(path: Path, canvas: Canvas) -> None:
    path.write_bytes(encode_png(canvas))


def encode_png(canvas: Canvas) -> bytes:
    raw = bytearray()
    stride = canvas.w * 4
    for y in range(canvas.h):
        raw.append(0)
        raw += canvas.px[y * stride:(y + 1) * stride]

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", canvas.w, canvas.h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Drawings
# ---------------------------------------------------------------------------


def _knob(w, h, frame, frames, shape) -> Canvas:
    c = Canvas(w, h)
    r = float(shape.get("diameter_px", min(w, h) - 10)) / 2
    cx, cy = w / 2, h / 2
    c.circle(cx, cy, r, PART_LIGHT)
    c.circle(cx, cy, r - 4, PART)
    t = frame / max(1, frames - 1)
    angle = math.radians(-150 + 300 * t)  # 0 = 12 o'clock, clockwise
    px, py = cx + math.sin(angle) * (r - 10), cy - math.cos(angle) * (r - 10)
    c.line(cx, cy, px, py, max(4, r / 8), LIT)
    return c


def _fader(w, h, frame, frames) -> Canvas:
    c = Canvas(w, h)
    horizontal = w > h
    if horizontal:
        c.rect(0, h / 2 - 3, w, h / 2 + 3, PANEL_DARK)
        size = max(10, w // max(2, frames))
        t = frame / max(1, frames - 1)
        x = (w - size) * t
        c.rect(x, 2, x + size, h - 2, PART_LIGHT)
        c.rect(x + size / 2 - 2, 4, x + size / 2 + 2, h - 4, LIT)
    else:
        c.rect(w / 2 - 3, 0, w / 2 + 3, h, PANEL_DARK)
        size = max(10, h // max(2, frames))
        t = frame / max(1, frames - 1)
        y = (h - size) * (1 - t)  # frame 0: handle at the bottom
        c.rect(2, y, w - 2, y + size, PART_LIGHT)
        c.rect(4, y + size / 2 - 2, w - 4, y + size / 2 + 2, LIT)
    return c


def _button(w, h, frame, frames) -> Canvas:
    c = Canvas(w, h)
    on = frame % 2 == 1
    c.rect(0, 0, w, h, PART_LIGHT)
    c.rect(4, 4, w - 4, h - 4, LIT if on else PART)
    return c


def _lamp(w, h, frame, frames) -> Canvas:
    c = Canvas(w, h)
    t = frame / max(1, frames - 1)
    color = tuple(int(PART[i] + (LIT[i] - PART[i]) * t) for i in range(3)) + (255,)
    c.circle(w / 2, h / 2, min(w, h) / 2 - 1, color)
    return c


def _box(w, h, color=PART, border=PART_LIGHT) -> Canvas:
    c = Canvas(w, h, border)
    c.rect(3, 3, w - 3, h - 3, color)
    return c


def _jack(w, h, frame) -> Canvas:
    c = Canvas(w, h)
    c.circle(w / 2, h / 2, min(w, h) / 2 - 2, PART_LIGHT)
    c.circle(w / 2, h / 2, min(w, h) / 4, SCREEN)
    return c


def _backdrop(w, h, panel) -> Canvas:
    c = Canvas(w, h, PANEL)
    m = calibration.EDGE_MARGIN_PX
    c.rect(0, 0, m, h, PANEL_DARK)
    c.rect(w - m, 0, w, h, PANEL_DARK)
    if panel == "front":
        c.rect(0, 0, 160, h, PANEL_DARK)
        c.rect(3610, 0, w, h, PANEL_DARK)
    return c


@dataclass
class _Sheet:
    path: str
    kind: str
    frames: int
    w: int
    h: int
    widget: str = ""
    panel: str = ""
    shape: dict = field(default_factory=dict)


def _draw(sheet: _Sheet) -> Canvas:
    w, h, n = sheet.w, sheet.h, sheet.frames
    if sheet.kind == kinds.BACKDROP:
        return _backdrop(w, h, sheet.panel)
    strip = Canvas(w, h * n)
    for f in range(n):
        if sheet.widget in ("audio_input_socket", "audio_output_socket",
                            "cv_input_socket", "cv_output_socket"):
            frame = _jack(w, h, f)
        elif sheet.kind == kinds.KNOB:
            frame = _knob(w, h, f, n, sheet.shape)
        elif sheet.kind in (kinds.FADER_HANDLE, kinds.SELECTOR) and sheet.widget == "sequence_fader":
            frame = _fader(w, h, f, n)
        elif sheet.kind in (kinds.BUTTON_TOGGLE, kinds.BUTTON_MOMENTARY, kinds.BUTTON_UPDOWN):
            frame = _button(w, h, f, n)
        elif sheet.kind == kinds.LAMP:
            frame = _lamp(w, h, f, n)
        elif sheet.kind == kinds.DISPLAY:
            frame = _box(w, h, SCREEN)
        else:
            frame = _box(w, h)
        strip.paste(frame, 0, f * h)
    return strip


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


@dataclass
class GreyboxResult:
    drawn: list[str] = field(default_factory=list)
    copied: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)       # real art, left alone
    sized: list[str] = field(default_factory=list)      # frame size filled in the sidecar

    def to_dict(self) -> dict:
        return {"drawn": self.drawn, "copied": self.copied, "kept": self.kept,
                "sized": self.sized}


def _height_ru(root: Path) -> int:
    info = root / "info.lua"
    if info.is_file():
        try:
            ru = _execute_sandboxed(info).get("device_height_ru")
            if isinstance(ru, (int, float)) and ru >= 1:
                return int(ru)
        except LuaConfigError:
            pass
    return 1


def build_greybox(root: Path | str, sdk_root: Path | str | None = None,
                  dry_run: bool = False) -> GreyboxResult:
    root = Path(root)
    project = BenchProject.load(root)
    sidecar = project.sidecar
    gui2d = project.link.gui2d_dir
    ru = _height_ru(root)
    result = GreyboxResult()
    owned = dict(sidecar.greybox)

    widget_of: dict[str, str] = {}
    for spec in project.link.specs:
        for p in spec.placements:
            hd = project.link.hdgui.panels.get(p.panel)
            for wdg in (hd.widgets if hd else []):
                if wdg.node == p.node:
                    widget_of.setdefault(spec.path, wdg.kind)

    for spec in project.link.specs:
        png = gui2d / f"{spec.path}.png"
        if png.exists() and owned.get(spec.path) != _digest(png.read_bytes()):
            owned.pop(spec.path, None)  # real art now: forget the placeholder
            result.kept.append(spec.path)
            continue
        widget = widget_of.get(spec.path, "")
        panel = spec.placements[0].panel if spec.placements else ""

        if widget in STOCK_SIZES and sdk_root:
            part = sdk_parts.stock_part_for_widget(widget)
            if part and sdk_parts.find_stock_image(part, sdk_root):
                if not dry_run:
                    sdk_parts.install_stock_part(part, sdk_root, gui2d, spec.path)
                    owned.pop(spec.path, None)  # real SDK art now
                result.copied.append(spec.path)
                continue

        if spec.kind == kinds.BACKDROP:
            w = calibration.PANEL_WIDTH_PX
            h = (calibration.FOLDED_HEIGHT_PX if calibration.is_folded(panel)
                 else ru * calibration.UNIT_HEIGHT_PX)
        elif widget in STOCK_SIZES:
            w, h, _ = STOCK_SIZES[widget]
        elif spec.frame_w and spec.frame_h:
            w, h = spec.frame_w, spec.frame_h
        else:
            w, h = DEFAULT_SIZES.get(spec.kind, (100, 100))
            result.sized.append(spec.path)
            if not dry_run:
                info = sidecar.sprites.setdefault(spec.path, SpriteInfo())
                info.frame_w, info.frame_h = w, h
        sheet = _Sheet(spec.path, spec.kind, max(1, spec.frames), w, h, widget, panel,
                       spec.shape)
        if not dry_run:
            data = encode_png(_draw(sheet))
            png.write_bytes(data)
            owned[spec.path] = _digest(data)
        result.drawn.append(spec.path)

    if not dry_run:
        sidecar.greybox = owned
        sidecar.save(root)
    return result
