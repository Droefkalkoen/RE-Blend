"""Element kinds and their derivation from hdgui_2D widget types (§4.2, §4.3).

An RE Element's ``re_kind`` decides which rig it gets: knobs get the
turntable driver, multi-state controls get a state table compiled to constant
keyframes, statics/backdrops/SDK-supplied parts get no rig at all. The kind is
derived on import from the ``jbox.<widget>{...}`` constructors bound to the
node — the widget type is what Reason will *do* with the sheet, so it decides
what the sheet must *contain*.

This mapping is **normative**, not inferred: the SDK 4.6.0 Jukebox scripting
specification documents all 25 widget types and, for the ones whose art is an
animation, exactly how many frames that animation must have. Those rules are
transcribed into :data:`WIDGET_FRAME_RULES` and summarised in
``docs/sdk-gui-reference.md``. Unknown widget types (a future SDK adds one)
still map to ``static`` — rendering one frame is always safe — and the
validation report carries a widget↔kind check so a wrong guess is visible
rather than silent.

Three groups need care because RE-Blend must *not* produce their art:

- **SDK-supplied parts** (:data:`SDK_SUPPLIED`, :data:`SOCKET`) — the spec says
  "you cannot change the appearance of this widget" and names a stock image.
  RE-Blend installs those from the user's SDK instead of rendering them
  (:mod:`reblend.project.sdk_parts`).
- **Text/bounds widgets** (:data:`TEXT_BOUNDS`) — the graphics definition is
  only a rectangle Reason draws text into, or an invisible hit area. The GUI
  designer manual's worked example is explicit that the box "will not be drawn
  in Reason".
- **Custom displays** (:data:`DISPLAY`) — the region belongs to ``display.lua``
  at runtime; the node's art is a single static frame and may never animate.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

__all__ = [
    "KNOB",
    "BUTTON_TOGGLE",
    "BUTTON_MOMENTARY",
    "BUTTON_UPDOWN",
    "FADER_HANDLE",
    "SELECTOR",
    "LAMP",
    "BACKDROP",
    "STATIC",
    "SOCKET",
    "SDK_SUPPLIED",
    "TEXT_BOUNDS",
    "DISPLAY",
    "ALL_KINDS",
    "RIG_DRIVER",
    "RIG_STATES",
    "SHADOW_BACKGROUND",
    "SHADOW_ELEMENT",
    "SHADOW_OWNERS",
    "WIDGET_KINDS",
    "WIDGET_FRAME_RULES",
    "FrameRule",
    "kind_for_node",
    "rig_for_kind",
    "default_shadow_owner",
    "is_sdk_supplied",
    "is_interactive",
    "renders_art",
    "frame_rule_for_widget",
]

KNOB = "knob"
BUTTON_TOGGLE = "button_toggle"
BUTTON_MOMENTARY = "button_momentary"
BUTTON_UPDOWN = "button_updown"
FADER_HANDLE = "fader_handle"
SELECTOR = "selector"
LAMP = "lamp"
BACKDROP = "backdrop"
STATIC = "static"
SOCKET = "socket"
SDK_SUPPLIED = "sdk_supplied"
TEXT_BOUNDS = "text_bounds"
DISPLAY = "display"

ALL_KINDS = (
    KNOB,
    BUTTON_TOGGLE,
    BUTTON_MOMENTARY,
    BUTTON_UPDOWN,
    FADER_HANDLE,
    SELECTOR,
    LAMP,
    BACKDROP,
    STATIC,
    SOCKET,
    SDK_SUPPLIED,
    TEXT_BOUNDS,
    DISPLAY,
)

#: Rig flavours (§4.3). ``None`` means the element renders as-is, one frame.
RIG_DRIVER = "driver"  # auto-generated turntable rotation driver
RIG_STATES = "states"  # state table compiled to constant-interpolation keys

_RIGS = {
    KNOB: RIG_DRIVER,
    BUTTON_TOGGLE: RIG_STATES,
    BUTTON_MOMENTARY: RIG_STATES,
    BUTTON_UPDOWN: RIG_STATES,
    FADER_HANDLE: RIG_STATES,
    SELECTOR: RIG_STATES,
    LAMP: RIG_STATES,
}

#: Kinds whose art Reason (or the SDK) supplies. RE-Blend never renders these;
#: it copies the stock image into GUI2D/ from the user's SDK.
_SDK_SUPPLIED_KINDS = frozenset({SOCKET, SDK_SUPPLIED})

#: Kinds whose device_2D graphic is not drawn as authored art at all — Reason
#: uses the rectangle for text layout or hit-testing only.
_NON_ART_KINDS = frozenset(_SDK_SUPPLIED_KINDS | {TEXT_BOUNDS})

#: hdgui_2D widget constructor name -> element kind. Complete as of SDK 4.6.0;
#: every widget in the scripting specification appears exactly once.
WIDGET_KINDS = {
    # -- animated controls RE-Blend renders -------------------------------
    "analog_knob": KNOB,
    "zero_snap_knob": KNOB,
    "pitch_wheel": KNOB,
    "toggle_button": BUTTON_TOGGLE,
    "momentary_button": BUTTON_MOMENTARY,
    # step_button and radio_button are *not* selectors: both use the same
    # two-frame released/held animation as a momentary button, whatever the
    # bound property's step count. A radio group of N values is N separate
    # two-frame widgets, one per `index`, not one N-frame sheet.
    "step_button": BUTTON_MOMENTARY,
    "radio_button": BUTTON_MOMENTARY,
    "up_down_button": BUTTON_UPDOWN,
    "sequence_fader": FADER_HANDLE,
    "sequence_meter": LAMP,
    "static_decoration": STATIC,
    # -- bounds-only: Reason draws text, or nothing at all ----------------
    "value_display": TEXT_BOUNDS,
    "popup_button": TEXT_BOUNDS,
    "patch_name": TEXT_BOUNDS,
    "sample_drop_zone": TEXT_BOUNDS,
    "custom_display": DISPLAY,
    # -- fixed appearance: the SDK supplies the image ---------------------
    "device_name": SDK_SUPPLIED,
    "placeholder": SDK_SUPPLIED,
    "cv_trim_knob": SDK_SUPPLIED,
    "patch_browse_group": SDK_SUPPLIED,
    "sample_browse_group": SDK_SUPPLIED,
    "audio_input_socket": SOCKET,
    "audio_output_socket": SOCKET,
    "cv_input_socket": SOCKET,
    "cv_output_socket": SOCKET,
}

#: Kinds that outrank a bare STATIC guess when several widgets bind one node
#: (e.g. a node drawn by both a toggle_button and a static_decoration is a
#: button). Ordered by how specific the binding is.
_INTERACTIVE = frozenset(ALL_KINDS) - {STATIC, BACKDROP}


class FrameRule:
    """The frame counts one widget type's animation may have.

    ``allowed`` is an explicit tuple of legal counts, or ``()`` when the count
    is free (an ``analog_knob`` may have any resolution). ``steps_bound``
    marks the widgets whose frame count must additionally equal the ``steps``
    of the property they drive.
    """

    __slots__ = ("allowed", "steps_bound", "note")

    def __init__(
        self, allowed: tuple[int, ...] = (), steps_bound: bool = False, note: str = ""
    ) -> None:
        self.allowed = allowed
        self.steps_bound = steps_bound
        self.note = note

    def permits(self, frames: int) -> bool:
        return not self.allowed or frames in self.allowed

    def describe(self) -> str:
        if not self.allowed:
            return "any frame count"
        if len(self.allowed) == 1:
            return f"exactly {self.allowed[0]} frames"
        joined = " or ".join(str(n) for n in self.allowed)
        return f"{joined} frames"


#: Per-widget animation frame-count contract, transcribed from the SDK 4.6.0
#: Jukebox scripting specification. Widgets absent from this table place no
#: constraint on the frame count (their graphics is not an animation).
WIDGET_FRAME_RULES = {
    "toggle_button": FrameRule(
        (2, 4),
        note="2 = off/on; 4 = off, off-held, on, on-held",
    ),
    "momentary_button": FrameRule((2,), note="released, held"),
    "step_button": FrameRule(
        (2,),
        note="released, held — independent of the property's step count",
    ),
    "radio_button": FrameRule(
        (2,),
        note="released, held — one widget per value via `index`, "
        "not one frame per value",
    ),
    "up_down_button": FrameRule((3,), note="neutral, up-held, down-held"),
    "sequence_fader": FrameRule(
        steps_bound=True,
        note="the handle's full travel baked one frame at a time, linear; "
        "`handle_size` configures press-to-jump hit behaviour, not drawing",
    ),
    # Graphics that may not be animated at all.
    "static_decoration": FrameRule((1,), note="static_decoration cannot be animated"),
    "custom_display": FrameRule((1,), note="a custom display cannot be animated"),
}


def frame_rule_for_widget(widget: str) -> FrameRule | None:
    """The frame-count contract for a widget constructor name, if it has one."""
    return WIDGET_FRAME_RULES.get(widget)


def kind_for_node(
    widgets: Sequence[tuple[str, Mapping[str, Any]]],
    frames: int,
    is_backdrop: bool = False,
) -> str:
    """Derive the element kind for one device_2D node.

    ``widgets`` are the ``(constructor_name, attrs)`` pairs of the hdgui_2D
    widgets bound to the node (usually one, sometimes none, occasionally
    several). ``frames`` is the node's declared frame count.

    Two fallbacks apply when no widget claims the node: the node named by a
    panel's ``graphics.node`` is the panel backdrop, and multi-frame art that
    nothing binds is treated as a lamp so the designer still gets a state rig.
    A node bound only by ``static_decoration`` is static — per the spec that
    widget's graphics cannot be animated, so multi-frame art under one is a
    validation finding, not a lamp.
    """
    if is_backdrop:
        return BACKDROP

    derived = [
        WIDGET_KINDS[name] for name, _attrs in widgets if name in WIDGET_KINDS
    ]
    for kind in derived:
        if kind in _INTERACTIVE:
            return kind
    if derived:
        return derived[0]
    if frames > 1:
        # Multi-frame art no widget binds: most likely an indicator whose
        # sequence_meter binding is still to be written.
        return LAMP
    return STATIC


def rig_for_kind(kind: str) -> str | None:
    """Which rig flavour a kind gets: driver, state table, or none."""
    return _RIGS.get(kind)


#: Who owns an element's *cast* shadow (§5.1). ``SHADOW_BACKGROUND`` bakes it
#: into the panel plate underneath — right for anything that holds still, and
#: cheaper (one shadow in the backdrop instead of the same shadow repeated in
#: every frame of the sheet). ``SHADOW_ELEMENT`` renders it into the element's
#: own sheet, where it travels with the art frame by frame.
SHADOW_BACKGROUND = "background"
SHADOW_ELEMENT = "element"
SHADOW_OWNERS = (SHADOW_BACKGROUND, SHADOW_ELEMENT)

#: Kinds whose silhouette moves *relative to the panel* across their own
#: frames, so a shadow baked into the plate would sit frozen at one position
#: while the art slides away from it.
#:
#: Only the fader qualifies from the kind alone: the scripting specification
#: requires a ``sequence_fader`` sheet to bake "the entire travel distance of
#: the handle", one frame per position (docs/sdk-gui-reference.md), so the
#: handle is drawn somewhere different in every frame. A knob spins in place
#: and a button's cap depresses within its own outline — their contact shadow
#: is identical frame to frame, so it belongs to the plate. A button with real
#: travel is a judgement call the designer makes per element; this is only the
#: default RE-Blend starts it at.
_SHADOW_ELEMENT_KINDS = frozenset({FADER_HANDLE})


def default_shadow_owner(kind: str) -> str:
    """Where a freshly imported element of this kind should put its shadow."""
    if kind in _SHADOW_ELEMENT_KINDS:
        return SHADOW_ELEMENT
    return SHADOW_BACKGROUND


#: Kinds that never respond to user input. Everything else can, so everything
#: else is subject to the required 25 px interaction-free edge margin.
_NON_INTERACTIVE = frozenset({BACKDROP, STATIC, LAMP, DISPLAY})


def is_interactive(kind: str) -> bool:
    """Whether a widget of this kind responds to user input."""
    return kind not in _NON_INTERACTIVE


def is_sdk_supplied(kind: str) -> bool:
    """Whether Reason/the SDK owns this element's appearance.

    The scripting specification says of each of these widgets that "you cannot
    change the appearance of this widget" and names the stock image to use.
    RE-Blend installs those images rather than rendering over them.
    """
    return kind in _SDK_SUPPLIED_KINDS


def renders_art(kind: str) -> bool:
    """Whether RE-Blend should render a sprite sheet for this kind.

    False for SDK-supplied parts and for widgets whose graphics definition is
    only a text box or hit area. :data:`DISPLAY` returns True: the custom
    display's own region *is* authored art (a bezel, a screen backdrop), it
    simply may not be animated.
    """
    return kind not in _NON_ART_KINDS
