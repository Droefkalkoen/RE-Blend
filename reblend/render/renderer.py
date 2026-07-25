"""Per-element batch render: isolate, configure, render, stitch, verify (§5.1).

For each element: only its collection renders (every other element collection
is hidden), a temporary orthographic camera is derived from the registration
empty, frames ``0…re_frames − 1`` render to a scratch directory, the stitcher
builds the vertical strip, and the written file is *verified* — straight
alpha, frame overflow, and dimensions — rather than trusted (risk §10.1).

All scene state the renderer touches is pushed before and popped after every
element, whatever happens in between, so a failed render never leaves the
user's scene reconfigured.
"""

from __future__ import annotations

import contextlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

from ..model import calibration, kinds, schema
from ..project.png_meta import read_png_meta
from ..project.validation import ERROR, WARNING, Finding
from . import bpy_io, stitcher, validators

__all__ = ["RenderResult", "RenderError", "render_element", "render_elements"]

#: World axis an element camera looks along (§4.4 convention: the panel faces
#: −Y, so the camera sits on the −Y side looking towards +Y).
VIEW_AXIS = Vector((0.0, -1.0, 0.0))


class RenderError(Exception):
    """An element cannot be rendered (bad geometry, missing registration…)."""


@dataclass
class RenderResult:
    element: str
    strip_path: Path | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(f.severity != ERROR for f in self.findings)


#: How the elements that are *not* being rendered behave during a render
#: (§5.1 isolation). ``SHADOW`` keeps them in the render as shadow-only casters
#: (invisible to the camera, still shadowing the active element, catching
#: nothing) via Cycles object ray visibility; ``HIDDEN`` excludes them from the
#: render entirely and is engine-agnostic.
INACTIVE_SHADOW = "SHADOW"
INACTIVE_HIDDEN = "HIDDEN"

#: The only engine whose object ray visibility makes shadow-only isolation work.
CYCLES_ENGINE = "CYCLES"


def _skip_reason(collection) -> str | None:
    """Why this element must not be rendered, or None if it should be.

    Two groups the SDK takes out of the artist's hands. For sockets, the
    device-name tape, the back-panel placeholder, CV trim knobs and the
    browse groups the scripting specification says "you cannot change the
    appearance of this widget" and the design guidelines make using the
    Reason Studios-supplied image a requirement — so RE-Blend installs the
    stock file instead (RE-Blend > Install SDK Parts). For value displays,
    popup buttons, patch names and sample drop zones Reason uses the graphics
    definition only as a rectangle to lay text into or to hit-test against,
    and never draws the authored pixels at all.
    """
    try:
        data = schema.props_to_data(collection)
    except (KeyError, ValueError):
        return None
    if kinds.renders_art(data.kind):
        return None
    if kinds.is_sdk_supplied(data.kind):
        return (
            f"'{data.path}' is an SDK-supplied part whose appearance Reason fixes — "
            "not rendered; install the stock image with RE-Blend > Install SDK Parts"
        )
    return (
        f"'{data.path}' is a bounds-only widget: Reason draws its contents itself "
        "and uses the graphics only as a rectangle — not rendered"
    )


def _warn_shadow_engine(result, scene, collection, data, inactive_render) -> None:
    """Warn if shadow-only isolation was asked for under a non-Cycles engine.

    ``SHADOW`` mode makes the other elements invisible-to-camera via Cycles
    object ray visibility (§5.1); under EEVEE/Workbench those flags are ignored,
    so the siblings would render *visible* and pollute the sheet. Only warn when
    the choice actually bites — i.e. there is at least one other element to
    isolate — so a single-element scene stays quiet.
    """
    if inactive_render != INACTIVE_SHADOW or scene.render.engine == CYCLES_ENGINE:
        return
    others = [
        c
        for c in _element_collections(scene)
        if c is not collection and not _is_context_of(c, collection)
    ]
    if not others:
        return
    result.findings.append(
        Finding(
            WARNING,
            "engine",
            f"'Cast Shadows' isolation needs Cycles, but the render engine is "
            f"'{scene.render.engine}' — the other elements will render visible "
            "instead of shadow-only. Switch to Cycles, or use 'Hidden' isolation.",
            subject=data.path,
        )
    )


