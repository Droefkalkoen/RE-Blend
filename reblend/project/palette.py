"""The project palette (design §5.7): named sRGB swatches, read from a file.

Accepted formats, by extension:

- ``.json``: ``{"Amber Signal": "#E0A24B", ...}`` or
  ``[{"name": "Amber Signal", "hex": "#E0A24B"}, ...]``;
- anything else: text lines holding a name and a ``#RRGGBB`` value, such as
  ``Amber Signal  #E0A24B  the one accent`` (Terp Audio's ``theme_colors.txt``
  shape); lines without a hex value are skipped.

The convention is ``Design/palette.json``; any path works. Pure Python: the
Blender side turns the swatches into a node group (``model.kit``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Swatch", "PaletteError", "DEFAULT_PALETTE", "read_palette", "srgb_to_linear"]

DEFAULT_PALETTE = "Design/palette.json"
_HEX = re.compile(r"#([0-9A-Fa-f]{6})\b")


class PaletteError(ValueError):
    """The palette file cannot be read."""


@dataclass(frozen=True)
class Swatch:
    name: str
    hex: str  # "#RRGGBB", upper case

    @property
    def srgb(self) -> tuple[float, float, float]:
        value = int(self.hex[1:], 16)
        return ((value >> 16) / 255.0, ((value >> 8) & 0xFF) / 255.0, (value & 0xFF) / 255.0)

    @property
    def linear(self) -> tuple[float, float, float]:
        """Scene-linear RGB, what a Blender colour socket stores."""
        return tuple(srgb_to_linear(c) for c in self.srgb)  # type: ignore[return-value]


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _hex(value: str, where: str) -> str:
    match = _HEX.search(str(value)) or re.fullmatch(r"([0-9A-Fa-f]{6})", str(value).strip())
    if not match:
        raise PaletteError(f"{where}: {value!r} is not a #RRGGBB colour")
    return "#" + match.group(1).upper()


def read_palette(path: Path | str) -> list[Swatch]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PaletteError(f"cannot read {path}: {exc}") from exc
    swatches: list[Swatch] = []
    if path.suffix.lower() == ".json":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PaletteError(f"{path}: {exc}") from exc
        items = raw.items() if isinstance(raw, dict) else (
            (entry.get("name", ""), entry.get("hex", "")) for entry in raw)
        for name, value in items:
            swatches.append(Swatch(str(name).strip(), _hex(value, f"{path}: {name}")))
    else:
        for line in text.splitlines():
            match = _HEX.search(line)
            if not match:
                continue
            name = line[: match.start()].strip(" \t|:-=*`")
            name = re.sub(r"\s*[|`*]+\s*", " ", name).strip()
            if name:
                swatches.append(Swatch(name, "#" + match.group(1).upper()))
    names = [s.name for s in swatches]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise PaletteError(f"{path}: duplicate swatch names {dupes}")
    if not swatches:
        raise PaletteError(f"{path}: no swatches found")
    return swatches
