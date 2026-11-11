"""A project as the bench sees it: the Lua files, the sheets on disk, the sidecar.

:class:`BenchProject` holds the text of every file the bench may edit, so a
dry run can build the project as it would be after edits that have not been
written yet. Positions and frame counts live in ``device_2D.lua``; widgets in
``hdgui_2D.lua``; property stubs in ``motherboard_def.lua`` and their names in
``Resources/English/texts.lua``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from reblend.project.link import (
    GUI2D_DIRNAME, MOTHERBOARD_FILENAME, ProjectLink, derive_specs, size_specs,
)
from reblend.project.lua_reader import (
    LuaConfigError, read_device_2d_text, read_hdgui_2d_text,
)
from reblend.project.motherboard_reader import read_motherboard_steps_text

from .sidecar import Sidecar

__all__ = ["BenchProject", "DEVICE_2D", "HDGUI_2D", "MOTHERBOARD", "TEXTS"]

DEVICE_2D = f"{GUI2D_DIRNAME}/device_2D.lua"
HDGUI_2D = f"{GUI2D_DIRNAME}/hdgui_2D.lua"
MOTHERBOARD = MOTHERBOARD_FILENAME
TEXTS = "Resources/English/texts.lua"
#: Files the bench reads into memory; the last two are optional.
FILES = (DEVICE_2D, HDGUI_2D, MOTHERBOARD, TEXTS)


@dataclass
class BenchProject:
    root: Path
    #: relative path -> text, for each file in :data:`FILES` that exists.
    sources: dict[str, str]
    link: ProjectLink
    sidecar: Sidecar

    @property
    def device_source(self) -> str:
        return self.sources[DEVICE_2D]

    @property
    def device_path(self) -> Path:
        return self.root / DEVICE_2D

    @classmethod
    def load(cls, root: Path | str) -> "BenchProject":
        root = Path(root)
        sources = {}
        for rel in FILES:
            path = root / rel
            if path.is_file():
                sources[rel] = path.read_bytes().decode("utf-8")
        if DEVICE_2D not in sources or HDGUI_2D not in sources:
            raise FileNotFoundError(f"{root} has no {DEVICE_2D} and {HDGUI_2D}")
        return cls.build(root, sources, Sidecar.load(root))

    @classmethod
    def build(cls, root: Path, sources: dict[str, str], sidecar: Sidecar) -> "BenchProject":
        gui2d = root / GUI2D_DIRNAME
        device = read_device_2d_text(sources[DEVICE_2D], str(root / DEVICE_2D))
        hdgui = read_hdgui_2d_text(sources[HDGUI_2D], str(root / HDGUI_2D))
        specs = derive_specs(device, hdgui)
        size_specs(specs, gui2d, sidecar)
        steps: dict[str, int] = {}
        if MOTHERBOARD in sources:
            try:
                steps = read_motherboard_steps_text(sources[MOTHERBOARD], str(root / MOTHERBOARD))
            except LuaConfigError:
                pass
        link = ProjectLink(root=root, gui2d_dir=gui2d, device=device, hdgui=hdgui,
                           specs=specs, property_steps=steps)
        return cls(root, dict(sources), link, sidecar)

    def with_changes(self, sources: dict[str, str] | None = None,
                     sidecar: Sidecar | None = None,
                     device_source: str | None = None) -> "BenchProject":
        merged = dict(self.sources)
        merged.update(sources or {})
        if device_source is not None:
            merged[DEVICE_2D] = device_source
        return BenchProject.build(
            self.root, merged,
            copy.deepcopy(self.sidecar) if sidecar is None else sidecar,
        )
