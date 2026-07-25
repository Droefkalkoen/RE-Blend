format_version = "2.0"

front = jbox.panel{
	graphics = { node = "Panel_front_bg" },
	widgets = {
		jbox.analog_knob{
			graphics = { node = "knob_threshold" },
			value = "/custom_properties/threshold",
		},
		jbox.toggle_button{
			graphics = { node = "SilenceSwitch" },
			value = "/custom_properties/silence_switch",
		},
		-- An animated indicator is a sequence_meter: static_decoration
		-- graphics "cannot be animated" per the scripting specification.
		jbox.sequence_meter{
			graphics = { node = "lamp_signal" },
			value = "/custom_properties/signal_level",
		},
		-- static_decoration declares its node as `main_node`, and carries a
		-- blend_mode RE-Blend has no model for: both must survive a round trip.
		jbox.static_decoration{
			graphics = { main_node = "logo_plate" },
			blend_mode = "luminance",
			ui_name = jbox.ui_text("logo plate"),
		},
		jbox.device_name{
			graphics = { node = "DeviceName" },
		},
	},
}

back = jbox.panel{
	graphics = { node = "Panel_back_bg" },
	widgets = {
		jbox.audio_input_socket{
			graphics = { node = "MainInLeft" },
			socket = "/audio_inputs/InLeft",
		},
		jbox.audio_input_socket{
			graphics = { node = "MainInRight" },
			socket = "/audio_inputs/InRight",
		},
		-- Required on the (non-folded) back panel of every device.
		jbox.placeholder{
			graphics = { node = "Placeholder" },
		},
		jbox.device_name{
			graphics = { node = "DeviceName" },
		},
	},
}

folded_front = jbox.panel{
	graphics = { node = "Panel_folded_front_bg" },
	widgets = {
		jbox.sequence_fader{
			graphics = { node = "OnOffBypass" },
			value = "/custom_properties/builtin_onoffbypass",
			handle_size = 0,
			inverted = false,
		},
		jbox.device_name{
			graphics = { node = "DeviceName" },
		},
	},
}

folded_back = jbox.panel{
	graphics = { node = "Panel_folded_back_bg" },
	cable_origin = { node = "CableOrigin" },
	widgets = {
		jbox.device_name{
			graphics = { node = "DeviceName" },
		},
	},
}
