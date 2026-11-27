"""Best-effort, read-only extraction from ``motherboard_def.lua`` (§4.1).

RE-Blend never writes this file — properties are the developer's contract —
and only needs one fact from it: the ``steps`` count of stepped properties,
to flag a control whose sheet frame count disagrees with the property it
binds (validation table §6.3, e.g. a ``sequence_fader`` on an 8-step property
whose sheet declares ``frames = 3``).

The file is executed in the same sandbox as the GUI2D files; the recorded
``jbox.*`` tables are then walked for anything that looks like a property
definition carrying ``steps``. Anything the walk does not understand is
ignored — this reader must degrade to "no information" rather than fail on
motherboards using SDK features RE-Blend has no model for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .lua_reader import LuaConfigError, _execute_sandboxed, _execute_sandboxed_text

__all__ = ["BUILTIN_STEPS", "read_motherboard_steps", "read_motherboard_steps_text"]

#: Steps of built-in properties devices bind without declaring (SDK-defined).
#: The On/Off/Bypass sequence fader is the canonical case (design §4.3).
BUILTIN_STEPS = {"/custom_properties/builtin_onoffbypass": 3}


def read_motherboard_steps(path: Path | str) -> dict[str, int]:
    """Map bound property paths (``/custom_properties/<name>``) to ``steps``.

    Only custom properties that declare an integer ``steps`` appear; built-ins
    from :data:`BUILTIN_STEPS` are merged in so callers get one lookup table.
    Raises :class:`LuaConfigError` when the file is missing or fails to run.
    """
    path = Path(path)
    return _steps_from_globals(_execute_sandboxed(path))


def read_motherboard_steps_text(source: str, name: str = "motherboard_def.lua") -> dict[str, int]:
    """:func:`read_motherboard_steps` on source text (``name`` labels errors)."""
    return _steps_from_globals(_execute_sandboxed_text(source, Path(name)))


def _steps_from_globals(globals_: dict[str, Any]) -> dict[str, int]:
    steps = dict(BUILTIN_STEPS)
    custom = globals_.get("custom_properties")
    if isinstance(custom, dict):
        _collect_steps(custom, steps)
    return steps


def _collect_steps(table: dict[str, Any], out: dict[str, int]) -> None:
    """Recursively find ``properties = { name = { ..., steps = N } }`` tables.

    The motherboard groups properties by owner (``document_owner``,
    ``rt_owner``, ``gui_owner`` …), each with a ``properties`` sub-table; the
    property *name* there is what widgets reference as
    ``/custom_properties/<name>``.
    """
    for key, value in table.items():
        if not isinstance(value, dict):
            continue
        if key == "properties":
            for name, prop in value.items():
                if not (isinstance(name, str) and isinstance(prop, dict)):
                    continue
                declared = prop.get("steps")
                if isinstance(declared, (int, float)) and int(declared) > 0:
                    out[f"/custom_properties/{name}"] = int(declared)
        else:
            _collect_steps(value, out)
