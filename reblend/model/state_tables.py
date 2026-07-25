"""State tables: frame-indexed control states and their compilation (§4.3).

Multi-state controls (buttons, fader handles, selectors, lamps) map each
sprite frame to a named state, and each state to a set of *state actions*:
visibility toggles, material emission values, object transforms, shape keys.
The table compiles to constant-interpolation keyframe instructions so that
scrubbing the timeline previews exactly the discrete sheet, and rendering
frames ``0…N−1`` produces exactly the declared states.

This module is pure: it describes *what* to key, as data. Applying the
compiled keys to a live scene is :mod:`reblend.model.rigs`' job. Tables
serialise to JSON for storage in the element's ``re_states`` property.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from . import kinds

__all__ = [
    "StateAction",
    "State",
    "StateTable",
    "Key",
    "Channel",
    "visibility",
    "emission_strength",
    "emission_color",
    "location",
    "shape_key_value",
    "default_state_table",
    "describe_channel",
    "is_interpolatable",
    "linear_spread",
]

#: A channel identity: ``(id_type, target, data_path, index)`` — what
#: :meth:`StateAction.key` returns, and what must appear in *every* state.
Channel = tuple[str, str, str, int]

#: bpy.data collection names an action may target.
_ID_TYPES = ("objects", "materials")


@dataclass(frozen=True)
class StateAction:
    """One property assignment a state makes.

    ``id_type`` names the ``bpy.data`` collection (``objects`` /
    ``materials``), ``target`` the datablock, ``data_path`` the RNA path
    relative to it. ``index`` addresses one component of a vector property
    (−1 = whole value / scalar). ``value`` is a float or a float tuple.
    """

    id_type: str
    target: str
    data_path: str
    value: Any
    index: int = -1

    def key(self) -> tuple[str, str, str, int]:
        """Identity of the animated channel (what must appear in every state)."""
        return (self.id_type, self.target, self.data_path, self.index)


# -- convenience constructors (the vocabulary of design §4.3) ---------------


def visibility(obj: str, visible: bool) -> tuple[StateAction, ...]:
    """Show/hide an object in both render and viewport (preview = sheet)."""
    hide = not visible
    return (
        StateAction("objects", obj, "hide_render", float(hide)),
        StateAction("objects", obj, "hide_viewport", float(hide)),
    )


def emission_strength(material: str, value: float, node: str = "Emission") -> StateAction:
    """Emission strength on a named node of a material's tree (lamps, glows)."""
    path = f'node_tree.nodes["{node}"].inputs["Strength"].default_value'
    return StateAction("materials", material, path, float(value))


def emission_color(
    material: str, rgba: tuple[float, float, float, float], node: str = "Emission"
) -> StateAction:
    """Emission colour on a named node of a material's tree."""
    path = f'node_tree.nodes["{node}"].inputs["Color"].default_value'
    return StateAction("materials", material, path, tuple(float(c) for c in rgba))


def location(obj: str, axis: int, value: float) -> StateAction:
    """One location component of an object (a fader handle's detent position)."""
    return StateAction("objects", obj, "location", float(value), index=axis)


def shape_key_value(obj: str, key_name: str, value: float) -> StateAction:
    """A shape key's value on a mesh object (pressed caps, flexing parts)."""
    path = f'data.shape_keys.key_blocks["{key_name}"].value'
    return StateAction("objects", obj, path, float(value))


#: Data paths whose value is a flag, not a quantity. Interpolating one is
#: meaningless (there is no half-hidden object), so a linear spread refuses.
_FLAG_PATHS = ("hide_render", "hide_viewport")


def is_interpolatable(channel: Channel) -> bool:
    """Whether a channel holds a quantity a linear spread can fill in."""
    return channel[2] not in _FLAG_PATHS


def linear_spread(count: int, start: Any, end: Any) -> list[Any]:
    """``count`` evenly spaced values from ``start`` to ``end``, inclusive.

    Scalars interpolate directly; sequences (an RGBA colour) interpolate
    component-wise and come back as tuples. The endpoints are copied through
    exactly rather than computed, so a spread always *ends* where the designer
    put it — no float drift on the two values they actually chose.
    """
    if count < 2:
        raise ValueError(f"a spread needs at least 2 states, got {count}")
    if isinstance(start, (tuple, list)) or isinstance(end, (tuple, list)):
        if not (isinstance(start, (tuple, list)) and isinstance(end, (tuple, list))):
            raise ValueError("spread endpoints must both be scalars or both vectors")
        if len(start) != len(end):
            raise ValueError(
                f"spread endpoints differ in length: {len(start)} vs {len(end)}"
            )
        return [
            tuple(_lerp(a, b, i, count) for a, b in zip(start, end))
            for i in range(count)
        ]
    return [_lerp(start, end, i, count) for i in range(count)]


def _lerp(start: float, end: float, index: int, count: int) -> float:
    if index == 0:
        return float(start)
    if index == count - 1:
        return float(end)
    return float(start) + (float(end) - float(start)) * index / (count - 1)


