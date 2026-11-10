"""Add a control to all of a project's files at once (Skou feedback #15).

A new knob needs four edits that must agree: a node in ``device_2D.lua``, a
widget in ``hdgui_2D.lua``, a property in ``motherboard_def.lua`` and its name
in ``Resources/English/texts.lua``. :func:`add_control_sources` makes all four
as anchored insertions, in the same spirit as RE-Blend's patch mode: it
appends to tables it can find exactly once, never reformats what is there,
and refuses (raising :class:`StubError`) rather than guess. Every edited file
is re-parsed before the edit is accepted.

Only the controls whose property shape is unambiguous get a property stub:
``analog_knob`` (a 0..1 number), ``toggle_button`` and ``momentary_button``
(booleans) and ``sequence_fader`` (a stepped number). Binding an existing
property skips the motherboard and texts edits, so any widget type can be
placed on a property that is already declared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from reblend.project.lua_reader import (
    LuaConfigError, _execute_sandboxed_text, read_device_2d_text, read_hdgui_2d_text,
)
from reblend.project.lua_writer import _CODE, _Anchors, PatchError

from .project import DEVICE_2D, HDGUI_2D, MOTHERBOARD, TEXTS

__all__ = ["StubError", "ControlSpec", "WIDGETS", "add_control_sources", "property_tags"]

#: Node-name prefixes dropped when the property name is derived from the node.
_PREFIXES = {"knob", "btn", "button", "fader", "sel", "led", "lamp", "toggle", "switch"}

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class StubError(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass(frozen=True)
class WidgetInfo:
    frames: int
    size: tuple[int, int]
    shape: str
    #: "number", "boolean", "momentary", "stepped", or "" (no stub)
    prop: str


#: Widgets ``add_control`` can create, with grey-box defaults.
WIDGETS: dict[str, WidgetInfo] = {
    "analog_knob": WidgetInfo(61, (120, 120), "knob", "number"),
    "toggle_button": WidgetInfo(2, (100, 100), "button", "boolean"),
    "momentary_button": WidgetInfo(2, (100, 100), "button", "momentary"),
    "sequence_fader": WidgetInfo(3, (60, 240), "fader", "stepped"),
}


@dataclass
class ControlSpec:
    panel: str
    node: str
    widget: str
    x: int
    y: int
    #: The custom property to bind; defaults to the node name.
    bind: str = ""
    title: str = ""
    steps: int = 3
    frames: int = 0

    @property
    def prop(self) -> str:
        if self.bind:
            return self.bind
        head, _, rest = self.node.partition("_")
        return rest if rest and head.lower() in _PREFIXES else self.node


# ---------------------------------------------------------------------------
# Generic anchored append
# ---------------------------------------------------------------------------


def _line_start(source: str, index: int) -> int:
    return source.rfind("\n", 0, index) + 1


def _indent_of(source: str, index: int) -> str:
    start = _line_start(source, index)
    end = start
    while end < len(source) and source[end] in " \t":
        end += 1
    return source[start:end]


def _last_code_index(anchors: _Anchors, before: int) -> int:
    for i in range(before - 1, -1, -1):
        if anchors.mask[i] == _CODE and not anchors.source[i].isspace():
            return i
    return -1


def _append(source: str, span: tuple[int, int], entry: str) -> str:
    """Append ``entry`` (one or more lines, no indent) as the last element of
    the table whose braces are at ``span``."""
    anchors = _Anchors(source)
    open_idx, close_idx = span
    last = _last_code_index(anchors, close_idx)
    multiline = "\n" in source[last:close_idx] or "\n" in source[open_idx:last + 1]
    empty = last == open_idx
    if multiline:
        indent = (_indent_of(source, close_idx) + "  ") if empty else _indent_of(source, last)
        if not empty and source[last] == "}":
            # the last entry closed a multi-line table: indent like its opening line
            indent = _indent_of(source, _matching_open(anchors, last))
        body = "".join(f"{indent}{line}\n" for line in entry.splitlines())
        comma = "" if empty or source[last] in ",;" else ","
        insert_at = _line_start(source, close_idx)
        if source[insert_at:close_idx].strip():
            # closing brace shares a line with content: break the line
            return (source[:last + 1] + comma + "\n" + body + _indent_of(source, open_idx)
                    + source[close_idx:])
        return source[:last + 1] + comma + source[last + 1:insert_at] + body + source[insert_at:]
    comma = "" if empty or source[last] in ",;" else ","
    return source[:last + 1] + comma + " " + entry.replace("\n", " ") + " " + source[close_idx:]


def _matching_open(anchors: _Anchors, close_idx: int) -> int:
    depth = anchors.depths[close_idx]
    for i in range(close_idx - 1, -1, -1):
        if anchors.mask[i] == _CODE and anchors.source[i] == "{" and anchors.depths[i] == depth - 1:
            return i
    return close_idx


def _one(spans: list, what: str, reasons: list[str]):
    if len(spans) != 1:
        reasons.append(f"found {len(spans)} {what}; add it by hand")
        return None
    return spans[0]


def _call_table_spans(anchors: _Anchors, key: str, call: str, start: int, end: int,
                      depth: int) -> list[tuple[int, int]]:
    pattern = re.compile(r"(?<![\w.])%s\s*=\s*%s\s*\(?\s*(?P<brace>\{)"
                         % (re.escape(key), re.escape(call)))
    spans = []
    for m in anchors.code_matches(pattern, start, end):
        brace = m.start("brace")
        if anchors.depths[brace] == depth:
            spans.append(anchors.table_span(brace))
    return spans


def _positional_tables(anchors: _Anchors, span: tuple[int, int]) -> list[tuple[int, int]]:
    """Unkeyed ``{ ... }`` tables directly inside the table at ``span``."""
    open_idx, close_idx = span
    depth = anchors.depths[open_idx] + 1
    out = []
    for i in range(open_idx + 1, close_idx):
        if (anchors.mask[i] == _CODE and anchors.source[i] == "{"
                and anchors.depths[i] == depth):
            prev = _last_code_index(anchors, i)
            if anchors.source[prev] in "{,;":
                out.append(anchors.table_span(i))
    return out


# ---------------------------------------------------------------------------
# Per-file insertions
# ---------------------------------------------------------------------------


def insert_device_node(source: str, panel: str, node: str, x: int, y: int,
                       frames: int, reasons: list[str]) -> str:
    anchors = _Anchors(source)
    span = _one(anchors.find_key_tables(panel, 0, len(source), at_depth=0),
                f"'{panel} = {{' tables in device_2D.lua", reasons)
    if span is None:
        return source
    graphic = f'{{ path = "{node}", frames = {frames} }}' if frames > 1 else f'{{ path = "{node}" }}'
    entry = f"{node} = {{ offset = {{ {x}, {y} }}, {graphic} }},"
    layers = _positional_tables(anchors, span)
    if layers:
        # The last unnamed group draws on top: new controls go there.
        return _append(source, layers[-1], entry)
    return _append(source, span, "{\n  " + entry + "\n},")


def _widget_text(spec: ControlSpec) -> str:
    attrs = [f'graphics = {{ node = "{spec.node}" }}']
    if spec.widget == "sequence_fader":
        attrs.append("handle_size = 0")
    attrs.append(f'value = "/custom_properties/{spec.prop}"')
    return f"jbox.{spec.widget} {{ " + ", ".join(attrs) + " },"


def insert_hdgui_widget(source: str, spec: ControlSpec, reasons: list[str]) -> str:
    anchors = _Anchors(source)
    panel = _one(_call_table_spans(anchors, spec.panel, "jbox.panel", 0, len(source), 0),
                 f"'{spec.panel} = jbox.panel {{' tables in hdgui_2D.lua", reasons)
    if panel is None:
        return source
    widgets = _one(anchors.find_key_tables("widgets", panel[0], panel[1],
                                           at_depth=anchors.depths[panel[0]] + 1),
                   f"'widgets = {{' tables in the {spec.panel} panel of hdgui_2D.lua", reasons)
    if widgets is None:
        return source
    return _append(source, widgets, _widget_text(spec))


def property_tags(globals_: dict) -> set[int]:
    tags: set[int] = set()

    def walk(value):
        if isinstance(value, dict):
            tag = value.get("property_tag")
            if isinstance(tag, (int, float)):
                tags.add(int(tag))
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    walk(globals_.get("custom_properties"))
    return tags


def _property_text(spec: ControlSpec, kind: str, tag: int) -> str:
    name = spec.prop
    head = f"{name} = jbox.%s {{\n  property_tag = {tag},\n"
    ui_name = f'  ui_name = jbox.ui_text("{name}"),\n'
    if kind == "number":
        return (head % "number" + "  default = 0.5,\n" + ui_name
                + "  ui_type = jbox.ui_percent { decimals = 0 },\n},")
    if kind in ("boolean", "momentary"):
        persistence = '  persistence = "none",\n' if kind == "momentary" else ""
        return (head % "boolean" + "  default = false,\n" + persistence + ui_name
                + "  ui_type = jbox.ui_selector { jbox.UI_TEXT_OFF, jbox.UI_TEXT_ON },\n},")
    items = ", ".join(f'jbox.ui_text("{name}_{i + 1}")' for i in range(spec.steps))
    return (head % "number" + f"  default = 0,\n  steps = {spec.steps},\n" + ui_name
            + f"  ui_type = jbox.ui_selector {{ {items} }},\n}},")


def insert_property(source: str, spec: ControlSpec, kind: str, tag: int,
                    reasons: list[str]) -> str:
    anchors = _Anchors(source)
    props = _one(_call_table_spans(anchors, "custom_properties", "jbox.property_set",
                                   0, len(source), 0),
                 "'custom_properties = jbox.property_set {' in motherboard_def.lua", reasons)
    if props is None:
        return source
    owner = _one(anchors.find_key_tables("document_owner", props[0], props[1],
                                         at_depth=anchors.depths[props[0]] + 1),
                 "'document_owner = {' tables in custom_properties", reasons)
    if owner is None:
        return source
    table = _one(anchors.find_key_tables("properties", owner[0], owner[1],
                                         at_depth=anchors.depths[owner[0]] + 1),
                 "'properties = {' tables in document_owner", reasons)
    if table is None:
        return source
    return _append(source, table, _property_text(spec, kind, tag))


def insert_texts(source: str, entries: list[tuple[str, str]], reasons: list[str]) -> str:
    for key, text in entries:
        anchors = _Anchors(source)
        span = _one(anchors.find_key_tables("texts", 0, len(source), at_depth=0),
                    "'texts = {' tables in texts.lua", reasons)
        if span is None:
            return source
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        source = _append(source, span, f'{key} = "{escaped}",')
    return source


# ---------------------------------------------------------------------------
# The whole edit
# ---------------------------------------------------------------------------


def _title(name: str) -> str:
    words = [w for w in re.split(r"[_\W]+", name) if w]
    if words and words[0].lower() in _PREFIXES:
        words = words[1:] or words
    return " ".join(w.capitalize() for w in words) or name


def add_control_sources(sources: dict[str, str], spec: ControlSpec, root: Path) -> dict[str, str]:
    """Return the edited sources (only the files that changed).

    Raises :class:`StubError` with every reason the edit cannot be made.
    """
    reasons: list[str] = []
    info = WIDGETS.get(spec.widget)
    for name, value in (("node", spec.node), ("property", spec.prop)):
        if not _IDENT.match(value):
            reasons.append(f"{name} '{value}' is not a Lua identifier")
        elif name == "property" and len(value) > 34:
            reasons.append(f"property '{value}' is longer than 34 characters (SDK limit)")
    if spec.panel not in ("front", "back", "folded_front", "folded_back"):
        reasons.append(f"unknown panel '{spec.panel}'")
    if reasons:
        raise StubError(reasons)

    device = read_device_2d_text(sources[DEVICE_2D], str(root / DEVICE_2D))
    hdgui = read_hdgui_2d_text(sources[HDGUI_2D], str(root / HDGUI_2D))
    if device.node(spec.panel, spec.node) is not None:
        reasons.append(f"node '{spec.node}' already exists on the {spec.panel} panel")

    mb_globals: dict = {}
    if MOTHERBOARD in sources:
        try:
            mb_globals = _execute_sandboxed_text(sources[MOTHERBOARD], root / MOTHERBOARD)
        except LuaConfigError as exc:
            reasons.append(f"motherboard_def.lua does not load: {exc}")
    declared = _declared_properties(mb_globals)
    needs_stub = spec.prop not in declared
    if needs_stub and (info is None or not info.prop):
        reasons.append(
            f"'{spec.widget}' has no property stub: declare '{spec.prop}' in "
            "motherboard_def.lua first, or use one of " + ", ".join(sorted(WIDGETS)))
    if needs_stub and MOTHERBOARD not in sources:
        reasons.append("no motherboard_def.lua to add the property to")
    if needs_stub and TEXTS not in sources:
        reasons.append(f"no {TEXTS} for the property's name")
    if reasons:
        raise StubError(reasons)

    frames = spec.frames or (spec.steps if spec.widget == "sequence_fader" else
                             (info.frames if info else 1))
    out: dict[str, str] = {}
    out[DEVICE_2D] = insert_device_node(sources[DEVICE_2D], spec.panel, spec.node,
                                        spec.x, spec.y, frames, reasons)
    out[HDGUI_2D] = insert_hdgui_widget(sources[HDGUI_2D], spec, reasons)
    if needs_stub:
        used = property_tags(mb_globals)
        tag = next(t for t in range(1, len(used) + 2) if t not in used)
        out[MOTHERBOARD] = insert_property(sources[MOTHERBOARD], spec, info.prop, tag, reasons)
        texts = [(spec.prop, spec.title or _title(spec.prop))]
        if info.prop == "stepped":
            texts += [(f"{spec.prop}_{i + 1}", str(i + 1)) for i in range(spec.steps)]
        existing = _existing_texts(sources[TEXTS], root)
        out[TEXTS] = insert_texts(sources[TEXTS], [t for t in texts if t[0] not in existing],
                                  reasons)
    if reasons:
        raise StubError(reasons)
    _verify(out, spec, root, needs_stub)
    return out


def _declared_properties(globals_: dict) -> set[str]:
    names: set[str] = set()
    custom = globals_.get("custom_properties")
    if isinstance(custom, dict):
        for owner in custom.values():
            if isinstance(owner, dict) and isinstance(owner.get("properties"), dict):
                names.update(k for k in owner["properties"] if isinstance(k, str))
    return names


def _existing_texts(source: str, root: Path) -> set[str]:
    try:
        texts = _execute_sandboxed_text(source, root / TEXTS).get("texts")
    except LuaConfigError:
        return set()
    return set(texts) if isinstance(texts, dict) else set()


def _verify(out: dict[str, str], spec: ControlSpec, root: Path, stubbed: bool) -> None:
    problems: list[str] = []
    try:
        device = read_device_2d_text(out[DEVICE_2D], str(root / DEVICE_2D))
        if device.node(spec.panel, spec.node) is None:
            problems.append("device_2D.lua re-parsed without the new node")
        hdgui = read_hdgui_2d_text(out[HDGUI_2D], str(root / HDGUI_2D))
        if not any(w.node == spec.node for w in hdgui.panels[spec.panel].widgets):
            problems.append("hdgui_2D.lua re-parsed without the new widget")
        if stubbed:
            mb = _execute_sandboxed_text(out[MOTHERBOARD], root / MOTHERBOARD)
            if spec.prop not in _declared_properties(mb):
                problems.append("motherboard_def.lua re-parsed without the new property")
            if spec.prop not in _existing_texts(out[TEXTS], root):
                problems.append("texts.lua re-parsed without the new name")
    except (LuaConfigError, PatchError, KeyError) as exc:
        problems.append(f"an edited file no longer loads: {exc}")
    if problems:
        raise StubError(problems)
