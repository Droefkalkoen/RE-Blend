"""Sandboxed reading of an RE project's ``GUI2D`` layout Lua files.

Implements the reading half of design §6.4: the files are *executed* in an
embedded Lua interpreter (``lupa``) inside a restricted environment, with a
stub ``jbox`` table that records constructor calls instead of doing SDK work.
Executing the real Lua — rather than parsing a second grammar — means anything
the SDK or RE Edit wrote reads with full fidelity.

Two file kinds are handled:

- ``device_2D.lua`` — plain nested tables per panel: node name → ``offset``,
  graphics entries (``{ path = ..., frames = N }``), and optional child nodes.
- ``hdgui_2D.lua`` — ``jbox.panel{...}`` per panel containing ``jbox.<widget>{...}``
  constructor calls.

All widget attributes are preserved verbatim in :attr:`Widget.attrs`, known or
not — the interop rule (§6.4) requires never dropping what other tools wrote.

The sandbox is a *hygiene* measure, not a security boundary against a hostile
attacker: it keeps config execution deterministic and side-effect free
(no ``os``/``io``/``require``), and turns Lua errors into :class:`LuaConfigError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lupa

__all__ = [
    "PANELS",
    "LuaConfigError",
    "Graphic",
    "Node2D",
    "Device2D",
    "Widget",
    "HDPanel",
    "HDGui2D",
    "read_device_2d",
    "read_device_2d_text",
    "read_hdgui_2d",
]

#: Panel keys an RE project can define, in SDK convention order.
PANELS = ("front", "back", "folded_front", "folded_back")

#: Panels every device must define — except Players, which have no folded
#: panels at all (GUI designer manual, "Defining the panels for Players").
UNFOLDED_PANELS = ("front", "back")

#: ``panel_type`` value that marks a Player device in device_2D.lua.
PLAYER_PANEL_TYPE = "note_player"

#: Key under which the jbox recorder tags the constructor name on the table it
#: returns. Double-underscore prefix keeps it out of the way of real SDK keys.
_JBOX_TAG = "__jbox"


class LuaConfigError(Exception):
    """A GUI2D Lua file could not be read (missing, failed to run, bad shape)."""

    def __init__(self, path: Path | str, message: str) -> None:
        self.path = Path(path)
        super().__init__(f"{self.path}: {message}")


# ---------------------------------------------------------------------------
# Sandboxed execution
# ---------------------------------------------------------------------------

# The chunk runs with an empty environment whose __index falls back to a fixed
# whitelist, so after execution `env` holds exactly the globals the file
# assigned (format_version, front, back, ...) and nothing else.
_SANDBOX_LUA = """
function(source, chunkname)
    local jbox = setmetatable({}, {
        __index = function(_, name)
            return function(arg)
                if type(arg) == "table" then
                    arg.%(tag)s = name
                    return arg
                end
                return { %(tag)s = name, value = arg }
            end
        end,
    })
    local base = {
        jbox = jbox,
        math = math, string = string, table = table,
        pairs = pairs, ipairs = ipairs, next = next, select = select,
        type = type, tostring = tostring, tonumber = tonumber,
        unpack = table.unpack,
    }
    local env = setmetatable({}, { __index = base })
    local chunk, load_err = load(source, chunkname, "t", env)
    if not chunk then
        return nil, load_err
    end
    local ok, run_err = pcall(chunk)
    if not ok then
        return nil, tostring(run_err)
    end
    setmetatable(env, nil)
    return env, nil
end
""" % {"tag": _JBOX_TAG}


def _execute_sandboxed(path: Path) -> dict[str, Any]:
    """Run a Lua file in the sandbox and return its assigned globals as Python."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LuaConfigError(path, f"cannot read file: {exc}") from exc
    return _execute_sandboxed_text(source, path)


def _execute_sandboxed_text(source: str, path: Path) -> dict[str, Any]:
    """Run Lua source in the sandbox; ``path`` only labels errors."""
    runtime = lupa.LuaRuntime(
        unpack_returned_tuples=True,
        register_eval=False,
        register_builtins=False,
    )
    sandbox = runtime.eval(_SANDBOX_LUA)
    env, err = sandbox(source, f"@{path.name}")
    if env is None:
        raise LuaConfigError(path, f"Lua error: {err}")
    globals_ = _to_python(env)
    assert isinstance(globals_, dict)
    return globals_


def _to_python(value: Any, _seen: frozenset[int] = frozenset()) -> Any:
    """Convert a Lua value to Python.

    Tables with only consecutive integer keys 1..n become lists; everything
    else becomes a dict (integer keys preserved as ints). Cycles are an error —
    the GUI2D files are declarative data and must be tree-shaped.
    """
    if lupa.lua_type(value) != "table":
        return value
    if id(value) in _seen:
        raise ValueError("cycle detected in Lua table")
    seen = _seen | {id(value)}

    items = {key: _to_python(val, seen) for key, val in value.items()}
    length = len(items)
    if length > 0 and all(isinstance(k, int) for k in items) and sorted(items) == list(
        range(1, length + 1)
    ):
        return [items[i] for i in range(1, length + 1)]
    return items


# ---------------------------------------------------------------------------
# device_2D.lua
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Graphic:
    """One graphics entry of a device_2D node: ``{ path = ..., frames = N }``."""

    path: str
    frames: int = 1


@dataclass
class Node2D:
    """A named node in ``device_2D.lua``.

    ``offset`` is in panel pixels, top-left origin, +y down; ``None`` when the
    file omits it (SDK treats that as ``{0, 0}``). ``CableOrigin`` is an
    ordinary node with an offset and no graphics.
    """

    name: str
    offset: tuple[float, float] | None = None
    graphics: list[Graphic] = field(default_factory=list)
    children: dict[str, "Node2D"] = field(default_factory=dict)
    #: True for the unnamed sub-tables the SDK convention uses to group a
    #: panel's widget nodes (see :func:`_parse_device_panel`). Such a group
    #: carries no graphics and a synthesized name; it exists only so its
    #: children are reached by the offset-folding walk and by name lookup.
    anonymous: bool = False

    @property
    def frames(self) -> int:
        """Frame count of the node's primary (first) graphic, 1 if none."""
        return self.graphics[0].frames if self.graphics else 1

    def walk(self) -> "list[Node2D]":
        """This node and all descendants, depth-first."""
        nodes = [self]
        for child in self.children.values():
            nodes.extend(child.walk())
        return nodes


@dataclass
class Device2D:
    """Parsed ``device_2D.lua``: panels mapping node names to :class:`Node2D`."""

    format_version: str
    panels: dict[str, dict[str, Node2D]]
    source_path: Path
    #: ``panel_type`` global, when the file declares one. Only Players set it.
    panel_type: str | None = None

    @property
    def is_player(self) -> bool:
        """Players have no folded panels and a narrower visible width."""
        return self.panel_type == PLAYER_PANEL_TYPE

    @property
    def required_panels(self) -> tuple[str, ...]:
        """Panels this device must define, given its type."""
        return UNFOLDED_PANELS if self.is_player else PANELS

    def node(self, panel: str, name: str) -> Node2D | None:
        """Look up a node by name anywhere in a panel's tree."""
        for root in self.panels.get(panel, {}).values():
            for candidate in root.walk():
                if candidate.name == name:
                    return candidate
        return None


def read_device_2d(path: Path | str) -> Device2D:
    """Read and parse ``GUI2D/device_2D.lua``."""
    path = Path(path)
    return _parse_device_globals(path, _execute_sandboxed(path))


def read_device_2d_text(source: str, name: str = "device_2D.lua") -> Device2D:
    """Parse device_2D source text without touching disk.

    The patch writer (§6.2) verifies its edits by re-parsing the patched text
    *before* anything is written; ``name`` only labels errors.
    """
    path = Path(name)
    return _parse_device_globals(path, _execute_sandboxed_text(source, path))


def _parse_device_globals(path: Path, globals_: dict[str, Any]) -> Device2D:
    format_version = _require_format_version(path, globals_)

    panels: dict[str, dict[str, Node2D]] = {}
    for panel in PANELS:
        table = globals_.get(panel)
        if table is None:
            continue
        if not isinstance(table, dict):
            raise LuaConfigError(path, f"panel '{panel}' is not a table")
        panels[panel] = _parse_device_panel(path, panel, table)

    panel_type = globals_.get("panel_type")
    return Device2D(
        format_version=format_version,
        panels=panels,
        source_path=path,
        panel_type=panel_type if isinstance(panel_type, str) else None,
    )


def _parse_device_panel(path: Path, panel: str, table: dict[Any, Any]) -> dict[str, Node2D]:
    """Parse one panel table into its named nodes.

    A panel is *not* a flat map of named nodes. The SDK / RE Edit convention —
    seen in every example device — puts the backdrop and any point nodes (e.g.
    ``CableOrigin``) as *named* entries at the top level, then collects the
    widget nodes inside one or more *unnamed* sub-tables::

        front = {
          Panel_Front = {{ path = "Panel_Front" }},   -- named backdrop
          {                                            -- unnamed widget group
            knob_tone = { offset = {...}, { path = "knob_tone", frames = 61 } },
            ...
          },
        }

    An unnamed sub-table arrives here as an *integer* key (Lua array index).
    Each is modelled as a nameless group :class:`Node2D` so its named children
    are reached by the offset-folding walk (:mod:`reblend.project.link`) and by
    :meth:`Device2D.node`, exactly like a named nested group. ``hdgui_2D.lua``
    references every node by name regardless of nesting depth, so the grouping
    is transparent to everything downstream.
    """
    nodes: dict[str, Node2D] = {}
    for key, value in table.items():
        if isinstance(key, str):
            nodes[key] = _parse_node(path, panel, key, value)
        elif isinstance(key, int):
            group_name = f"{panel}:group{key}"
            nodes[group_name] = _parse_node(path, panel, group_name, value, anonymous=True)
        else:
            raise LuaConfigError(path, f"panel '{panel}': unexpected key {key!r}")
    return nodes


def _parse_node(
    path: Path, panel: str, name: Any, value: Any, *, anonymous: bool = False
) -> Node2D:
    where = f"panel '{panel}', node '{name}'"
    if not isinstance(name, str):
        raise LuaConfigError(path, f"{where}: node names must be strings")
    if isinstance(value, list):
        # A table with only an array part converts to a list: graphics, no offset.
        value = {i + 1: entry for i, entry in enumerate(value)}
    if not isinstance(value, dict):
        raise LuaConfigError(path, f"{where}: expected a table, got {type(value).__name__}")

    node = Node2D(name=name, anonymous=anonymous)
    for key, entry in value.items():
        if key == "offset":
            if not (isinstance(entry, list) and len(entry) == 2):
                raise LuaConfigError(path, f"{where}: offset must be {{x, y}}")
            node.offset = (entry[0], entry[1])
        elif isinstance(key, int):
            if isinstance(entry, dict) and "path" in entry:
                frames = entry.get("frames", 1)
                if not isinstance(frames, (int, float)) or int(frames) < 1:
                    raise LuaConfigError(path, f"{where}: invalid frames value {frames!r}")
                node.graphics.append(Graphic(path=entry["path"], frames=int(frames)))
            elif isinstance(entry, dict):
                # An unnamed nested group (integer-keyed sub-table of named
                # nodes) rather than a graphic — same transparent grouping the
                # panel level uses, but nested inside a node.
                child_name = f"{name}:group{key}"
                node.children[child_name] = _parse_node(
                    path, panel, child_name, entry, anonymous=True
                )
            else:
                raise LuaConfigError(path, f"{where}: graphics entry {key} has no path")
        elif isinstance(key, str):
            node.children[key] = _parse_node(path, panel, key, entry)
        else:
            raise LuaConfigError(path, f"{where}: unexpected key {key!r}")
    return node


# ---------------------------------------------------------------------------
# hdgui_2D.lua
# ---------------------------------------------------------------------------


