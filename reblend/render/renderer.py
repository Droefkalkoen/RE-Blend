"""Per-element batch render: isolate, configure, render, stitch, verify (§5.1).

For each element: only its collection renders (what the other element
collections do meanwhile is :mod:`reblend.render.shadows`' call — hidden,
shadow-only caster, or shadow catcher, depending on the isolation setting and
on who owns which shadow), a temporary orthographic camera is derived from the
registration empty, frames ``0…re_frames − 1`` render to a scratch directory,
the stitcher builds the vertical strip, and the written file is *verified* —
straight alpha, frame overflow, and dimensions — rather than trusted
(risk §10.1).

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
from . import bpy_io, shadows, stitcher, validators

__all__ = ["RenderResult", "RenderError", "RenderOptions", "render_element",
           "render_elements", "reload_changed_images"]

#: World axis an element camera looks along (§4.4 convention: the panel faces
#: −Y, so the camera sits on the −Y side looking towards +Y).
VIEW_AXIS = Vector((0.0, -1.0, 0.0))


class RenderError(Exception):
    """An element cannot be rendered (bad geometry, missing registration…)."""


@dataclass
class RenderOptions:
    """Render settings beyond the isolation mode (Skou feedback #12-#14, #19, #38)."""

    #: Shadow-only casters keep lighting their neighbours: only camera
    #: visibility is dropped, so an isolated part still reflects the panel and
    #: gets bounce light instead of seeing the bare HDRI (#12).
    caster_bounce: bool = False
    #: Elements on other panels sit the render out entirely: all panels share
    #: one world origin, so back-panel geometry would otherwise shadow or
    #: reflect into front renders (#13).
    panel_isolation: bool = False
    #: Coverage below this becomes 0 and the rest is stretched to 0..1, to
    #: clear a shadow catcher's ambient haze (#19). 0 = off.
    alpha_floor: float = 0.0
    #: source sprite path -> [(derived path, (x, y, w, h))] (#38).
    derived: dict = field(default_factory=dict)


@dataclass
class RenderResult:
    element: str
    strip_path: Path | None = None
    findings: list[Finding] = field(default_factory=list)
    #: Derived sheets written from this element's frames.
    derived_paths: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(f.severity != ERROR for f in self.findings)


#: How the elements that are *not* being rendered behave during a render
#: (§5.1 isolation). ``SHADOW`` keeps them in the render as shadow-only casters
#: (invisible to the camera, still shadowing the active element, catching
#: nothing) via Cycles object ray visibility; ``HIDDEN`` excludes them from the
#: render entirely and is engine-agnostic. Per-element shadow *ownership*
#: overrides both where the two disagree — :mod:`reblend.render.shadows` is
#: the authority on the resulting roles.
INACTIVE_SHADOW = shadows.INACTIVE_SHADOW
INACTIVE_HIDDEN = shadows.INACTIVE_HIDDEN

#: The only engine whose object flags make shadow-only casters and shadow
#: catchers work.
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


def _warn_plan(result, scene, plan, data) -> None:
    """Warn about a shadow plan the scene cannot actually deliver (§5.1).

    Two ways it can fail quietly. The caster and catcher roles are Cycles
    object flags: under EEVEE/Workbench they are ignored, so those collections
    render *visible* and pollute the sheet. And an element that owns its shadow
    needs a plate to catch it — with no backdrop element in the scene the
    shadow simply is not in the sheet, which looks like nothing happened.
    """
    roles = {role for _c, role in plan}
    needed = roles & shadows.NEEDS_CYCLES
    if needed and scene.render.engine != CYCLES_ENGINE:
        wanted = (
            "shadow catching" if shadows.ROLE_CATCHER in needed
            else "'Cast Shadows' isolation"
        )
        result.findings.append(
            Finding(
                WARNING,
                "engine",
                f"{wanted} needs Cycles, but the render engine is "
                f"'{scene.render.engine}' — the other elements will render "
                "visible instead. Switch to Cycles, or use 'Hidden' isolation.",
                subject=data.path,
            )
        )
    if (data.shadow_owner == kinds.SHADOW_ELEMENT
            and shadows.ROLE_CATCHER not in roles):
        result.findings.append(
            Finding(
                WARNING,
                "shadow-owner",
                "this element owns its cast shadow, but the scene has no "
                "backdrop element for the shadow to fall on — the sheet will "
                "have no shadow in it. Import the panel backdrop, or hand the "
                "shadow back to the background",
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
    options: RenderOptions | None = None,
    progress=None,
    cancelled=None,
) -> list[RenderResult]:
    """Render several elements' sheets; one element's failure stops nobody else.

    Elements whose art RE-Blend must not author are skipped rather than
    rendered (:func:`reblend.model.kinds.renders_art`): SDK-supplied parts
    (sockets, the name tape, the placeholder, browse groups) whose appearance
    Reason fixes, and bounds-only widgets whose graphics Reason uses purely
    as a text box or hit area. Each skip is reported so it is visible in the
    panel rather than looking like a silently missing sheet.

    ``progress(done, total, name)`` is called before each element and once at
    the end; ``cancelled()`` returning True stops before the next element
    (the background render's Cancel, Skou feedback #14/#39).
    """
    results = []
    reload_changed_images()
    total = len(collections)
    for index, collection in enumerate(collections):
        if cancelled is not None and cancelled():
            break
        if progress is not None:
            progress(index, total, collection.name)
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
                    options=options,
                )
            )
        except RenderError as exc:
            results.append(
                RenderResult(
                    element=collection.name,
                    findings=[Finding(ERROR, "render", str(exc), subject=collection.name)],
                )
            )
    if progress is not None:
        progress(len(results), total, "")
    return results


