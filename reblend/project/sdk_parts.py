"""Stock 2D parts the SDK supplies and RE-Blend must not re-author.

For a handful of widgets the Jukebox scripting specification says outright
that "you cannot change the appearance of this widget" and names the image to
use — sockets, the device-name tape, the back-panel placeholder, CV trim
knobs, and the patch/sample browse groups. The GUI design guidelines repeat
each of these as a *requirement*: a device that draws its own socket art is
rejected. Reason draws these parts itself; the PNG in ``GUI2D/`` only has to
be the right stock image at the right size so the layout lines up.

So RE-Blend renders nothing for them. Instead it resolves the stock file out
of the SDK the user has on disk and copies it into the project's ``GUI2D/``.
This module is the pure half of that: locating candidate files under an SDK
root and deciding what to copy where. It never imports ``bpy``; the operator
in :mod:`reblend.ui.operators` supplies the configured SDK path.

Layout note: the SDK ships these images in ``RE2DRender/Images`` (the "RE2D
package" the specification refers to), but the exact root a user points at
varies — some point at the folder containing ``RE2DRender/``, some at the
inner ``SDK/`` directory, and the example devices carry their own copies in
``Examples/<Device>/GUI2D``. Rather than hard-code one layout, we search a
short list of likely directories and then fall back to a bounded recursive
glob, preferring the shallowest match.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "STOCK_PARTS",
    "SEARCH_DIRS",
    "MAX_SEARCH_DEPTH",
    "StockPart",
    "stock_part_for_widget",
    "find_stock_image",
    "install_stock_part",
]


@dataclass(frozen=True)
class StockPart:
    """One SDK-supplied image and the widgets that must use it."""

    #: Basename of the stock PNG, without extension.
    name: str
    #: Human description for the UI and validation messages.
    description: str
    #: Alternative basenames seen across SDK versions and example devices.
    aliases: tuple[str, ...] = ()

    @property
    def candidates(self) -> tuple[str, ...]:
        return (self.name,) + self.aliases


#: widget constructor name -> the stock part it must use. Derived from the
#: per-widget "use the <X> scenegraph / image" notes in the scripting
#: specification; the ``.sg`` names given there are the 3D-pipeline
#: scenegraphs, and the 2D pipeline ships same-named PNGs.
STOCK_PARTS: dict[str, StockPart] = {
    "audio_input_socket": StockPart(
        "Cable_Attachment_Audio",
        "audio socket",
        aliases=("SharedAudioJack", "Socket_Audio", "AudioJack"),
    ),
    "audio_output_socket": StockPart(
        "Cable_Attachment_Audio",
        "audio socket",
        aliases=("SharedAudioJack", "Socket_Audio", "AudioJack"),
    ),
    "cv_input_socket": StockPart(
        "CV_Attachment_Audio",
        "CV socket",
        aliases=("SharedCVJack", "Socket_CV", "CVJack"),
    ),
    "cv_output_socket": StockPart(
        "CV_Attachment_Audio",
        "CV socket",
        aliases=("SharedCVJack", "Socket_CV", "CVJack"),
    ),
    "cv_trim_knob": StockPart("Trim_Knob", "CV trim knob"),
    "device_name": StockPart(
        "Tape_Horizontal",
        "device-name tape",
        aliases=("Tape_Vertical",),
    ),
    "placeholder": StockPart("Placeholder", "back-panel placeholder"),
    "patch_browse_group": StockPart("Patch_Browse_Group", "patch browse group"),
    "sample_browse_group": StockPart("Sample_Browse_Group", "sample browse group"),
}

#: Directories under an SDK root to check first, in order of preference.
SEARCH_DIRS = (
    Path("RE2DRender/Images"),
    Path("Images"),
    Path("RE2D/Images"),
    Path("Tools/RE2DRender/Images"),
)

#: How deep the recursive fallback search descends below the SDK root. Deep
#: enough to reach ``Examples/<Device>/GUI2D``, shallow enough not to walk a
#: whole toolchain tree.
MAX_SEARCH_DEPTH = 4


def stock_part_for_widget(widget: str) -> StockPart | None:
    """The stock part a widget constructor must use, if it has a fixed one."""
    return STOCK_PARTS.get(widget)


def find_stock_image(part: StockPart, sdk_root: Path | str) -> Path | None:
    """Locate a stock PNG under an SDK root, or None if it isn't there.

    Preferred directories are checked first; failing that, a bounded
    recursive search returns the shallowest match so a canonical copy beats an
    example device's local one.
    """
    root = Path(sdk_root)
    if not root.is_dir():
        return None

    for relative in SEARCH_DIRS:
        directory = root / relative
        if not directory.is_dir():
            continue
        for name in part.candidates:
            candidate = directory / f"{name}.png"
            if candidate.is_file():
                return candidate

    best: tuple[int, Path] | None = None
    for name in part.candidates:
        for found in root.rglob(f"{name}.png"):
            try:
                depth = len(found.relative_to(root).parts)
            except ValueError:  # pragma: no cover - rglob results are relative
                continue
            if depth > MAX_SEARCH_DEPTH:
                continue
            if best is None or depth < best[0]:
                best = (depth, found)
    return best[1] if best is not None else None


def install_stock_part(
    part: StockPart, sdk_root: Path | str, gui2d_dir: Path | str, sprite_path: str
) -> Path:
    """Copy a stock image into ``GUI2D/<sprite_path>.png``.

    ``sprite_path`` is the name device_2D.lua already uses, so the copy lands
    under the name the project expects rather than the SDK's own. Raises
    :class:`FileNotFoundError` when the part isn't found under ``sdk_root`` —
    the caller turns that into a message naming the SDK path to fix.
    """
    source = find_stock_image(part, sdk_root)
    if source is None:
        raise FileNotFoundError(
            f"no stock image for the {part.description} "
            f"({'/'.join(part.candidates)}.png) under {sdk_root}"
        )
    destination = Path(gui2d_dir) / f"{sprite_path}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination
