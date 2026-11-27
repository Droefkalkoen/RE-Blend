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
    "check_stock_size",
    "visible_centre_offset",
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
    #: Size of the whole hi-res strip as RE2DRender and the SDK examples ship
    #: it (width, height), or None when unknown. A copy of another size is
    #: the wrong file (Skou feedback #25: a 3 KB low-res tape).
    size: tuple[int, int] | None = None
    #: Frames in the strip (the sockets are 3-frame strips, #9).
    frames: int = 1

    @property
    def candidates(self) -> tuple[str, ...]:
        return (self.name,) + self.aliases


#: widget constructor name -> the stock part it must use. Derived from the
#: per-widget "use the <X> scenegraph / image" notes in the scripting
#: specification; the ``.sg`` names given there are the 3D-pipeline
#: scenegraphs, and the 2D pipeline ships same-named PNGs.
STOCK_PARTS: dict[str, StockPart] = {
    # Canonical names first: the files RE2DRender ships in RE2DRender/Images
    # and every SDK example's GUI2D/ uses. Sizes are the hi-res strips
    # (measured from the SDK 4.6.0 examples, which carry RE2DRender's files).
    "audio_input_socket": StockPart(
        "SharedAudioJack", "audio socket",
        aliases=("Cable_Attachment_Audio", "Socket_Audio", "AudioJack"),
        size=(95, 315), frames=3,
    ),
    "audio_output_socket": StockPart(
        "SharedAudioJack", "audio socket",
        aliases=("Cable_Attachment_Audio", "Socket_Audio", "AudioJack"),
        size=(95, 315), frames=3,
    ),
    "cv_input_socket": StockPart(
        "SharedCVJack", "CV socket",
        aliases=("CV_Attachment_Audio", "Socket_CV", "CVJack"),
        size=(75, 255), frames=3,
    ),
    "cv_output_socket": StockPart(
        "SharedCVJack", "CV socket",
        aliases=("CV_Attachment_Audio", "Socket_CV", "CVJack"),
        size=(75, 255), frames=3,
    ),
    "cv_trim_knob": StockPart("TrimKnob", "CV trim knob", aliases=("Trim_Knob",),
                              size=(100, 100)),
    "device_name": StockPart(
        "TapeHorz", "device-name tape",
        aliases=("Tape_Horizontal",),
        size=(400, 65),
    ),
    "placeholder": StockPart("Placeholder", "back-panel placeholder", size=(300, 100)),
    "patch_browse_group": StockPart("PatchBrowseGroup", "patch browse group",
                                    aliases=("Patch_Browse_Group",), size=(290, 110)),
    "sample_browse_group": StockPart("SampleBrowseGroup", "sample browse group",
                                     aliases=("Sample_Browse_Group",), size=(295, 110)),
}

#: The vertical tape for a device_name on a rack ear (``TapeVert``).
VERTICAL_TAPE = StockPart("TapeVert", "device-name tape (vertical)",
                          aliases=("Tape_Vertical",), size=(65, 400))


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
    if source is not None and check_stock_size(part, source) is not None:
        # A wrong-size copy (an example's low-res file) under a canonical
        # name: look for a right-size one before giving up on it.
        source = _right_size_copy(part, sdk_root) or source
    if source is None:
        raise FileNotFoundError(
            f"no stock image for the {part.description} "
            f"({'/'.join(part.candidates)}.png) under {sdk_root}"
        )
    destination = Path(gui2d_dir) / f"{sprite_path}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def check_stock_size(part: StockPart, path: Path | str) -> str | None:
    """Why ``path`` is not the hi-res stock image, or None when its size fits."""
    if part.size is None:
        return None
    from .png_meta import PngError, read_png_meta

    try:
        meta = read_png_meta(path)
    except (PngError, OSError) as exc:
        return f"cannot read {path}: {exc}"
    if (meta.width, meta.height) != part.size:
        return (f"{Path(path).name} is {meta.width}x{meta.height}; the SDK's hi-res "
                f"{part.description} is {part.size[0]}x{part.size[1]} — this is probably a "
                "low-res or edited copy (use RE2DRender/Images)")
    return None


def _right_size_copy(part: StockPart, sdk_root: Path | str) -> Path | None:
    root = Path(sdk_root)
    for name in part.candidates:
        for found in sorted(root.rglob(f"{name}.png"), key=lambda p: len(p.parts)):
            if len(found.relative_to(root).parts) <= MAX_SEARCH_DEPTH \
                    and check_stock_size(part, found) is None:
                return found
    return None


def visible_centre_offset(alpha, frame_h: int | None = None) -> tuple[float, float]:
    """How far the visible art's centre sits from the frame centre, in px
    (x right, y down), from an ``(H, W)`` alpha array of the first frame
    (Skou feedback #28: SharedAudioJack's nut sits about 6 px left of centre).

    Subtract it from a node offset to centre what the eye sees rather than
    the frame. Pure numpy; ``frame_h`` crops a strip to its first frame.
    """
    import numpy as np

    a = np.asarray(alpha, dtype=np.float32)
    if frame_h:
        a = a[:frame_h]
    total = float(a.sum())
    if total <= 0.0:
        return 0.0, 0.0
    ys, xs = np.indices(a.shape)
    cx = float((xs * a).sum()) / total + 0.5
    cy = float((ys * a).sum()) / total + 0.5
    return cx - a.shape[1] / 2.0, cy - a.shape[0] / 2.0
