format_version = "2.0"

-- NOTE: hand-maintained offsets below are load-bearing documentation;
-- RE-Blend must never reflow or reformat this file when patching.
--
-- Panel shape follows the SDK / RE Edit convention (see SilenceDetectionEffect):
-- the backdrop — and any point node such as CableOrigin — is a *named* entry at
-- the panel's top level, while the widget nodes live inside a single *unnamed*
-- sub-table. hdgui_2D.lua binds every node by name regardless of that nesting.

front = {
	Panel_front_bg = {
		{ path = "Panel_Front" },
	},

	{
		-- threshold knob, 61-frame turntable
		knob_threshold = {
			offset = { 950, 120 },
			{ path = "Knob_65x65_61frames", frames = 61 },
		},

		SilenceSwitch = {
			offset = { 1810, 145 },
			{ path = "Button_50x35_2frames", frames = 2 },
		},

		-- lamp group: two LEDs sharing a group offset
		lamp_group = {
			offset = { 300, 100 },
			lamp_signal = {
				offset = { 0, 0 },
				{ path = "Lamp_15x15_2frames", frames = 2 },
			},
			lamp_silence = {
				offset = { 30, 0 },
				{ path = "Lamp_15x15_2frames", frames = 2 },
			},
		},

		-- static_decoration art is a single frame and may not animate
		logo_plate = {
			offset = { 2600, 100 },
			{ path = "Logo_120x40_1frames" },
		},

		DeviceName = {
			offset = { 1665, 25 },
			{ path = "Tape_Horizontal_1frames" },
		},
	},
}

back = {
	Panel_back_bg = {
		{ path = "Panel_Back" },
	},

	{
		MainInLeft = {
			offset = { 105, 105 },
			{ path = "SharedAudioJack" },
		},
		MainInRight = {
			offset = { 105, 210 },
			{ path = "SharedAudioJack" },
		},
		-- Reserved space Reason keeps clear: fixed 300 x 100.
		Placeholder = {
			offset = { 2100, 200 },
			{ path = "Placeholder" },
		},
		DeviceName = {
			offset = { 1665, 25 },
			{ path = "Tape_Horizontal_1frames" },
		},
	},
}

folded_front = {
	Panel_folded_front_bg = {
		{ path = "Panel_Folded_Front" },
	},

	{
		OnOffBypass = {
			offset = { 100, 35 },
			{ path = "Fader_25x60_3frames", frames = 3 },
		},
		DeviceName = {
			offset = { 660, 50 },
			{ path = "Tape_Horizontal_1frames" },
		},
	},
}

folded_back = {
	Panel_folded_back_bg = {
		{ path = "Panel_Folded_Back" },
	},
	CableOrigin = {
		offset = { 1885, 65 },
	},

	{
		DeviceName = {
			offset = { 660, 50 },
			{ path = "Tape_Horizontal_1frames" },
		},
	},
}
