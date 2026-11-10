"""A project as the bench sees it: the Lua files, the sheets on disk, the sidecar.

:class:`BenchProject` can be built from disk or from an edited ``device_2D.lua``
source held in memory, which is how dry runs see the result of edits that
have not been written yet.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from reblend.project.link import (
    GUI2D_DIRNAME, MOTHERBOARD_FILENAME, ProjectLink, _probe_frame_size, derive_specs,
)
from reblend.project.lua_reader import (
    LuaConfigError, read_device_2d_text, read_hdgui_2d,
)
from reblend.project.motherboard_reader import read_motherboard_steps

from .sidecar import Sidecar

__all__ = ["BenchProject"]


@dataclass
class BenchProject:
    root: Path
    device_source: str
    link: ProjectLink
    sidecar: Sidecar

    @property
    def device_path(self) -> Path:
        return self.root / GUI2D_DIRNAME / "device_2D.lua"

    @classmethod
    def load(cls, root: Path | str) -> "BenchProject":
        root = Path(root)
        source = (root / GUI2D_DIRNAME / "device_2D.lua").read_bytes().decode("utf-8")
        return cls.build(root, source, Sidecar.load(root))

    @classmethod
    def build(cls, root: Path, device_source: str, sidecar: Sidecar) -> "BenchProject":
        gui2d = root / GUI2D_DIRNAME
        device = read_device_2d_text(device_source, str(gui2d / "device_2D.lua"))
        hdgui = read_hdgui_2d(gui2d / "hdgui_2D.lua")
        specs = derive_specs(device, hdgui)
        for spec in specs:
            info = sidecar.sprites.get(spec.path)
            if info and info.frame_w and info.frame_h:
                # Designer-set sizes win over whatever the last render produced.
                spec.frame_w, spec.frame_h = info.frame_w, info.frame_h
            else:
                _probe_frame_size(spec, gui2d)
        steps: dict[str, int] = {}
        if (root / MOTHERBOARD_FILENAME).is_file():
            try:
                steps = read_motherboard_steps(root / MOTHERBOARD_FILENAME)
            except LuaConfigError:
                pass
        link = ProjectLink(root=root, gui2d_dir=gui2d, device=device, hdgui=hdgui,
                           specs=specs, property_steps=steps)
        return cls(root, device_source, link, sidecar)

    def with_changes(self, device_source: str | None = None,
                     sidecar: Sidecar | None = None) -> "BenchProject":
        return BenchProject.build(
            self.root,
            self.device_source if device_source is None else device_source,
            copy.deepcopy(self.sidecar) if sidecar is None else sidecar,
        )
