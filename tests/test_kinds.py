"""Element-kind derivation from hdgui_2D widget types (§4.2, §4.3)."""

from reblend.model import kinds


def test_widget_kind_mapping():
    assert kinds.kind_for_node([("analog_knob", {})], 61) == kinds.KNOB
    assert kinds.kind_for_node([("zero_snap_knob", {})], 61) == kinds.KNOB
    assert kinds.kind_for_node([("pitch_wheel", {})], 33) == kinds.KNOB
    assert kinds.kind_for_node([("toggle_button", {})], 2) == kinds.BUTTON_TOGGLE
    assert kinds.kind_for_node([("momentary_button", {})], 2) == kinds.BUTTON_MOMENTARY
    assert kinds.kind_for_node([("sequence_meter", {})], 8) == kinds.LAMP
    assert kinds.kind_for_node([("audio_input_socket", {})], 1) == kinds.SOCKET
    assert kinds.kind_for_node([("device_name", {})], 1) == kinds.SDK_SUPPLIED
    assert kinds.kind_for_node([("value_display", {})], 1) == kinds.TEXT_BOUNDS
    assert kinds.kind_for_node([("custom_display", {})], 1) == kinds.DISPLAY


def test_every_sdk_widget_is_mapped():
    """All 25 widgets in the SDK 4.6.0 scripting specification are covered."""
    documented = {
        "analog_knob", "zero_snap_knob", "cv_trim_knob", "pitch_wheel",
        "toggle_button", "momentary_button", "step_button", "radio_button",
        "up_down_button", "popup_button", "sequence_fader", "sequence_meter",
        "value_display", "custom_display", "static_decoration", "device_name",
        "audio_input_socket", "audio_output_socket", "cv_input_socket",
        "cv_output_socket", "patch_browse_group", "patch_name",
        "sample_browse_group", "sample_drop_zone", "placeholder",
    }
    assert set(kinds.WIDGET_KINDS) == documented
    assert all(kind in kinds.ALL_KINDS for kind in kinds.WIDGET_KINDS.values())


def test_stepping_buttons_are_two_frame_buttons_not_selectors():
    """step_button / radio_button animate released-vs-held, like a momentary.

    Their frame count is fixed at two whatever the bound property's step
    count is — a radio group of N values is N two-frame widgets, one per
    `index`, not a single N-frame sheet.
    """
    for widget in ("step_button", "radio_button"):
        assert kinds.kind_for_node([(widget, {})], 2) == kinds.BUTTON_MOMENTARY
        rule = kinds.frame_rule_for_widget(widget)
        assert rule.allowed == (2,)
        assert not rule.steps_bound
        assert rule.permits(2) and not rule.permits(8)


def test_up_down_button_is_three_frames():
    assert kinds.kind_for_node([("up_down_button", {})], 3) == kinds.BUTTON_UPDOWN
    rule = kinds.frame_rule_for_widget("up_down_button")
    assert rule.allowed == (3,)
    assert rule.permits(3) and not rule.permits(2)


def test_toggle_button_takes_two_or_four_frames():
    rule = kinds.frame_rule_for_widget("toggle_button")
    assert rule.permits(2) and rule.permits(4)
    assert not rule.permits(3)
    assert kinds.frame_rule_for_widget("momentary_button").allowed == (2,)


def test_sdk_supplied_parts_are_never_rendered():
    for widget in ("device_name", "placeholder", "cv_trim_knob",
                   "patch_browse_group", "sample_browse_group",
                   "audio_input_socket", "cv_output_socket"):
        kind = kinds.WIDGET_KINDS[widget]
        assert kinds.is_sdk_supplied(kind)
        assert not kinds.renders_art(kind)


def test_bounds_only_widgets_are_not_rendered():
    for widget in ("value_display", "popup_button", "patch_name",
                   "sample_drop_zone"):
        assert not kinds.renders_art(kinds.WIDGET_KINDS[widget])
    # A custom display's own region *is* authored art; it just can't animate.
    assert kinds.renders_art(kinds.DISPLAY)


def test_interactive_kinds():
    for kind in (kinds.KNOB, kinds.BUTTON_TOGGLE, kinds.FADER_HANDLE,
                 kinds.SOCKET, kinds.TEXT_BOUNDS):
        assert kinds.is_interactive(kind)
    for kind in (kinds.BACKDROP, kinds.STATIC, kinds.LAMP, kinds.DISPLAY):
        assert not kinds.is_interactive(kind)


def test_backdrop_wins_over_everything():
    assert kinds.kind_for_node([("analog_knob", {})], 61, is_backdrop=True) == kinds.BACKDROP
    assert kinds.kind_for_node([], 1, is_backdrop=True) == kinds.BACKDROP


def test_sequence_fader_always_bakes_its_travel():
    """`handle_size` is hit behaviour, not a drawing mode.

    The specification requires a fader's animation to include the handle's
    entire travel, one frame per position, regardless of `handle_size` —
    which only configures where a press outside the handle jumps to. There
    is no "1-frame moving handle" authoring mode (this closes design §10.4).
    """
    for attrs in ({"handle_size": 0}, {}, {"handle_size": 60}):
        assert kinds.kind_for_node([("sequence_fader", attrs)], 3) == kinds.FADER_HANDLE
    rule = kinds.frame_rule_for_widget("sequence_fader")
    assert rule.steps_bound
    assert rule.permits(3) and rule.permits(64)


def test_static_decoration_cannot_be_animated():
    """Multi-frame art under a static_decoration is a mistake, not a lamp.

    The animated-indicator widget is sequence_meter; static_decoration
    graphics "cannot be animated" per the specification.
    """
    assert kinds.kind_for_node([("static_decoration", {})], 2) == kinds.STATIC
    assert kinds.kind_for_node([("static_decoration", {})], 1) == kinds.STATIC
    rule = kinds.frame_rule_for_widget("static_decoration")
    assert rule.permits(1) and not rule.permits(2)


def test_unbound_node_defaults():
    assert kinds.kind_for_node([], 1) == kinds.STATIC
    assert kinds.kind_for_node([], 2) == kinds.LAMP


def test_unknown_widget_defaults_to_static():
    assert kinds.kind_for_node([("some_future_widget", {})], 1) == kinds.STATIC


def test_interactive_widget_outranks_static_companion():
    widgets = [("static_decoration", {}), ("toggle_button", {})]
    assert kinds.kind_for_node(widgets, 2) == kinds.BUTTON_TOGGLE


def test_rig_flavours():
    assert kinds.rig_for_kind(kinds.KNOB) == kinds.RIG_DRIVER
    for kind in (kinds.BUTTON_TOGGLE, kinds.BUTTON_MOMENTARY, kinds.BUTTON_UPDOWN,
                 kinds.FADER_HANDLE, kinds.SELECTOR, kinds.LAMP):
        assert kinds.rig_for_kind(kind) == kinds.RIG_STATES
    for kind in (kinds.STATIC, kinds.BACKDROP, kinds.SOCKET, kinds.SDK_SUPPLIED,
                 kinds.TEXT_BOUNDS, kinds.DISPLAY):
        assert kinds.rig_for_kind(kind) is None
