"""Edit operations: the one way the canvas, the CLI and agents change a layout.

An operation is a small JSON object. A batch applies whole or not at all:
every op is checked, ``device_2D.lua`` is patched in memory through RE-Blend's
patch mode (which refuses rather than guesses, and verifies by re-parsing),
the sidecar is updated in memory, and the rules run on the result. Only then,
and only when ``dry_run`` is false, are the files written.

The result says which findings the batch added and which it resolved, so an
agent sees the consequence of each change.

Operations::

    {"op": "move",  "panel": "front", "node": "knob_tone", "x": 980, "y": 210}   # absolute panel px
    {"op": "nudge", "panel": "front", "node": "knob_tone", "dx": 5, "dy": 0}
    {"op": "set_frames", "panel": "front", "node": "knob_tone", "frames": 61}
    {"op": "set_frame_size", "sprite": "knob_tone", "w": 120, "h": 120}
    {"op": "set_shape", "sprite": "knob_tone", "shape": {"type": "knob", "diameter_px": 100}}
    {"op": "label", "id": "lbl-tone", "panel": "front", "text": "TONE",
     "x": 990, "y": 180, "size_px": 48, "functional": true, "node": "knob_tone"}
    {"op": "remove_label", "id": "lbl-tone"}
    {"op": "add_control", "panel": "front", "node": "knob_drive", "widget": "analog_knob",
     "x": 900, "y": 110, "property": "drive", "title": "Drive"}   # also: steps, frames, w, h

``add_control`` writes the node, the widget, and (for a new property) the
property stub and its English name: see :mod:`rebench.stubs`.
"""

from __future__ import annotations

import copy
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from reblend.project.lua_writer import (
    FramesEdit, OffsetEdit, PatchError, node_base_offset, patch_device_2d,
)

from .check import BenchFinding, CheckResult, check_project
from .profile import Profile, load_profile
from .project import DEVICE_2D, BenchProject
from .sidecar import Label, SpriteInfo
from .stubs import WIDGETS, ControlSpec, StubError, add_control_sources

__all__ = ["OpError", "ApplyResult", "apply_ops"]