def render_element(
    scene: "bpy.types.Scene",
    collection: "bpy.types.Collection",
    out_dir: Path | str,
    ppb: float = calibration.DEFAULT_PPB,
    inactive_render: str = INACTIVE_SHADOW,
    view_axis: "Vector | tuple | None" = None,
    options: RenderOptions | None = None,
) -> RenderResult:
    """Render one element collection to ``<out_dir>/<re_path>.png``."""
    options = options or RenderOptions()
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
    plan = _isolation_plan(scene, collection, data, inactive_render,
                           panel_isolation=options.panel_isolation)
    _warn_plan(result, scene, plan, data)

    axis = Vector(view_axis) if view_axis is not None else VIEW_AXIS
    with _element_scene_state(scene, plan, caster_bounce=options.caster_bounce):
        camera = _make_camera(scene, data, registration, ppb, axis)
        try:
            _configure_render(scene, data, samples=int(collection.get("re_samples", 0)))
            with tempfile.TemporaryDirectory(prefix="reblend_") as scratch:
                frame_paths = _render_frames(scene, data, Path(scratch))
                frames = [bpy_io.load_raw_pixels(p) for p in frame_paths]
        finally:
            _remove_camera(camera)

    if options.alpha_floor > 0.0:
        frames = [stitcher.alpha_floor(f, options.alpha_floor) for f in frames]
    _validate_frames(result, frames, data)
    strip = stitcher.stitch(frames)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    strip_path = out_dir / f"{data.path}.png"
    bpy_io.save_strip(strip, strip_path, name=f"reblend_{data.path}")
    result.strip_path = strip_path
    _verify_written(result, strip_path, data)

    for derived_path, rect in options.derived.get(data.path, ()):
        try:
            cut = stitcher.crop_frames(frames, rect)
        except stitcher.StitchError as exc:
            result.findings.append(Finding(ERROR, "derived", str(exc), subject=derived_path))
            continue
        path = out_dir / f"{derived_path}.png"
        bpy_io.save_strip(stitcher.stitch(cut), path, name=f"reblend_{derived_path}")
        result.derived_paths.append(path)
        derived_data = schema.ElementData(
            node=derived_path, path=derived_path, kind=data.kind, frames=data.frames,
            frame_w=int(rect[2]), frame_h=int(rect[3]))
        _verify_written(result, path, derived_data)
    return result


def reload_changed_images() -> list[str]:
    """Reload file-backed images whose file changed since Blender read it
    (Skou feedback #32: a silkscreen edited in Photoshop rendered stale).

    The file's mtime is remembered on the image (``re_mtime``); an image
    seen for the first time is reloaded once, which is cheap and certain.
    Returns the names of the images reloaded.
    """
    import os

    reloaded = []
    for image in bpy.data.images:
        if image.source not in {"FILE", "SEQUENCE", "TILED"} or image.packed_file:
            continue
        path = bpy.path.abspath(image.filepath)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if float(image.get("re_mtime", -1.0)) != mtime:
            image.reload()
            image["re_mtime"] = mtime
            reloaded.append(image.name)
    return reloaded


# ---------------------------------------------------------------------------
# scene state push/pop
# ---------------------------------------------------------------------------

#: Cycles sample count, overridden per element by ``re_samples`` (#14).
_CYCLES_ATTRS = ("samples",)
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
#: The "shadows + bounce" caster (Skou feedback #12): hidden from the camera
#: only, so neighbours still reflect into and bounce light onto the element.
_BOUNCE_CASTER_OFF = ("visible_camera",)
#: Everything an object-level role touches, so the restore covers all of it.
_OBJ_ATTRS = _SHADOW_ONLY_OFF + ("visible_shadow", "is_shadow_catcher")


