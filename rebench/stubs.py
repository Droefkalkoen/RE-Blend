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

__all__ = ["StubError", "ControlSpec", "WIDGETS", "add_control_sources", "remove_control_sources",
           "property_tags"]

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


# ---------------------------------------------------------------------------
# Removing a control
# ---------------------------------------------------------------------------


def _cut(source: str, start: int, end: int) -> str:
    """Remove ``source[start:end]`` (one table entry) with its separator.

    An entry alone on its lines takes those lines with it; an entry sharing a
    line loses its trailing comma and one space. Nothing else is reformatted.
    """
    tail = end
    while tail < len(source) and source[tail] in " \t":
        tail += 1
    if tail < len(source) and source[tail] in ",;":
        tail += 1
    line_start = _line_start(source, start)
    eol = source.find("\n", tail)
    eol = len(source) if eol < 0 else eol
    if not source[line_start:start].strip() and not source[tail:eol].strip():
        return source[:line_start] + source[min(eol + 1, len(source)):]
    while tail < len(source) and source[tail] == " ":
        tail += 1
    return source[:start] + source[tail:]


def _entry_spans(anchors: _Anchors, key: str, start: int, end: int) -> list[tuple[int, int]]:
    """``(entry start, closing brace + 1)`` of each ``key = { ... }`` in code."""
    pattern = re.compile(r"(?<![\w.])%s\s*=\s*(?P<brace>\{)" % re.escape(key))
    spans = []
    for m in anchors.code_matches(pattern, start, end):
        open_idx, close_idx = anchors.table_span(m.start("brace"))
        spans.append((m.start(), close_idx + 1))
    return spans


def remove_device_node(source: str, panel: str, node: str, reasons: list[str]) -> str:
    anchors = _Anchors(source)
    span = _one(anchors.find_key_tables(panel, 0, len(source), at_depth=0),
                f"'{panel} = {{' tables in device_2D.lua", reasons)
    if span is None:
        return source
    entry = _one(_entry_spans(anchors, node, span[0] + 1, span[1]),
                 f"'{node} = {{' nodes on the {panel} panel of device_2D.lua", reasons)
    if entry is None:
        return source
    return _cut(source, *entry)


_WIDGET_CALL = re.compile(r"(?<![\w.])jbox\s*\.\s*(?P<kind>\w+)\s*\(?\s*(?P<brace>\{)")
_NODE_ATTR = r"""node\s*=\s*["']%s["']"""


def remove_hdgui_widgets(source: str, panel: str, node: str, reasons: list[str]) -> tuple[str, int]:
    """Remove every widget on ``panel`` whose graphics name ``node``."""
    anchors = _Anchors(source)
    panel_span = _one(_call_table_spans(anchors, panel, "jbox.panel", 0, len(source), 0),
                      f"'{panel} = jbox.panel {{' tables in hdgui_2D.lua", reasons)
    if panel_span is None:
        return source, 0
    widgets = _one(anchors.find_key_tables("widgets", panel_span[0], panel_span[1],
                                           at_depth=anchors.depths[panel_span[0]] + 1),
                   f"'widgets = {{' tables in the {panel} panel of hdgui_2D.lua", reasons)
    if widgets is None:
        return source, 0
    depth = anchors.depths[widgets[0]] + 1
    node_re = re.compile(_NODE_ATTR % re.escape(node))
    doomed = []
    for m in anchors.code_matches(_WIDGET_CALL, widgets[0] + 1, widgets[1]):
        brace = m.start("brace")
        if anchors.depths[brace] != depth:
            continue
        open_idx, close_idx = anchors.table_span(brace)
        end = close_idx + 1
        if "(" in source[m.start():brace]:
            # jbox.x({ ... }) call syntax: take the closing parenthesis too
            close_paren = source.find(")", end)
            if close_paren >= 0 and not source[end:close_paren].strip():
                end = close_paren + 1
        if any(anchors.mask[x.start()] == _CODE
               for x in node_re.finditer(source, open_idx, close_idx)):
            doomed.append((m.start(), end))
    for start, end in reversed(doomed):
        source = _cut(source, start, end)
    return source, len(doomed)