def render_elements(
    scene: "bpy.types.Scene",
    collections: list["bpy.types.Collection"],
    out_dir: Path | str,
    ppb: float = calibration.DEFAULT_PPB,
    inactive_render: str = INACTIVE_SHADOW,
    view_axis: "Vector | tuple | None" = None,
) -> list[RenderResult]:
    """Render several elements' sheets; one element's failure stops nobody else.

    Elements whose art RE-Blend must not author are skipped rather than
    rendered (:func:`reblend.model.kinds.renders_art`): SDK-supplied parts
    (sockets, the name tape, the placeholder, browse groups) whose appearance
    Reason fixes, and bounds-only widgets whose graphics Reason uses purely
    as a text box or hit area. Each skip is reported so it is visible in the
    panel rather than looking like a silently missing sheet.
    """
    results = []
    for collection in collections:
        skip = _skip_reason(collection)
        if skip is not None:
            results.append(
                RenderResult(
                    element=collection.name,
                    findings=[
                        Finding(WARNING, "not-rendered", skip, subject=collection.name)
                    ],
                )
            )
            continue
        try:
            results.append(
                render_element(
                    scene, collection, out_dir, ppb=ppb,
                    inactive_render=inactive_render, view_axis=view_axis,
                )
            )
        except RenderError as exc:
            results.append(
                RenderResult(
                    element=collection.name,
                    findings=[Finding(ERROR, "render", str(exc), subject=collection.name)],
                )
            )
    return results


def render_element(
    scene: "bpy.types.Scene",
    collection: "bpy.types.Collection",
    out_dir: Path | str,
    ppb: float = calibration.DEFAULT_PPB,
    inactive_render: str = INACTIVE_SHADOW,
    view_axis: "Vector | tuple | None" = None,
) -> RenderResult:
    """Render one element collection to ``<out_dir>/<re_path>.png``."""
    data = schema.props_to_data(collection)
    if not data.path:
        raise RenderError(f"collection '{collection.name}' has no re_path")
    if not data.has_frame_size:
        raise RenderError(f"'{data.path}': set re_frame_w / re_frame_h before rendering")
    geometry_errors = validators.check_frame_bounds(data.frame_w, data.frame_h, data.frames)
    if geometry_errors:
        # Refuse up front: RE2DRender would reframe the sheet (M0 finding 6).
        raise RenderError(f"'{data.path}': " + "; ".join(geometry_errors))

    registration = _find_registration(collection)
    result = RenderResult(element=data.path)
    _warn_shadow_engine(result, scene, collection, data, inactive_render)

    axis = Vector(view_axis) if view_axis is not None else VIEW_AXIS
    with _element_scene_state(scene, collection, inactive_render):
        camera = _make_camera(scene, data, registration, ppb, axis)
        try:
            _configure_render(scene, data)
            with tempfile.TemporaryDirectory(prefix="reblend_") as scratch:
                frame_paths = _render_frames(scene, data, Path(scratch))
                frames = [bpy_io.load_raw_pixels(p) for p in frame_paths]
        finally:
            _remove_camera(camera)

    _validate_frames(result, frames)
    strip = stitcher.stitch(frames)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    strip_path = out_dir / f"{data.path}.png"
    bpy_io.save_strip(strip, strip_path, name=f"reblend_{data.path}")
    result.strip_path = strip_path
    _verify_written(result, strip_path, data)
    return result


# ---------------------------------------------------------------------------
# scene state push/pop
# ---------------------------------------------------------------------------

