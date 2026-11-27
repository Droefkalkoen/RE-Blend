"""UI layer: the N-panel "RE-Blend" tab, operators, and scene settings (§8).

Everything an operator does is UI-stateless (§7): the panels only collect
inputs and display results, so the same operators drive headless use in M3.
Only importable inside Blender.
"""

from __future__ import annotations

import bpy

from . import operators, panels, props

_MODULES = (props, operators, panels)


def register() -> None:
    for module in _MODULES:
        for cls in module.CLASSES:
            bpy.utils.register_class(cls)
    props.attach()
    if operators.live_overlap_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(operators.live_overlap_handler)


def unregister() -> None:
    if operators.live_overlap_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(operators.live_overlap_handler)
    props.detach()
    for module in reversed(_MODULES):
        for cls in reversed(module.CLASSES):
            bpy.utils.unregister_class(cls)
