"""``GUI2D/rebench.json``: what RE Bench knows that the Lua files cannot carry.

RE Bench writes it; RE-Blend's import reads it (designer-set frame sizes and
blockout shapes), so the format lives here, in RE-Blend's pure-Python layer.

Positions never live here; ``device_2D.lua`` owns them. The sidecar holds:

- ``sprites``: per sprite path, the designer-set frame size (overrides the size
  probed from a PNG on disk, which only says what the last render produced)
  and an optional blockout ``shape`` for the grey-box and Blender handoff;
- ``labels``: panel text (silk) with its size in hi-res px, so legibility can
  be checked before any art exists;
- ``sprites[path].derive_from`` / ``crop``: a *derived sheet* (Skou feedback
  #38), cut from another sprite's frames instead of rendered on its own. The
  folded On/Off/Bypass fader is the front fader's sheet with every frame
  cropped to ``[x, y, w, h]`` (frame px) so it fits the 150 px folded strip.
  RE-Blend renders the source and writes the derived sheet beside it;
- ``art``: marks painted into panel art that the Lua files cannot see:
  rack-ear screws and the SDK ``Routing_Icon_*`` symbols, each with its
  panel and centre (hi-res px), so the screw and routing-icon rules can check
  them (Skou feedback #30, #31). Sprites placed as nodes count too; this is
  for marks baked into a backdrop;
- ``greybox``: sprite path -> SHA-256 of the ``rebench greybox`` placeholder
  written there. A PNG is redrawn only while its bytes still match, so a
  render from RE-Blend (or any other edit) is never overwritten.

Versioned so later schema changes can migrate old files.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

__all__ = ["SIDECAR_FILENAME", "SCHEMA_VERSION", "ArtMark", "Label", "SpriteInfo", "Sidecar"]

#: ``ArtMark.kind`` values.
SCREW = "screw"
ROUTING_ICON = "routing_icon"

SIDECAR_FILENAME = "rebench.json"
#: v2 adds ``derive_from`` / ``crop`` on sprites; v3 adds ``art``. Older
#: files read unchanged.
SCHEMA_VERSION = 3


@dataclass
class SpriteInfo:
    frame_w: int = 0
    frame_h: int = 0
    shape: dict = field(default_factory=dict)
    #: Sprite path this sheet is cut from ("" = rendered on its own).
    derive_from: str = ""
    #: ``[x, y, w, h]`` in the source's frame px; the derived frame size is
    #: ``w x h``. Empty means the whole source frame.
    crop: list = field(default_factory=list)

    def derived_rect(self, source_w: int, source_h: int) -> tuple[int, int, int, int]:
        """The crop rectangle, defaulting to the full source frame."""
        if len(self.crop) == 4:
            x, y, w, h = (int(v) for v in self.crop)
            return x, y, w, h
        return 0, 0, int(source_w), int(source_h)


@dataclass
class Label:
    id: str
    panel: str
    text: str
    x: float
    y: float
    size_px: float
    #: False for decorative text (brand marks); only functional text has to
    #: meet the readability floor.
    functional: bool = True
    #: The node the label describes, if any (moves with it in the canvas).
    node: str = ""


@dataclass
class ArtMark:
    """A mark painted into panel art: a screw or a routing icon."""

    panel: str
    kind: str
    #: For routing icons, the SDK name (``Routing_Icon_03`` or
    #: ``Routing_Icon_TrueStereo``); free text for screws.
    name: str = ""
    #: Centre, hi-res px.
    x: float = 0.0
    y: float = 0.0


@dataclass
class Sidecar:
    sprites: dict[str, SpriteInfo] = field(default_factory=dict)
    labels: list[Label] = field(default_factory=list)
    greybox: dict[str, str] = field(default_factory=dict)
    art: list[ArtMark] = field(default_factory=list)
    version: int = SCHEMA_VERSION

    # -- io ---------------------------------------------------------------

    @classmethod
    def path_for(cls, root: Path | str) -> Path:
        return Path(root) / "GUI2D" / SIDECAR_FILENAME

    @classmethod
    def load(cls, root: Path | str) -> "Sidecar":
        path = cls.path_for(root)
        if not path.is_file():
            return cls()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, raw: dict) -> "Sidecar":
        version = int(raw.get("version", 1))
        if version > SCHEMA_VERSION:
            raise ValueError(f"{SIDECAR_FILENAME} is schema version {version}; this "
                             f"RE Bench reads up to {SCHEMA_VERSION}")
        sprites = {k: SpriteInfo(**_known(SpriteInfo, v))
                   for k, v in (raw.get("sprites") or {}).items()}
        labels = [Label(**_known(Label, v)) for v in (raw.get("labels") or [])]
        greybox = {str(k): str(v) for k, v in (raw.get("greybox") or {}).items()}
        art = [ArtMark(**_known(ArtMark, v)) for v in (raw.get("art") or [])]
        return cls(sprites=sprites, labels=labels, greybox=greybox, art=art,
                   version=SCHEMA_VERSION)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "sprites": {k: _sprite_dict(v) for k, v in sorted(self.sprites.items())},
            "labels": [asdict(label) for label in self.labels],
            "greybox": dict(sorted(self.greybox.items())),
            **({"art": [asdict(mark) for mark in self.art]} if self.art else {}),
        }

    def save(self, root: Path | str) -> Path:
        path = self.path_for(root)
        text = json.dumps(self.to_dict(), indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".rebench.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
        return path

    # -- helpers ----------------------------------------------------------

    def label(self, label_id: str) -> Label | None:
        return next((l for l in self.labels if l.id == label_id), None)

    @property
    def empty(self) -> bool:
        return not self.sprites and not self.labels and not self.greybox and not self.art


def _sprite_dict(info: SpriteInfo) -> dict:
    """A sprite entry without the empty derived-sheet keys, so files that
    use no derived sheets keep their v1 shape."""
    raw = asdict(info)
    if not raw["derive_from"]:
        raw.pop("derive_from")
    if not raw["crop"]:
        raw.pop("crop")
    return raw


def _known(kind, raw: dict) -> dict:
    """Drop keys a newer writer added that this dataclass does not know."""
    names = {f.name for f in fields(kind)}
    return {k: v for k, v in raw.items() if k in names}