def _isolation_plan(scene, active_collection, data, inactive_render,
                    panel_isolation: bool = False):
    """``[(collection, role)]`` for every collection in the isolation set.

    The active element and its context collection render normally; what each
    other collection does is :func:`reblend.render.shadows.sibling_role`,
    which weighs the scene-wide isolation setting against the per-element
    shadow owners on both sides.
    """
    active_owner = data.shadow_owner
    active_panels = set(data.panels)
    plan = []
    for c in _element_collections(scene):
        if c is active_collection or _is_context_of(c, active_collection):
            plan.append((c, shadows.ROLE_VISIBLE))
            continue
        if panel_isolation and active_panels and not (_panels_of(c) & active_panels):
            plan.append((c, shadows.ROLE_HIDDEN))
            continue
        kind, owner = _shadow_facts(c)
        plan.append(
            (c, shadows.sibling_role(active_owner, kind, owner, inactive_render))
        )
    return plan


def _panels_of(collection) -> set[str]:
    """The panels an element (or an element's context collection) is on."""
    if not schema.is_element(collection) and collection.name.endswith(" context"):
        owner = bpy.data.collections.get(collection.name[: -len(" context")])
        if owner is None:
            return set()
        collection = owner
    try:
        return set(schema.props_to_data(collection).panels)
    except (KeyError, ValueError):
        return set()


def _shadow_facts(collection) -> tuple[str, str]:
    """One sibling's ``(kind, shadow owner)``, defaulted for a non-element.

    Another element's context collection carries no ``re_*`` properties; it is
    ordinary neighbouring geometry, so it takes the plain background-owner
    treatment rather than being mistaken for a plate.
    """
    if not schema.is_element(collection):
        return kinds.STATIC, kinds.SHADOW_BACKGROUND
    try:
        data = schema.props_to_data(collection)
    except (KeyError, ValueError):
        return kinds.STATIC, kinds.SHADOW_BACKGROUND
    return data.kind, data.shadow_owner


@contextlib.contextmanager
def _element_scene_state(scene, plan, caster_bounce: bool = False):
    """Push everything the render touches; pop it however rendering ends.

    ``plan`` is the ``(collection, role)`` list from :func:`_isolation_plan`;
    this applies each role to real collections and objects and undoes it
    afterwards, so a failed render never leaves the scene reconfigured.
    """
    saved_render = {a: getattr(scene.render, a) for a in _RENDER_ATTRS}
    saved_image = {a: getattr(scene.render.image_settings, a) for a in _IMAGE_ATTRS}
    saved_view = {a: getattr(scene.view_settings, a) for a in _VIEW_ATTRS}
    saved_display = scene.display_settings.display_device
    cycles = getattr(scene, "cycles", None)
    saved_cycles = {a: getattr(cycles, a) for a in _CYCLES_ATTRS if hasattr(cycles, a)}
    saved_camera = scene.camera
    saved_frame = scene.frame_current

    saved_hide = {c.name: c.hide_render for c, _role in plan}
    saved_vis: dict[str, dict[str, bool]] = {}
    try:
        for collection, role in plan:
            collection.hide_render = role == shadows.ROLE_HIDDEN
            if role not in (shadows.ROLE_CASTER, shadows.ROLE_CATCHER):
                continue
            for obj in collection.all_objects:
                if obj.name in saved_vis:
                    continue
                saved_vis[obj.name] = {a: getattr(obj, a) for a in _OBJ_ATTRS}
                if role == shadows.ROLE_CASTER:
                    for attr in (_BOUNCE_CASTER_OFF if caster_bounce else _SHADOW_ONLY_OFF):
                        setattr(obj, attr, False)
                    obj.visible_shadow = True
                else:  # CATCHER: transparent except where the shadow lands.
                    obj.visible_camera = True
                    obj.is_shadow_catcher = True
        yield
    finally:
        for collection, _role in plan:
            if collection.name in saved_hide:
                collection.hide_render = saved_hide[collection.name]
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
        for attr, value in saved_cycles.items():
            setattr(cycles, attr, value)
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


def _configure_render(scene, data: schema.ElementData, samples: int = 0) -> None:
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
    cycles = getattr(scene, "cycles", None)
    if samples > 0 and cycles is not None and hasattr(cycles, "samples"):
        cycles.samples = samples  # per-element override; restored afterwards


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


#: Kinds whose art is meant to fill its frame edge to edge (a panel backdrop,
#: a display window), so alpha at the border is the design, not a bleed
#: (Skou feedback #10).
FULL_BLEED_KINDS = frozenset({kinds.BACKDROP, kinds.DISPLAY})


def _validate_frames(result: RenderResult, frames: list[np.ndarray],
                     data: "schema.ElementData | None" = None) -> None:
    if data is not None and (data.kind in FULL_BLEED_KINDS or data.full_bleed):
        return
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
