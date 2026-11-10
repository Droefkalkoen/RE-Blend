"""``rebench new``: start a device from a template.

The effect template is a stereo ``creative_fx`` with one Level knob, the
On/Off/Bypass fader, the name tape on all four panels, stereo jacks and the
placeholder: the smallest device that passes the rule catalogue, builds with
pongasoft re-cmake and opens in Recon. Its C++ is a gain stage that follows
the host's On/Off/Bypass switch, as an effect must. Grey-box sprites are drawn
straight away, so the build has art to compile.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .greybox import GreyboxResult, build_greybox
from .sidecar import Sidecar

__all__ = ["TEMPLATES", "DeviceIdentity", "new_device"]

TEMPLATES_DIR = Path(__file__).parent / "templates"
TEMPLATES = ("effect",)
#: Template file name -> name in the new project (dotfiles do not package well).
_RENAMES = {"gitignore.txt": ".gitignore"}
_TEXT_SUFFIXES = {".lua", ".cpp", ".h", ".txt", ".md", ".json", ".example"}


@dataclass
class DeviceIdentity:
    name: str
    manufacturer: str
    product_id: str = ""
    height_ru: int = 1

    def values(self) -> dict[str, str]:
        slug = re.sub(r"[^a-z0-9]+", "", self.name.lower()) or "device"
        maker = re.sub(r"[^a-z0-9]+", "", self.manufacturer.lower()) or "maker"
        ident = re.sub(r"[^A-Za-z0-9]+", "", self.name) or "Device"
        return {
            "LONG_NAME": self.name[:40],
            "MEDIUM_NAME": self.name[:20],
            "SHORT_NAME": self.name[:10],
            "MANUFACTURER": self.manufacturer,
            "PRODUCT_ID": self.product_id or f"com.{maker}.{ident}",
            "HEIGHT_RU": str(self.height_ru),
            "CPP_NAMESPACE": slug if slug[0].isalpha() else f"re_{slug}",
            "CMAKE_PROJECT": ident if ident[0].isalpha() else f"RE{ident}",
        }


def _lua_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def new_device(target: Path | str, identity: DeviceIdentity, template: str = "effect",
               sdk_root: Path | str | None = None) -> GreyboxResult:
    """Write a new project into ``target`` (which must not exist or be empty)."""
    target = Path(target)
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}; choose from {', '.join(TEMPLATES)}")
    if not 1 <= identity.height_ru <= 9:
        raise ValueError("height must be 1 to 9 rack units")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"{target} is not empty")
    values = identity.values()
    lua_values = {k: _lua_string(v) for k, v in values.items()}
    source = TEMPLATES_DIR / template

    for path in sorted(source.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(source)
        out = target / rel.parent / _RENAMES.get(rel.name, rel.name)
        out.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in _TEXT_SUFFIXES or path.name in _RENAMES:
            text = path.read_text(encoding="utf-8")
            table = lua_values if path.suffix == ".lua" else values
            text = re.sub(r"@@([A-Z_]+)@@", lambda m: table[m.group(1)], text)
            out.write_text(text, encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(path, out)

    if identity.height_ru > 1:
        _scale_template(target, identity.height_ru)
    # Normalise the sidecar to the format every later save uses (small diffs).
    Sidecar.load(target).save(target)
    return build_greybox(target, sdk_root=sdk_root)


def _scale_template(target: Path, ru: int) -> None:
    """A taller front only moves the knob and its label to the vertical middle."""
    from .ops import apply_ops
    mid = ru * 345 // 2
    apply_ops(target, [
        {"op": "move", "panel": "front", "node": "knob_level", "x": 1825, "y": mid - 60},
        {"op": "label", "id": "lbl-level", "y": mid + 110},
    ])
