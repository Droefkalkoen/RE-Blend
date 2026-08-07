"""Patch-mode writing of ``device_2D.lua`` (§6.2, risk §10.2).

RE-Blend owns exactly two kinds of values in an RE project's Lua: the
``offset = { x, y }`` and ``frames = N`` number literals of nodes it knows.
Patch mode rewrites *only those literals*, as anchored structural edits on the
original text — never a reserialisation — so hand-written comments and
formatting (load-bearing documentation in these files) survive byte-for-byte.

The safety contract, in order of defence:

1. **Anchoring is comment- and string-aware.** A lexer pass masks Lua
   comments and string contents, so a decoy ``offset = { 9, 9 }`` inside a
   comment can never be matched, and brace depths are computed over code only.
2. **On any ambiguity, refuse.** A node found twice in a panel, an offset
   that is not two plain number literals, a missing field, several graphics
   with the target path — every anchor failure collects into one
   :class:`PatchError` and *nothing* is written. Never guess, never partial.
3. **Verify before write.** The patched text is re-parsed through the same
   sandboxed interpreter that read it, and the resulting tree must equal the
   original tree with exactly the requested edits applied. Only then does
   :func:`patch_device_2d_file` replace the file (atomically, preserving the
   original bytes' line endings and encoding).

``hdgui_2D.lua`` is never patched — RE-Blend owns no values in it — and
``motherboard_def.lua`` is never touched at all (§6.2).
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from ..model.schema import ElementData
from .lua_reader import Device2D, Graphic, Node2D, read_device_2d_text

__all__ = [
    "OffsetEdit",
    "FramesEdit",
    "PatchError",
    "PatchResult",
    "node_base_offset",
    "compute_device_edits",
    "patch_device_2d",
    "patch_device_2d_file",
]


@dataclass(frozen=True)
class OffsetEdit:
    """Set a node's ``offset`` literals. ``x``/``y`` are panel px *relative to
    the node's parent group*, exactly as the file stores them."""

    panel: str
    node: str
    x: float
    y: float


@dataclass(frozen=True)
class FramesEdit:
    """Set ``frames`` on the node's graphic entry whose ``path`` matches."""

    panel: str
    node: str
    path: str
    frames: int


class PatchError(Exception):
    """The edits cannot be applied safely; nothing was (or must be) written."""

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = list(reasons)
        super().__init__(
            "refusing to patch device_2D.lua:\n  - " + "\n  - ".join(self.reasons)
        )


@dataclass
class PatchResult:
    """Outcome of a successful patch: the new source and what changed."""

    source: str
    applied: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.applied)


# ---------------------------------------------------------------------------
# Lexical mask: which characters are code vs comment/string content
# ---------------------------------------------------------------------------

_CODE, _COMMENT, _STRING = "c", "-", "s"


def _lex_mask(source: str) -> str:
    """Per-character region map of Lua source: code, comment, or string.

    Handles line comments, ``--[[ ]]`` / ``--[=[ ]=]`` long comments, quoted
    strings with backslash escapes, and ``[[ ]]`` / ``[=[ ]=]`` long strings.
    An unterminated region runs to the end (the interpreter pass that always
    precedes patching would have rejected such a file anyway).
    """
    mask = [_CODE] * len(source)
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch == "-" and source.startswith("--", i):
            end = _long_bracket_end(source, i + 2)
            if end is None:  # plain line comment
                end = source.find("\n", i)
                end = n if end == -1 else end
            for j in range(i, end):
                mask[j] = _COMMENT
            i = end
        elif ch in "\"'":
            j = i + 1
            while j < n and source[j] != ch:
                j += 2 if source[j] == "\\" else 1
            end = min(j + 1, n)
            for k in range(i, end):
                mask[k] = _STRING
            i = end
        elif ch == "[":
            end = _long_bracket_end(source, i)
            if end is None:
                i += 1
            else:
                for j in range(i, end):
                    mask[j] = _STRING
                i = end
        else:
            i += 1
    return "".join(mask)


