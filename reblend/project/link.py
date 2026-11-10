"""Project link: read an RE project's GUI2D config into element specs (§4.1, §6.1).

This is the read-only half of the sync story and the whole of M1's import:
``device_2D.lua`` (placement, sprite paths, frame counts) and ``hdgui_2D.lua``
(widget types, property bindings) are read through the sandboxed interpreter
and correlated into one :class:`ElementSpec` per *sprite sheet*.

Identity is the sprite path, not the node: every exported sheet corresponds
to one RE Element (§4.2), and several nodes may place the same sheet (two
lamps sharing lamp art, the DeviceName tape on all four panels, both audio
jacks). Each such appearance is a :class:`Placement` with its offset resolved
to absolute panel pixels (device_2D group offsets folded in).

Nothing here writes anything, and nothing imports ``bpy`` — materialising
specs into a scene is the UI layer's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..model import kinds, schema
from .lua_reader import (
    PANELS,
    Device2D,
    HDGui2D,
    LuaConfigError,
    Node2D,
    read_device_2d,
    read_hdgui_2d,
)
from .motherboard_reader import read_motherboard_steps
from .png_meta import PngError, read_png_meta
from .sidecar import Sidecar

__all__ = ["GUI2D_DIRNAME", "ElementSpec", "ProjectLink", "derive_specs", "load_project",
           "size_specs"]

GUI2D_DIRNAME = "GUI2D"
MOTHERBOARD_FILENAME = "motherboard_def.lua"


@dataclass
class ElementSpec:
    """Everything import knows about one sheet-to-be.

    ``frame_w``/``frame_h`` are 0 when unknown — an existing sheet on disk
    fills them in (dimensions ÷ frames); otherwise the designer decides them
    in Blender and validation nags until they're set. A size set in RE Bench's
    sidecar (``GUI2D/rebench.json``) wins over the sheet on disk, which only
    says what the last render produced.

    ``shape`` is the sidecar's blockout shape (``{"type": "knob", ...}``),
    empty when the bench never set one; import uses it for blockout geometry.
    """

    path: str
    kind: str
    frames: int
    frame_w: int = 0
    frame_h: int = 0
    placements: tuple[schema.Placement, ...] = ()
    widget_kinds: tuple[str, ...] = ()
    shape: dict = field(default_factory=dict)

    @property
    def node(self) -> str:
        """Primary node name (first placement)."""
        return self.placements[0].node if self.placements else ""

    @property
    def panels(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for placement in self.placements:
            seen.setdefault(placement.panel, None)
        return tuple(seen)

    def to_element_data(self) -> schema.ElementData:
        return schema.ElementData(
            node=self.node,
            path=self.path,
            kind=self.kind,
            frames=self.frames,
            frame_w=self.frame_w,
            frame_h=self.frame_h,
            placements=self.placements,
        )


@dataclass
class ProjectLink:
    """A parsed, read-only view of one RE project's GUI2D layer."""

    root: Path
    gui2d_dir: Path
    device: Device2D
    hdgui: HDGui2D
    specs: list[ElementSpec] = field(default_factory=list)
    #: property path -> steps, from motherboard_def.lua (best effort; empty
    #: when the file is absent or unreadable — validation degrades gracefully).
    property_steps: dict[str, int] = field(default_factory=dict)

    def spec(self, path: str) -> ElementSpec | None:
        for candidate in self.specs:
            if candidate.path == path:
                return candidate
        return None


def load_project(root: Path | str) -> ProjectLink:
    """Read a project by its repo root (the directory containing ``GUI2D/``)."""
    root = Path(root)
    gui2d = root / GUI2D_DIRNAME
    device = read_device_2d(gui2d / "device_2D.lua")
    hdgui = read_hdgui_2d(gui2d / "hdgui_2D.lua")

    specs = derive_specs(device, hdgui)
    size_specs(specs, gui2d, Sidecar.load(root))

    property_steps: dict[str, int] = {}
    motherboard = root / MOTHERBOARD_FILENAME
    if motherboard.is_file():
        try:
            property_steps = read_motherboard_steps(motherboard)
        except LuaConfigError:
            pass  # best effort by design (§4.1): validation-only input

    return ProjectLink(
        root=root,
        gui2d_dir=gui2d,
        device=device,
        hdgui=hdgui,
        specs=specs,
        property_steps=property_steps,
    )


