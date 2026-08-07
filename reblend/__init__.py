"""RE-Blend — Blender extension for Rack Extension GUI sprite-sheet production.

Makes the Blender scene the single source of truth for a Rack Extension's 2D
GUI art: control states bound to timeline frames, per-element cameras derived
from registration empties, verified straight-alpha sprite-sheet export, and
two-way sync with the RE project's ``GUI2D/*.lua`` configuration.

This package must stay importable *outside* Blender: the ``project``,
``model`` (minus ``rigs``), and the numpy half of the ``render`` layer are
pure Python and are exercised by the test suite without ``bpy``. Anything
that needs Blender imports ``bpy`` lazily inside the modules that use it —
including everything below, which is why the imports live inside the
functions.
"""

__version__ = "0.1.0"


def register() -> None:
    """Blender extension entry point: UI classes plus the schema-migration
    load handler (§8 — ``.blend`` files outlive add-on versions)."""
    import bpy

    from . import ui

    ui.register()
    if _migrate_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_migrate_on_load)
    # Migrate the file already open when the add-on enables. bpy.data is
    # restricted during register() (a _RestrictData proxy), so this cannot run
    # inline — defer it to a one-shot timer that fires once the restriction
    # lifts. Returning None from the callback unregisters the timer.
    bpy.app.timers.register(_migrate_open_file, first_interval=0.0)


def unregister() -> None:
    """Blender extension exit point (mirror of :func:`register`)."""
    import bpy

    from . import ui

    if _migrate_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_migrate_on_load)
    ui.unregister()


def _migrate_all() -> None:
    import bpy

    from .model import schema
    from .ui import operators

    # bpy.data, not a scene walk: migrations must reach every element in the
    # file, including collections not linked under any scene.
    for collection in bpy.data.collections:
        if schema.is_element(collection):
            try:
                schema.migrate(collection)
            except ValueError as exc:
                print(f"[RE-Blend] {collection.name}: {exc}")
                continue
            # UI metadata is not part of the schema, so re-derive it on every
            # load: the preview-frame slider clamps to the element's frames.
            operators._configure_preview_ui(collection)


def _migrate_open_file():
    """One-shot timer callback: migrate the file open at enable time, once the
    register()-time ``bpy.data`` restriction has lifted. Returns ``None`` so the
    timer does not repeat."""
    _migrate_all()
    return None


def _migrate_on_load(_filepath=None, _none=None) -> None:
    _migrate_all()


try:  # persistent survives file loads; only decoratable inside Blender
    import bpy.app.handlers

    _migrate_on_load = bpy.app.handlers.persistent(_migrate_on_load)
except ModuleNotFoundError:
    pass


def cli() -> None:
    """Headless entry point (design §7).

    Invoked as::

        blender -b MyDevice.blend --python-expr "import reblend; reblend.cli()" -- \\
            render --all --project /path/to/mydevice --strict

    Implemented in M3; defined now so the invocation contract is stable.
    """
    raise NotImplementedError("headless CLI arrives in milestone M3")