_RENDER_ATTRS = (
    "resolution_x",
    "resolution_y",
    "resolution_percentage",
    "film_transparent",
    "filepath",
)
_IMAGE_ATTRS = ("file_format", "color_mode", "color_depth")
_VIEW_ATTRS = ("view_transform", "look", "exposure", "gamma")

#: Object ray-visibility flags a shadow-only caster must *drop* (§5.1): it
#: contributes nothing to the camera image or to indirect light, so it neither
#: shows up nor catches shadows — only ``visible_shadow`` is kept on.
_SHADOW_ONLY_OFF = (
    "visible_camera",
    "visible_diffuse",
    "visible_glossy",
    "visible_transmission",
    "visible_volume_scatter",
)
_RAY_ATTRS = _SHADOW_ONLY_OFF + ("visible_shadow",)


@contextlib.contextmanager
def _element_scene_state(scene, active_collection, inactive_render="SHADOW"):
    """Push everything the render touches; pop it however rendering ends.

    The other RE Element collections are taken out of the visible image so
    only the active element renders. ``inactive_render`` chooses how:

    - ``"SHADOW"`` (default): they stay in the render but every object becomes
      a shadow-only caster — invisible to the camera, still casting shadows on
      the active element, catching none itself.
    - ``"HIDDEN"``: they are excluded from the render outright.
    """
    saved_render = {a: getattr(scene.render, a) for a in _RENDER_ATTRS}
    saved_image = {a: getattr(scene.render.image_settings, a) for a in _IMAGE_ATTRS}
    saved_view = {a: getattr(scene.view_settings, a) for a in _VIEW_ATTRS}
    saved_display = scene.display_settings.display_device
    saved_camera = scene.camera
    saved_frame = scene.frame_current

    siblings = _element_collections(scene)
    saved_hide = {c.name: c.hide_render for c in siblings}
    saved_vis: dict[str, dict[str, bool]] = {}
    try:
        for c in siblings:
            visible = c is active_collection or _is_context_of(c, active_collection)
            if visible:
                c.hide_render = False
            elif inactive_render == "HIDDEN":
                c.hide_render = True
            else:  # SHADOW: keep the geometry in the render, shadow-only.
                c.hide_render = False
                for obj in c.all_objects:
                    if obj.name in saved_vis:
                        continue
                    saved_vis[obj.name] = {a: getattr(obj, a) for a in _RAY_ATTRS}
                    for attr in _SHADOW_ONLY_OFF:
                        setattr(obj, attr, False)
                    obj.visible_shadow = True
        yield
    finally:
        for c in siblings:
            if c.name in saved_hide:
                c.hide_render = saved_hide[c.name]
        for name, attrs in saved_vis.items():
            obj = bpy.data.objects.get(name)
            if obj is not None:
                for attr, value in attrs.items():
                    setattr(obj, attr, value)
        for attr, value in saved_render.items():
            setattr(scene.render, attr, value)
        for attr, value in saved_image.items():
            setattr(scene.render.image_settings, attr, value)
        for attr, value in saved_view.items():
            setattr(scene.view_settings, attr, value)
        scene.display_settings.display_device = saved_display
        scene.camera = saved_camera
        scene.frame_set(saved_frame)


def _element_collections(scene) -> list["bpy.types.Collection"]:
    """The isolation set (§5.1): every RE Element collection in the scene,
    plus their per-element context collections ('<element> context')."""
    found = []

    def walk(collection):
        for child in collection.children:
            if schema.is_element(child) or child.name.endswith(" context"):
                found.append(child)
            walk(child)

    walk(scene.collection)
    return found


def _is_context_of(collection, element) -> bool:
    """A per-element optional context collection (light catchers, §5.1),
    named '<element> context', stays visible for its element only."""
    return collection.name == f"{element.name} context"


# ---------------------------------------------------------------------------
# camera / render configuration (M0-proven values)
# ---------------------------------------------------------------------------