def derive_specs(device: Device2D, hdgui: HDGui2D) -> list[ElementSpec]:
    """Correlate the two files into one spec per sprite path.

    Deterministic regardless of Lua table iteration order: placements sort by
    (panel order, node name); elements order by first placement.
    """
    backdrop_nodes = {
        panel: hd.graphics_node for panel, hd in hdgui.panels.items() if hd.graphics_node
    }

    by_path: dict[str, ElementSpec] = {}
    for panel in PANELS:
        nodes = device.panels.get(panel, {})
        for name in sorted(nodes):
            _walk(by_path, hdgui, backdrop_nodes, panel, nodes[name], 0.0, 0.0)

    return list(by_path.values())


def _walk(
    by_path: dict[str, ElementSpec],
    hdgui: HDGui2D,
    backdrop_nodes: dict[str, str],
    panel: str,
    node: Node2D,
    base_x: float,
    base_y: float,
) -> None:
    x = base_x + (node.offset[0] if node.offset else 0.0)
    y = base_y + (node.offset[1] if node.offset else 0.0)

    if node.graphics:
        hd_panel = hdgui.panels.get(panel)
        widgets = (
            [(w.kind, w.attrs) for w in hd_panel.widgets if w.node == node.name]
            if hd_panel
            else []
        )
        is_backdrop = backdrop_nodes.get(panel) == node.name
        kind = kinds.kind_for_node(widgets, node.frames, is_backdrop=is_backdrop)
        placement = schema.Placement(panel=panel, node=node.name, x=x, y=y)

        for graphic in node.graphics:
            spec = by_path.get(graphic.path)
            if spec is None:
                spec = ElementSpec(path=graphic.path, kind=kind, frames=graphic.frames)
                by_path[graphic.path] = spec
            elif spec.kind == kinds.STATIC and kind != kinds.STATIC:
                # A later, more specific binding wins over a static guess.
                spec.kind = kind
            spec.placements = spec.placements + (placement,)
            spec.widget_kinds = spec.widget_kinds + tuple(
                w for w, _ in widgets if w not in spec.widget_kinds
            )

    for child_name in sorted(node.children):
        _walk(by_path, hdgui, backdrop_nodes, panel, node.children[child_name], x, y)


def size_specs(specs: list[ElementSpec], gui2d_dir: Path, sidecar: Sidecar) -> None:
    """Frame sizes and blockout shapes: the sidecar's first, else the PNG's."""
    for spec in specs:
        info = sidecar.sprites.get(spec.path)
        if info and info.frame_w > 0 and info.frame_h > 0:
            spec.frame_w, spec.frame_h = int(info.frame_w), int(info.frame_h)
        else:
            _probe_frame_size(spec, gui2d_dir)
        if info and info.shape:
            spec.shape = dict(info.shape)


def _probe_frame_size(spec: ElementSpec, gui2d_dir: Path) -> None:
    """Fill frame size from an existing sheet on disk, when it divides evenly.

    A sheet whose height is not ``frames × frame_h`` for integer ``frame_h``
    stays unknown here; the dimension cross-check in validation reports it.
    """
    png = gui2d_dir / f"{spec.path}.png"
    if not png.is_file():
        return
    try:
        meta = read_png_meta(png)
    except PngError:
        return
    frame_h, remainder = divmod(meta.height, spec.frames)
    if remainder == 0 and frame_h > 0:
        spec.frame_w = meta.width
        spec.frame_h = frame_h
