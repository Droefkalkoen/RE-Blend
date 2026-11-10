"""House profile: the configurable numbers behind ``house`` and ``obs`` rules.

Defaults are Terp Audio's, learned on Skou. Every number here is either a brand
choice or an observation of Reason's own drawing, never an SDK requirement, so
it lives in a profile a project can override (``rebench.profile.json`` beside
``GUI2D/``) rather than in RE-Blend's calibration constants.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path

__all__ = ["Profile", "load_profile", "PROFILE_FILENAME"]

PROFILE_FILENAME = "rebench.profile.json"


@dataclass(frozen=True)
class Profile:
    #: Front-panel rack ears: x < ear_left or x > ear_right holds ear art only
    #: (Skou feedback #24: the Output plate grew over the right ear).
    ear_left_px: int = 160
    ear_right_px: int = 3610
    #: Fold/unfold arrow keep-out, per panel kind, as (x, y, w, h). PROVISIONAL:
    #: the SDK gives no position. Bounded from Skou, where the folded tape had to
    #: move right of x = 430 and the cable hole at x = 260 clears it. Replace
    #: with a measurement from a Recon screenshot (docs/rebench-design.md).
    fold_arrow_full: tuple[int, int, int, int] = (25, 0, 135, 140)
    fold_arrow_folded: tuple[int, int, int, int] = (25, 0, 135, 150)
    fold_arrow_measured: bool = False
    #: "Top-left" for the effect bypass control: its centre inside this box.
    #: The SDK names no size; the SDK's SilenceDetectionEffect sits at x 175-185
    #: and Skou's at 170, while Skou's folded fader at x 480 was called out as
    #: "not top-left" (feedback #29), so 400 px is the house reading.
    bypass_box_w: int = 400
    bypass_box_h_full: int = 345
    #: Smallest functional text, hi-res px (SDK guideline: 43 px). Used once
    #: label sizes are known (phase 2 sidecar).
    min_text_px: int = 43
    #: Widget kinds allowed on the rack ears.
    ear_art_widgets: tuple[str, ...] = field(default=("device_name", "static_decoration"))


def load_profile(project_root: Path | str | None) -> Profile:
    """The project's profile, or the defaults when it has none."""
    if project_root is None:
        return Profile()
    path = Path(project_root) / PROFILE_FILENAME
    if not path.is_file():
        return Profile()
    raw = json.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Profile)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"{path}: unknown profile keys {unknown}")
    coerced = {k: tuple(v) if isinstance(v, list) else v for k, v in raw.items()}
    return Profile(**coerced)