def _long_bracket_end(source: str, start: int) -> int | None:
    """If a long bracket ``[=*[`` opens at ``start``, index past its closing
    ``]=*]`` (or end of source when unterminated); else ``None``."""
    if start >= len(source) or source[start] != "[":
        return None
    i = start + 1
    while i < len(source) and source[i] == "=":
        i += 1
    if i >= len(source) or source[i] != "[":
        return None
    closer = "]" + "=" * (i - start - 1) + "]"
    end = source.find(closer, i + 1)
    return len(source) if end == -1 else end + len(closer)


def _depths(source: str, mask: str) -> list[int]:
    """``depth[i]`` = brace depth *before* consuming ``source[i]`` (code only)."""
    depths = [0] * (len(source) + 1)
    depth = 0
    for i, ch in enumerate(source):
        depths[i] = depth
        if mask[i] == _CODE:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
    depths[len(source)] = depth
    return depths


# ---------------------------------------------------------------------------
# Anchoring
# ---------------------------------------------------------------------------


#: ``name = {`` with an identifier or ["quoted"] key; group 'brace' is the
#: opening brace of the value table.
def _key_table_re(key: str) -> re.Pattern:
    quoted = re.escape(key)
    return re.compile(
        r"(?:(?<![\w.])%s|\[\s*([\"'])%s\1\s*\])\s*=\s*(?P<brace>\{)" % (quoted, quoted)
    )


_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_FRAMES_RE = re.compile(r"(?<![\w.])frames\s*=\s*(?P<num>-?\d+(?:\.\d+)?)")
_PATH_RE = re.compile(r"(?<![\w.])path\s*=\s*([\"'])(?P<value>(?:[^\"'\\]|\\.)*?)\1")