def remove_property(source: str, name: str, reasons: list[str]) -> str:
    anchors = _Anchors(source)
    props = _one(_call_table_spans(anchors, "custom_properties", "jbox.property_set",
                                   0, len(source), 0),
                 "'custom_properties = jbox.property_set {' in motherboard_def.lua", reasons)
    if props is None:
        return source
    pattern = re.compile(r"(?<![\w.])%s\s*=\s*jbox\s*\.\s*\w+\s*\(?\s*(?P<brace>\{)"
                         % re.escape(name))
    found = []
    for m in anchors.code_matches(pattern, props[0], props[1]):
        _o, close_idx = anchors.table_span(m.start("brace"))
        end = close_idx + 1
        rest = source[end:end + 8].lstrip(" \t")
        if rest.startswith(")"):
            end = source.index(")", end) + 1
        found.append((m.start(), end))
    entry = _one(found, f"'{name} = jbox.<type> {{' properties in motherboard_def.lua", reasons)
    return source if entry is None else _cut(source, *entry)


def remove_texts(source: str, keys: list[str]) -> str:
    """Remove ``key = "..."`` lines from ``texts = {`` (absent keys are fine)."""
    for key in keys:
        anchors = _Anchors(source)
        spans = anchors.find_key_tables("texts", 0, len(source), at_depth=0)
        if len(spans) != 1:
            return source
        pattern = re.compile(r"""(?<![\w.])(?:%s|\[\s*["']%s["']\s*\])\s*=\s*"(?:[^"\\\n]|\\.)*\""""
                             % (re.escape(key), re.escape(key)))
        matches = anchors.code_matches(pattern, spans[0][0], spans[0][1])
        if len(matches) == 1:
            source = _cut(source, matches[0].start(), matches[0].end())
    return source


def remove_control_sources(sources: dict[str, str], panel: str, node: str, root: Path,
                           drop_property: bool = False) -> tuple[dict[str, str], list[str]]:
    """Remove a node and the widgets drawn on it; with ``drop_property``, also
    the property they bind and its texts, when nothing else binds it.

    Returns ``(edited sources, notes)``. Raises :class:`StubError` on any
    ambiguity. Property tags are permanent once a device is released, so only
    drop a property from a device that has never shipped.
    """
    reasons: list[str] = []
    notes: list[str] = []
    device = read_device_2d_text(sources[DEVICE_2D], str(root / DEVICE_2D))
    hdgui = read_hdgui_2d_text(sources[HDGUI_2D], str(root / HDGUI_2D))
    if device.node(panel, node) is None:
        raise StubError([f"no node '{node}' on the {panel} panel"])
    hd_panel = hdgui.panels.get(panel)
    bound = [w for w in (hd_panel.widgets if hd_panel else []) if w.node == node]
    props = sorted({w.value.rsplit("/", 1)[-1] for w in bound
                    if isinstance(w.value, str) and w.value.startswith("/custom_properties/")})

    out = {DEVICE_2D: remove_device_node(sources[DEVICE_2D], panel, node, reasons)}
    out[HDGUI_2D], removed = remove_hdgui_widgets(sources[HDGUI_2D], panel, node, reasons)
    if removed != len(bound):
        reasons.append(f"found {removed} of the {len(bound)} widgets on '{node}' to remove; "
                       "remove them by hand")
    if drop_property and props:
        others = {w.value for p in hdgui.panels.values() for w in p.widgets
                  if not (p is hd_panel and w.node == node) and isinstance(w.value, str)}
        for name in props:
            if f"/custom_properties/{name}" in others:
                notes.append(f"kept property '{name}': other widgets still bind it")
                continue
            if MOTHERBOARD not in sources:
                continue
            out[MOTHERBOARD] = remove_property(out.get(MOTHERBOARD, sources[MOTHERBOARD]),
                                               name, reasons)
            if TEXTS in sources:
                keys = [name] + [f"{name}_{i + 1}" for i in range(64)]
                out[TEXTS] = remove_texts(out.get(TEXTS, sources[TEXTS]), keys)
            notes.append(f"removed property '{name}' (its tag is free again only while "
                         "the device is unreleased)")
    if reasons:
        raise StubError(reasons)
    try:
        if read_device_2d_text(out[DEVICE_2D], str(root / DEVICE_2D)).node(panel, node):
            reasons.append("device_2D.lua re-parsed with the node still there")
        hd = read_hdgui_2d_text(out[HDGUI_2D], str(root / HDGUI_2D))
        if any(w.node == node for w in hd.panels[panel].widgets):
            reasons.append("hdgui_2D.lua re-parsed with a widget still on the node")
        if MOTHERBOARD in out:
            _execute_sandboxed_text(out[MOTHERBOARD], root / MOTHERBOARD)
        if TEXTS in out:
            _execute_sandboxed_text(out[TEXTS], root / TEXTS)
    except (LuaConfigError, PatchError, KeyError) as exc:
        reasons.append(f"an edited file no longer loads: {exc}")
    if reasons:
        raise StubError(reasons)
    return {k: v for k, v in out.items() if v != sources.get(k)}, notes