@dataclass
class Widget:
    """One ``jbox.<kind>{...}`` widget from ``hdgui_2D.lua``.

    ``attrs`` is the constructor's full argument table (minus the recorder
    tag), unknown attributes included — they must survive a round trip
    byte-for-byte, so nothing is filtered here.
    """

    kind: str
    attrs: dict[str, Any]

    @property
    def node(self) -> str | None:
        """The device_2D node this widget draws on.

        Almost every widget spells this ``graphics.node``, but the scripting
        specification declares ``static_decoration`` and ``custom_display``
        with ``graphics = { main_node = ... }``. Both spellings are accepted
        on read: dropping one would silently detach those widgets from their
        node, which then reads as "node has no widget" everywhere downstream.
        """
        graphics = self.attrs.get("graphics")
        if isinstance(graphics, dict):
            for key in ("node", "main_node"):
                node = graphics.get(key)
                if isinstance(node, str):
                    return node
        return None

    @property
    def value(self) -> str | None:
        """The motherboard property path the widget binds, if any."""
        value = self.attrs.get("value")
        return value if isinstance(value, str) else None


@dataclass
class HDPanel:
    """One ``jbox.panel{...}`` from ``hdgui_2D.lua``."""

    name: str
    graphics_node: str | None
    widgets: list[Widget]
    attrs: dict[str, Any]

    @property
    def cable_origin_node(self) -> str | None:
        """The node named by this panel's ``cable_origin``, if it declares one.

        Only ``folded_back`` may: the specification says cable_origin is
        "Required for the folded back panel. Must not be present in any other
        panel specification."
        """
        origin = self.attrs.get("cable_origin")
        if isinstance(origin, dict):
            node = origin.get("node")
            if isinstance(node, str):
                return node
        return None


@dataclass
class HDGui2D:
    """Parsed ``hdgui_2D.lua``: the four possible panels and their widgets."""

    format_version: str
    panels: dict[str, HDPanel]
    source_path: Path

    def widgets_for_node(self, node: str) -> list[Widget]:
        """All widgets (across panels) bound to a device_2D node name."""
        return [
            widget
            for panel in self.panels.values()
            for widget in panel.widgets
            if widget.node == node
        ]


def read_hdgui_2d(path: Path | str) -> HDGui2D:
    """Read and parse ``GUI2D/hdgui_2D.lua``."""
    path = Path(path)
    globals_ = _execute_sandboxed(path)
    format_version = _require_format_version(path, globals_)

    panels: dict[str, HDPanel] = {}
    for panel in PANELS:
        table = globals_.get(panel)
        if table is None:
            continue
        if not (isinstance(table, dict) and table.get(_JBOX_TAG) == "panel"):
            raise LuaConfigError(path, f"panel '{panel}' is not a jbox.panel{{...}}")
        panels[panel] = _parse_panel(path, panel, table)
    return HDGui2D(format_version=format_version, panels=panels, source_path=path)


def _parse_panel(path: Path, name: str, table: dict[str, Any]) -> HDPanel:
    attrs = {key: value for key, value in table.items() if key != _JBOX_TAG}

    graphics_node = None
    graphics = attrs.get("graphics")
    if isinstance(graphics, dict) and isinstance(graphics.get("node"), str):
        graphics_node = graphics["node"]

    widgets: list[Widget] = []
    raw_widgets = attrs.get("widgets", [])
    if not isinstance(raw_widgets, list):
        raise LuaConfigError(path, f"panel '{name}': widgets must be an array")
    for index, entry in enumerate(raw_widgets):
        if not (isinstance(entry, dict) and isinstance(entry.get(_JBOX_TAG), str)):
            raise LuaConfigError(
                path, f"panel '{name}': widget {index + 1} is not a jbox constructor call"
            )
        kind = entry[_JBOX_TAG]
        widgets.append(
            Widget(kind=kind, attrs={k: v for k, v in entry.items() if k != _JBOX_TAG})
        )
    return HDPanel(name=name, graphics_node=graphics_node, widgets=widgets, attrs=attrs)


def _require_format_version(path: Path, globals_: dict[str, Any]) -> str:
    version = globals_.get("format_version")
    if not isinstance(version, str):
        raise LuaConfigError(path, "missing or non-string format_version")
    return version