class OpError(ValueError):
    """The batch cannot be applied; nothing was written."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass
class ApplyResult:
    written: bool
    applied: list[str]
    check: CheckResult
    added: list[BenchFinding] = field(default_factory=list)
    resolved: list[BenchFinding] = field(default_factory=list)
    #: The project as it is after the ops (in memory for a dry run).
    project: BenchProject | None = None

    def to_dict(self) -> dict:
        return {
            "written": self.written,
            "applied": self.applied,
            "errors": len(self.check.errors),
            "warnings": len(self.check.warnings),
            "added": [f.to_dict() for f in self.added],
            "resolved": [f.to_dict() for f in self.resolved],
            "findings": [f.to_dict() for f in self.check.findings],
        }


def _key(f: BenchFinding) -> tuple:
    return (f.code, f.panel, f.subject, f.message)


def _node_position(project: BenchProject, panel: str, node: str) -> tuple[float, float] | None:
    for spec in project.link.specs:
        for p in spec.placements:
            if p.panel == panel and p.node == node:
                return p.x, p.y
    n = project.link.device.node(panel, node)
    if n is None:
        return None
    base = node_base_offset(project.link.device, panel, node) or (0.0, 0.0)
    off = n.offset or (0.0, 0.0)
    return base[0] + off[0], base[1] + off[1]


def apply_ops(root: Path | str, ops: list[dict], dry_run: bool = False,
              profile: Profile | None = None,
              project: BenchProject | None = None) -> ApplyResult:
    root = Path(root)
    project = project or BenchProject.load(root)
    profile = profile or load_profile(root)
    before = check_project(project, profile)

    current = project
    sources = dict(project.sources)
    sidecar = copy.deepcopy(project.sidecar)
    applied: list[str] = []
    reasons: list[str] = []

    def patch(edit) -> None:
        nonlocal current
        sources[DEVICE_2D] = patch_device_2d(sources[DEVICE_2D], [edit]).source
        current = current.with_changes(sources=sources, sidecar=sidecar)

    for i, op in enumerate(ops):
        kind = op.get("op")
        device = current.link.device
        try:
            if kind in ("move", "nudge"):
                panel, node = op["panel"], op["node"]
                if device.node(panel, node) is None:
                    reasons.append(f"op {i}: no node '{node}' on the {panel} panel")
                    continue
                if kind == "move":
                    x, y = float(op["x"]), float(op["y"])
                else:
                    pos = _node_position(current, panel, node)
                    x, y = pos[0] + float(op.get("dx", 0)), pos[1] + float(op.get("dy", 0))
                base = node_base_offset(device, panel, node) or (0.0, 0.0)
                patch(OffsetEdit(panel, node, round(x - base[0]), round(y - base[1])))
                applied.append(f"{kind} {panel}/{node} to ({round(x)}, {round(y)})")
            elif kind == "set_frames":
                panel, node, frames = op["panel"], op["node"], int(op["frames"])
                n = device.node(panel, node)
                if n is None or not n.graphics:
                    reasons.append(f"op {i}: no sprite node '{node}' on the {panel} panel")
                    continue
                patch(FramesEdit(panel, node, n.graphics[0].path, frames))
                applied.append(f"set_frames {panel}/{node} = {frames}")
            elif kind == "add_control":
                spec = ControlSpec(
                    panel=op["panel"], node=op["node"], widget=op["widget"],
                    x=int(round(float(op["x"]))), y=int(round(float(op["y"]))),
                    bind=op.get("property", ""), title=op.get("title", ""),
                    steps=int(op.get("steps", 3)), frames=int(op.get("frames", 0)))
                sources.update(add_control_sources(sources, spec, root))
                info = WIDGETS.get(spec.widget)
                w, h = int(op.get("w", 0)), int(op.get("h", 0))
                if not (w and h) and info:
                    w, h = info.size
                if w and h:
                    sprite = sidecar.sprites.setdefault(spec.node, SpriteInfo())
                    sprite.frame_w, sprite.frame_h = w, h
                    if info and not sprite.shape:
                        sprite.shape = {"type": info.shape}
                current = current.with_changes(sources=sources, sidecar=sidecar)
                applied.append(f"add_control {spec.panel}/{spec.node} ({spec.widget} on "
                               f"/custom_properties/{spec.prop})")
            elif kind == "set_frame_size":
                w, h = int(op["w"]), int(op["h"])
                if w <= 0 or h <= 0:
                    reasons.append(f"op {i}: frame size must be positive")
                    continue
                info = sidecar.sprites.setdefault(op["sprite"], SpriteInfo())
                info.frame_w, info.frame_h = w, h
                applied.append(f"set_frame_size {op['sprite']} = {w}x{h}")
            elif kind == "set_shape":
                info = sidecar.sprites.setdefault(op["sprite"], SpriteInfo())
                info.shape = dict(op["shape"])
                applied.append(f"set_shape {op['sprite']}")
            elif kind == "label":
                fields = {k: v for k, v in op.items() if k != "op"}
                existing = sidecar.label(fields["id"])
                if existing is None:
                    sidecar.labels.append(Label(**fields))
                else:
                    for k, v in fields.items():
                        setattr(existing, k, v)
                applied.append(f"label {fields['id']}")
            elif kind == "remove_label":
                before_n = len(sidecar.labels)
                sidecar.labels = [l for l in sidecar.labels if l.id != op["id"]]
                if len(sidecar.labels) == before_n:
                    reasons.append(f"op {i}: no label '{op['id']}'")
                    continue
                applied.append(f"remove_label {op['id']}")
            else:
                reasons.append(f"op {i}: unknown op {kind!r}")
        except (KeyError, TypeError) as exc:
            reasons.append(f"op {i} ({kind}): missing or bad field {exc}")
        except PatchError as exc:
            reasons.extend(f"op {i}: {r}" for r in exc.reasons)
        except StubError as exc:
            reasons.extend(f"op {i}: {r}" for r in exc.reasons)

    if reasons:
        raise OpError(reasons)

    after_project = project.with_changes(sources=sources, sidecar=sidecar)
    after = check_project(after_project, profile)

    written = False
    if not dry_run:
        for rel, text in sources.items():
            if text != project.sources.get(rel):
                _write_atomic(root / rel, text)
                written = True
        if sidecar.to_dict() != project.sidecar.to_dict():
            sidecar.save(root)
            written = True

    old = {_key(f) for f in before.findings}
    new = {_key(f) for f in after.findings}
    return ApplyResult(
        written=written,
        applied=applied,
        check=after,
        added=[f for f in after.findings if _key(f) not in old],
        resolved=[f for f in before.findings if _key(f) not in new],
        project=after_project,
    )


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