def _find_registration(collection) -> "bpy.types.Object":
    name = str(collection.get("re_registration", ""))
    if name and name in bpy.data.objects:
        return bpy.data.objects[name]
    for obj in collection.all_objects:
        if obj.type == "EMPTY" and obj.name.startswith("reg_"):
            return obj
    raise RenderError(
        f"collection '{collection.name}' has no registration empty "
        "(re_registration property or an EMPTY named 'reg_…')"
    )


def _make_camera(scene, data: schema.ElementData, registration, ppb: float,
                 view_axis: Vector = VIEW_AXIS):
    cam_data = bpy.data.cameras.new(f"reblend_cam_{data.path}")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = calibration.ortho_scale(data.frame_w, data.frame_h, ppb)
    camera = bpy.data.objects.new(cam_data.name, cam_data)
    scene.collection.objects.link(camera)

    # Fixed per element, never moves between frames: registration is true by
    # construction (§4.2). The camera sits on the view axis through the empty.
    axis = (registration.matrix_world.to_quaternion() @ view_axis).normalized()
    camera.location = registration.matrix_world.translation + axis * calibration.CAMERA_DISTANCE
    camera.rotation_euler = axis.to_track_quat("Z", "Y").to_euler()
    scene.camera = camera
    return camera


def _remove_camera(camera) -> None:
    cam_data = camera.data
    bpy.data.objects.remove(camera)
    bpy.data.cameras.remove(cam_data)


def _configure_render(scene, data: schema.ElementData) -> None:
    """The correctness-critical block (§5.2, risk §10.1) — every line defends
    one M0 failure mode; change nothing without a reason you can name."""
    render = scene.render
    render.resolution_x = data.frame_w
    render.resolution_y = data.frame_h
    render.resolution_percentage = 100
    render.film_transparent = True
    render.image_settings.file_format = "PNG"
    render.image_settings.color_mode = "RGBA"
    render.image_settings.color_depth = "8"

    scene.view_settings.view_transform = "Standard"  # NOT Filmic / AgX
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.display_settings.display_device = "sRGB"


def _render_frames(scene, data: schema.ElementData, scratch: Path) -> list[Path]:
    paths = []
    for frame in range(data.frames):
        scene.frame_set(frame)
        path = scratch / f"{data.path}_{frame:04d}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# post-render verification (§5.2: never trust settings, check the pixels)
# ---------------------------------------------------------------------------


def _validate_frames(result: RenderResult, frames: list[np.ndarray]) -> None:
    for index in validators.overflow_frames(frames):
        result.findings.append(
            Finding(
                WARNING,
                "overflow",
                f"frame {index}: alpha touches the frame border — geometry, shadow, "
                "or glow bleeds outside the declared bounding box",
                subject=result.element,
            )
        )


def _verify_written(result: RenderResult, path: Path, data: schema.ElementData) -> None:
    meta = read_png_meta(path)
    want = (data.frame_w, data.frame_h * data.frames)
    if (meta.width, meta.height) != want:
        result.findings.append(
            Finding(
                ERROR,
                "png-dims",
                f"written sheet is {meta.width}x{meta.height}, expected {want[0]}x{want[1]}",
                subject=data.path,
            )
        )
    if not meta.is_8bit_rgba:
        result.findings.append(
            Finding(
                ERROR,
                "png-format",
                f"written sheet is not 8-bit RGBA (bit depth {meta.bit_depth}, "
                f"colour type {meta.color_type})",
                subject=data.path,
            )
        )

    verdict = validators.classify_alpha(bpy_io.load_raw_pixels(path))
    if verdict == validators.ALPHA_PREMULTIPLIED:
        result.findings.append(
            Finding(
                ERROR,
                "alpha",
                "written sheet looks premultiplied — straight (unassociated) alpha "
                "is required; see risk §10.1",
                subject=data.path,
            )
        )