class _Anchors:
    """One patching session's view of the source: mask, depths, spans."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.mask = _lex_mask(source)
        self.depths = _depths(source, self.mask)

    def table_span(self, open_idx: int) -> tuple[int, int]:
        """(open, close) indices of the braces of the table opening at open_idx."""
        target = self.depths[open_idx]
        for i in range(open_idx + 1, len(self.source)):
            if self.mask[i] == _CODE and self.source[i] == "}" and self.depths[i] == target + 1:
                return open_idx, i
        raise PatchError([f"unbalanced table at index {open_idx}"])

    def find_key_tables(
        self, key: str, start: int, end: int, at_depth: int | None
    ) -> list[tuple[int, int]]:
        """Spans of ``key = { ... }`` inside [start, end) whose match starts in
        code (never a comment/string decoy), optionally pinned to a depth."""
        spans = []
        for match in _key_table_re(key).finditer(self.source, start, end):
            if self.mask[match.start()] != _CODE:
                continue
            brace = match.start("brace")
            if at_depth is not None and self.depths[brace] != at_depth:
                continue
            spans.append(self.table_span(brace))
        return spans

    def code_matches(self, pattern: re.Pattern, start: int, end: int) -> list[re.Match]:
        return [
            m for m in pattern.finditer(self.source, start, end)
            if self.mask[m.start()] == _CODE
        ]

    def next_code_char(self, index: int) -> str:
        """First significant code character at or after ``index`` ('' at end)."""
        for i in range(index, len(self.source)):
            if self.mask[i] == _CODE and not self.source[i].isspace():
                return self.source[i]
        return ""

    def prev_code_char(self, index: int) -> str:
        """Last significant code character before ``index`` ('' at start)."""
        for i in range(index - 1, -1, -1):
            if self.mask[i] == _CODE and not self.source[i].isspace():
                return self.source[i]
        return ""


def _format_number(value: float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


# ---------------------------------------------------------------------------
# Edit → replacement resolution
# ---------------------------------------------------------------------------


def _panel_span(anchors: _Anchors, panel: str, refusals: list[str]) -> tuple[int, int] | None:
    spans = anchors.find_key_tables(panel, 0, len(anchors.source), at_depth=0)
    if len(spans) != 1:
        refusals.append(
            f"panel '{panel}': found {len(spans)} top-level '{panel} = {{' "
            "assignments, need exactly 1"
        )
        return None
    return spans[0]


def _node_span(
    anchors: _Anchors, panel_span: tuple[int, int], panel: str, node: str,
    refusals: list[str],
) -> tuple[int, int] | None:
    spans = anchors.find_key_tables(node, panel_span[0] + 1, panel_span[1], at_depth=None)
    if len(spans) != 1:
        refusals.append(
            f"{panel}/{node}: found {len(spans)} '{node} = {{' anchors in the "
            "panel, need exactly 1"
        )
        return None
    return spans[0]


def _number_literals(
    anchors: _Anchors, start: int, end: int
) -> list[re.Match] | None:
    """The number literals in [start, end), or None when the code there is not
    just numbers separated by commas (an expression — refuse, don't guess)."""
    matches = anchors.code_matches(_NUMBER_RE, start, end)
    taken = [False] * (end - start)
    for m in matches:
        for i in range(m.start() - start, m.end() - start):
            taken[i] = True
    for i in range(start, end):
        if anchors.mask[i] != _CODE or taken[i - start]:
            continue
        if not (anchors.source[i].isspace() or anchors.source[i] == ","):
            return None
    return matches


def _resolve_offset(
    anchors: _Anchors, node_span: tuple[int, int], edit: OffsetEdit,
    refusals: list[str],
) -> list[tuple[int, int, str]]:
    where = f"{edit.panel}/{edit.node}"
    open_idx, close_idx = node_span
    spans = anchors.find_key_tables(
        "offset", open_idx + 1, close_idx, at_depth=anchors.depths[open_idx] + 1
    )
    if not spans:
        refusals.append(
            f"{where}: node has no 'offset = {{ x, y }}' field to rewrite — "
            "add one by hand (or use generate mode), then patch"
        )
        return []
    if len(spans) > 1:
        refusals.append(f"{where}: {len(spans)} offset fields — ambiguous")
        return []
    o_open, o_close = spans[0]
    numbers = _number_literals(anchors, o_open + 1, o_close)
    if numbers is None or len(numbers) != 2:
        refusals.append(
            f"{where}: offset is not two plain number literals — patch mode "
            "only rewrites literal values (§10.2)"
        )
        return []
    return [
        (numbers[0].start(), numbers[0].end(), _format_number(edit.x)),
        (numbers[1].start(), numbers[1].end(), _format_number(edit.y)),
    ]


def _graphic_spans(
    anchors: _Anchors, node_span: tuple[int, int]
) -> list[tuple[int, int]]:
    """Spans of the node's array-part graphic tables: ``{`` entries one level
    inside the node whose preceding code is not ``=`` (those are offset/child
    values, not graphics)."""
    open_idx, close_idx = node_span
    inner_depth = anchors.depths[open_idx] + 1
    spans = []
    i = open_idx + 1
    while i < close_idx:
        if (
            anchors.mask[i] == _CODE
            and anchors.source[i] == "{"
            and anchors.depths[i] == inner_depth
            and anchors.prev_code_char(i) in ("{", ",", ";")
        ):
            span = anchors.table_span(i)
            spans.append(span)
            i = span[1] + 1
        else:
            i += 1
    return spans


def _resolve_frames(
    anchors: _Anchors, node_span: tuple[int, int], edit: FramesEdit,
    refusals: list[str],
) -> list[tuple[int, int, str]]:
    where = f"{edit.panel}/{edit.node}"
    matching = []
    for span in _graphic_spans(anchors, node_span):
        paths = anchors.code_matches(_PATH_RE, span[0] + 1, span[1])
        if any(m.group("value") == edit.path for m in paths):
            matching.append(span)
    if len(matching) != 1:
        refusals.append(
            f"{where}: found {len(matching)} graphic entries with path "
            f"'{edit.path}', need exactly 1"
        )
        return []
    g_open, g_close = matching[0]
    frames = anchors.code_matches(_FRAMES_RE, g_open + 1, g_close)
    if not frames:
        refusals.append(
            f"{where}: graphic '{edit.path}' has no 'frames = N' literal to "
            "rewrite (an absent field means 1) — add one by hand, then patch"
        )
        return []
    if len(frames) > 1:
        refusals.append(f"{where}: graphic '{edit.path}' has {len(frames)} frames fields")
        return []
    match = frames[0]
    if anchors.next_code_char(match.end()) not in (",", "}", ";"):
        refusals.append(
            f"{where}: 'frames' value of '{edit.path}' is not a plain number "
            "literal — patch mode only rewrites literal values (§10.2)"
        )
        return []
    return [(match.start("num"), match.end("num"), _format_number(edit.frames))]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def patch_device_2d(
    source: str, edits: Iterable[OffsetEdit | FramesEdit]
) -> PatchResult:
    """Apply edits to device_2D source text; refuse (raise) before guessing.

    Raises :class:`PatchError` carrying *every* refusal reason at once, and
    :class:`~.lua_reader.LuaConfigError` when the source itself does not parse.
    On success the returned source has been **verified**: re-parsed and
    compared against the original tree with exactly the requested edits.
    """
    device = read_device_2d_text(source)
    edits = list(dict.fromkeys(edits))  # drop exact duplicates, keep order

    refusals: list[str] = []
    live: list[tuple[OffsetEdit | FramesEdit, str]] = []
    unchanged: list[str] = []
    for edit in edits:
        description, noop, reason = _check_edit(device, edit)
        if reason:
            refusals.append(reason)
        elif noop:
            unchanged.append(description)
        else:
            live.append((edit, description))

    anchors = _Anchors(source)
    replacements: list[tuple[int, int, str]] = []
    panel_spans: dict[str, tuple[int, int] | None] = {}
    for edit, _ in live:
        if edit.panel not in panel_spans:
            panel_spans[edit.panel] = _panel_span(anchors, edit.panel, refusals)
        panel_span = panel_spans[edit.panel]
        if panel_span is None:
            continue
        node_span = _node_span(anchors, panel_span, edit.panel, edit.node, refusals)
        if node_span is None:
            continue
        if isinstance(edit, OffsetEdit):
            replacements += _resolve_offset(anchors, node_span, edit, refusals)
        else:
            replacements += _resolve_frames(anchors, node_span, edit, refusals)

    if refusals:
        raise PatchError(refusals)

    patched = _apply_replacements(source, replacements)
    _verify(device, patched, (edit for edit, _ in live))
    return PatchResult(
        source=patched, applied=[d for _, d in live], unchanged=unchanged
    )


def patch_device_2d_file(
    path: Path | str, edits: Iterable[OffsetEdit | FramesEdit]
) -> PatchResult:
    """Patch ``device_2D.lua`` on disk, atomically, only after verification.

    Bytes are decoded/encoded as UTF-8 without newline translation, so CRLF
    files written on Windows stay CRLF everywhere the patch didn't touch
    (which, line endings being outside offset/frames literals, is everywhere).
    """
    path = Path(path)
    source = path.read_bytes().decode("utf-8")
    result = patch_device_2d(source, edits)
    if result.dirty:
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(result.source.encode("utf-8"))
            # mkstemp files are 0600; keep the original's permissions so a
            # group-readable checkout stays readable to CI and teammates.
            os.chmod(tmp_name, os.stat(path).st_mode & 0o7777)
            os.replace(tmp_name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    return result


def _check_edit(
    device: Device2D, edit: OffsetEdit | FramesEdit
) -> tuple[str, bool, str | None]:
    """(description, is_noop, refusal_reason) for one edit against the tree."""
    node = device.node(edit.panel, edit.node)
    where = f"{edit.panel}/{edit.node}"
    if node is None:
        return where, False, f"{where}: node not found in device_2D.lua"

    if isinstance(edit, OffsetEdit):
        current = node.offset if node.offset is not None else (0.0, 0.0)
        desired = (float(edit.x), float(edit.y))
        description = (
            f"{where}: offset {{{_format_number(current[0])}, "
            f"{_format_number(current[1])}}} -> {{{_format_number(desired[0])}, "
            f"{_format_number(desired[1])}}}"
        )
        if tuple(map(float, current)) == desired:
            return description, True, None
        if node.offset is None:
            # No literal to rewrite; surfaced as an anchor refusal with advice.
            return description, False, (
                f"{where}: node has no 'offset' field to rewrite — add "
                "'offset = { 0, 0 }' by hand (or use generate mode), then patch"
            )
        return description, False, None

    graphics = [g for g in node.graphics if g.path == edit.path]
    if len(graphics) != 1:
        return where, False, (
            f"{where}: {len(graphics)} graphics with path '{edit.path}', "
            "need exactly 1"
        )
    description = f"{where}: frames {graphics[0].frames} -> {int(edit.frames)}"
    if int(edit.frames) < 1:
        return description, False, f"{where}: frames must be >= 1, got {edit.frames}"
    return description, graphics[0].frames == int(edit.frames), None


def _apply_replacements(
    source: str, replacements: list[tuple[int, int, str]]
) -> str:
    ordered = sorted(replacements, key=lambda r: r[0])
    for (_, end_a, _), (start_b, _, _) in zip(ordered, ordered[1:]):
        if end_a > start_b:
            raise PatchError(["internal: overlapping edits — refusing to write"])
    out, cursor = [], 0
    for start, end, text in ordered:
        out.append(source[cursor:start])
        out.append(text)
        cursor = end
    out.append(source[cursor:])
    return "".join(out)


def _verify(
    expected: Device2D, patched: str, edits: Iterable[OffsetEdit | FramesEdit]
) -> None:
    """The whole point (§10.2): parse the patched text and demand it equal the
    original tree with exactly the requested edits — nothing more, nothing
    less, or nothing gets written.

    ``expected`` is the original source's parse, mutated in place (its caller
    is done with it) — the one interpreter run that must still happen here is
    over the *patched* text.
    """
    for edit in edits:
        node = expected.node(edit.panel, edit.node)
        assert node is not None  # _check_edit guaranteed it
        if isinstance(edit, OffsetEdit):
            node.offset = (float(edit.x), float(edit.y))
        else:
            node.graphics = [
                Graphic(path=g.path, frames=int(edit.frames)) if g.path == edit.path else g
                for g in node.graphics
            ]
    actual = read_device_2d_text(patched)
    if actual.panels != expected.panels or actual.format_version != expected.format_version:
        raise PatchError(
            ["verification failed: patched text does not parse to exactly the "
             "requested edits — refusing to write"]
        )


# ---------------------------------------------------------------------------
# Scene → edits
# ---------------------------------------------------------------------------


def node_base_offset(
    device: Device2D, panel: str, name: str
) -> tuple[float, float] | None:
    """Summed ancestor offsets of a node (its own offset excluded), or None.

    Placements carry *absolute* panel px (group offsets folded in, §6.1);
    the file stores offsets *relative* to the enclosing group. This is the
    conversion back.
    """

    def walk(node: Node2D, base_x: float, base_y: float):
        if node.name == name:
            return (base_x, base_y)
        x = base_x + (node.offset[0] if node.offset else 0.0)
        y = base_y + (node.offset[1] if node.offset else 0.0)
        for child in node.children.values():
            found = walk(child, x, y)
            if found is not None:
                return found
        return None

    for root in device.panels.get(panel, {}).values():
        found = walk(root, 0.0, 0.0)
        if found is not None:
            return found
    return None


def compute_device_edits(
    device: Device2D, elements: Sequence[ElementData]
) -> tuple[list[OffsetEdit | FramesEdit], list[str]]:
    """Diff scene elements against the parsed file into the edits patch mode
    may apply, plus notes for what was skipped (unknown nodes — sync first).

    Only differences become edits, so an untouched project patches to a
    byte-identical file.
    """
    edits: dict[tuple, OffsetEdit | FramesEdit] = {}
    notes: list[str] = []
    for element in elements:
        # Export writes where the scene puts things, which is the registration
        # empty's reading when the Blender side supplied one.
        for placement in element.effective_placements:
            node = device.node(placement.panel, placement.node)
            if node is None:
                notes.append(
                    f"{placement.panel}/{placement.node}: not in device_2D.lua — "
                    "skipped (run Check for Differences to review adds/removes)"
                )
                continue

            base = node_base_offset(device, placement.panel, placement.node)
            assert base is not None  # node was found above
            rel_x = round(placement.x - base[0])
            rel_y = round(placement.y - base[1])
            current = node.offset if node.offset is not None else (0.0, 0.0)
            if (float(rel_x), float(rel_y)) != tuple(map(float, current)):
                key = ("offset", placement.panel, placement.node)
                edits[key] = OffsetEdit(placement.panel, placement.node, rel_x, rel_y)

            for graphic in node.graphics:
                if graphic.path == element.path and graphic.frames != int(element.frames):
                    key = ("frames", placement.panel, placement.node, element.path)
                    edits[key] = FramesEdit(
                        placement.panel, placement.node, element.path,
                        int(element.frames),
                    )
    return list(edits.values()), notes