@dataclass(frozen=True)
class State:
    """One sprite frame's named state and the actions that realise it."""

    name: str
    actions: tuple[StateAction, ...] = ()


@dataclass(frozen=True)
class Key:
    """One compiled keyframe instruction (constant interpolation implied)."""

    frame: int
    id_type: str
    target: str
    data_path: str
    value: Any
    index: int = -1


@dataclass
class StateTable:
    """Frame-indexed states: ``states[i]`` is sprite frame ``i``."""

    states: list[State] = field(default_factory=list)

    @property
    def frames(self) -> int:
        return len(self.states)

    def compile(self) -> list[Key]:
        """Flatten to keyframe instructions, one per action per frame.

        Every animated channel must be set in *every* state: constant
        interpolation holds the previous key's value, so a channel missing
        from one state would silently leak a neighbouring frame's look into
        it — exactly the class of silent divergence RE-Blend exists to kill.
        Raises :class:`ValueError` naming the gaps instead.
        """
        for action in self._all_actions():
            if action.id_type not in _ID_TYPES:
                raise ValueError(
                    f"unknown id_type {action.id_type!r} (expected one of {_ID_TYPES})"
                )

        channels = {a.key() for a in self._all_actions()}
        missing = [
            f"state {i} ({state.name!r}): {chan}"
            for i, state in enumerate(self.states)
            for chan in sorted(channels - {a.key() for a in state.actions})
        ]
        if missing:
            raise ValueError(
                "state table is not total; every state must set every channel:\n  "
                + "\n  ".join(missing)
            )

        return [
            Key(
                frame=i,
                id_type=action.id_type,
                target=action.target,
                data_path=action.data_path,
                value=action.value,
                index=action.index,
            )
            for i, state in enumerate(self.states)
            for action in state.actions
        ]

    def _all_actions(self) -> Iterable[StateAction]:
        for state in self.states:
            yield from state.actions

    # -- editing (the "state playground": build a table action by action) ----
    #
    # These keep the table *total* by construction — a channel is only ever
    # added to, removed from, or edited across states as a set — so the panel
    # can never assemble a table that :meth:`compile` would then reject.

    def channels(self) -> list[Channel]:
        """Distinct animated channels, in first-seen order across all states."""
        seen: list[Channel] = []
        for action in self._all_actions():
            if action.key() not in seen:
                seen.append(action.key())
        return seen

    def controls(self) -> list[list[Channel]]:
        """Group channels into UI *controls*, one editable unit each.

        The two visibility channels an object gets (``hide_render`` +
        ``hide_viewport``, kept in lockstep so the viewport preview matches the
        render) collapse into a single control; every other channel is its own.
        Order follows :meth:`channels`.
        """
        groups: dict[tuple, list[Channel]] = {}
        order: list[tuple] = []
        for channel in self.channels():
            id_type, target, data_path, _index = channel
            if data_path in ("hide_render", "hide_viewport"):
                gid: tuple = (id_type, target, "visibility")
            else:
                gid = channel
            if gid not in groups:
                groups[gid] = []
                order.append(gid)
            groups[gid].append(channel)
        return [groups[gid] for gid in order]

    def add_actions(self, actions: Iterable[StateAction]) -> None:
        """Add each action as a new channel to *every* state.

        The same action (value included) is appended to all states, so the
        table stays total; the caller then differentiates per-state values via
        :meth:`set_value`. Raises :class:`ValueError` if the table has no
        states to key, or if any channel is already present (adding it twice
        would double-key the same property).
        """
        actions = tuple(actions)
        if not self.states:
            raise ValueError("state table has no states to add an action to")
        existing = set(self.channels())
        for action in actions:
            if action.key() in existing:
                raise ValueError(
                    f"channel already in the table: {describe_channel(action.key())}"
                )
            existing.add(action.key())
        self.states = [
            State(state.name, state.actions + actions) for state in self.states
        ]

    def remove_channel(self, channel: Channel) -> None:
        """Drop a channel from every state (a no-op if it isn't present)."""
        self.states = [
            State(state.name, tuple(a for a in state.actions if a.key() != channel))
            for state in self.states
        ]

    def set_value(self, state_index: int, channel: Channel, value: Any) -> None:
        """Set one state's value for one channel, leaving other states alone.

        Raises :class:`IndexError` for a bad state index and :class:`KeyError`
        if that state doesn't carry the channel (which would mean the table is
        no longer total — the edit path never lets that happen).
        """
        state = self.states[state_index]
        if not any(a.key() == channel for a in state.actions):
            raise KeyError(
                f"state {state_index} ({state.name!r}) has no channel "
                f"{describe_channel(channel)}"
            )
        self.states[state_index] = State(
            state.name,
            tuple(
                replace(a, value=value) if a.key() == channel else a
                for a in state.actions
            ),
        )

    def spread_channel(
        self, channel: Channel, start: Any = None, end: Any = None
    ) -> list[Any]:
        """Fill every state's value for one channel by linear interpolation.

        The designer sets the two extremes — an 8-position selector's first and
        last handle position — and RE-Blend computes what lies between.
        ``start``/``end`` default to the values the first and last states
        already hold, so "place both ends, then spread" needs no retyping.

        This is how a ``sequence_fader``'s travel is made *exactly* evenly
        spaced, which the scripting specification requires of it ("the amount
        the handle travels between each animation frame must be constant") and
        which typing detent positions by hand does not guarantee.
        """
        if not is_interpolatable(channel):
            raise ValueError(
                f"{describe_channel(channel)} is a flag, not a quantity — "
                "there is nothing to interpolate between its states"
            )
        if channel not in self.channels():
            raise KeyError(f"channel not in the table: {describe_channel(channel)}")
        if self.frames < 2:
            raise ValueError(f"a spread needs at least 2 states, got {self.frames}")

        first = self.value_in(0, channel) if start is None else start
        last = self.value_in(self.frames - 1, channel) if end is None else end
        if first is None or last is None:
            raise ValueError(
                f"{describe_channel(channel)} has no value at one end to spread from"
            )

        values = linear_spread(self.frames, first, last)
        for index, value in enumerate(values):
            self.set_value(index, channel, value)
        return values

    def value_in(self, state_index: int, channel: Channel) -> Any:
        """The value a given state assigns to a channel (``None`` if unset)."""
        for action in self.states[state_index].actions:
            if action.key() == channel:
                return action.value
        return None

    # -- persistence (element `re_states` property) --------------------------

    def to_json(self) -> str:
        return json.dumps(
            {
                "states": [
                    {
                        "name": state.name,
                        "actions": [
                            [a.id_type, a.target, a.data_path, a.index, a.value]
                            for a in state.actions
                        ],
                    }
                    for state in self.states
                ]
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "StateTable":
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid state table JSON: {exc}") from exc
        states = []
        for entry in doc.get("states", []):
            actions = tuple(
                StateAction(
                    id_type=str(a[0]),
                    target=str(a[1]),
                    data_path=str(a[2]),
                    index=int(a[3]),
                    value=tuple(a[4]) if isinstance(a[4], list) else a[4],
                )
                for a in entry.get("actions", [])
            )
            states.append(State(name=str(entry.get("name", "")), actions=actions))
        return cls(states=states)


def describe_channel(channel: Channel) -> str:
    """A short human label for a channel, for the panel and error messages.

    Reverses the convenience constructors' data paths back into their
    vocabulary (visibility / emission / location / shape key) so the UI reads
    in the designer's terms rather than raw RNA paths.
    """
    _id_type, target, data_path, index = channel
    if data_path in ("hide_render", "hide_viewport"):
        return f"{target}: visibility"
    if 'inputs["Strength"]' in data_path:
        return f"{target}: emission strength"
    if 'inputs["Color"]' in data_path:
        return f"{target}: emission colour"
    if data_path == "location":
        return f"{target}: location {'XYZ'[index] if 0 <= index < 3 else index}"
    if "shape_keys" in data_path:
        name = data_path.partition('key_blocks["')[2].partition('"]')[0]
        return f"{target}: shape key '{name}'"
    return f"{target}: {data_path}"


#: Default state names per kind, as candidate tuples keyed by frame count.
#: The designer fills in the actions; the *names* encode the SDK-defined
#: meaning of each frame (§4.3, and the per-widget frame contracts in
#: :data:`reblend.model.kinds.WIDGET_FRAME_RULES`).
#:
#: A toggle_button legitimately has either two or four frames, so it carries
#: both namings; the four-frame form adds the held variants.
_DEFAULT_NAMES: dict[str, tuple[tuple[str, ...], ...]] = {
    kinds.LAMP: (("unlit", "lit"),),
    kinds.BUTTON_TOGGLE: (
        ("off", "on"),
        ("off", "off_held", "on", "on_held"),
    ),
    kinds.BUTTON_MOMENTARY: (("released", "held"),),
    kinds.BUTTON_UPDOWN: (("neutral", "up_held", "down_held"),),
    kinds.FADER_HANDLE: (("off", "on", "bypass"),),  # the builtin_onoffbypass case
}


def default_state_table(kind: str, frames: int) -> StateTable | None:
    """A named-but-empty table for a multi-state kind, or None for no rig.

    Knobs get a driver instead of states; statics, backdrops and SDK-supplied
    parts get nothing. When no conventional naming has exactly ``frames``
    entries (a 5-step selector, an 8-frame fader), states fall back to
    ``state_0…state_N−1``.
    """
    if kinds.rig_for_kind(kind) != kinds.RIG_STATES:
        return None
    names = tuple(f"state_{i}" for i in range(frames))
    for candidate in _DEFAULT_NAMES.get(kind, ()):
        if len(candidate) == frames:
            names = candidate
            break
    return StateTable(states=[State(name=name) for name in names])
