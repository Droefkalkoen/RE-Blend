"""RE Element schema: defaults, versioning, migrations, props round-trip (§4.2, §8)."""

import pytest

from reblend.model import schema
from reblend.model.schema import ElementData, Placement


def test_defaults_carry_current_version():
    assert schema.DEFAULTS["re_schema"] == schema.SCHEMA_VERSION


def test_migrate_pre_schema_props_fills_defaults():
    # An M0-era hand-tagged element: some keys, no re_schema.
    props = {"re_node": "knob_tone", "re_frames": 61}
    assert schema.migrate(props) is True
    assert props["re_schema"] == schema.SCHEMA_VERSION
    assert props["re_node"] == "knob_tone"      # existing values kept
    assert props["re_frames"] == 61
    assert props["re_kind"] == "static"         # missing values defaulted
    assert props["re_sweep_deg"] == 300.0


def test_migrate_v1_adds_preview_frame():
    # An M1-era element: full v1 property set, no re_preview_frame.
    props = dict(schema.DEFAULTS)
    props["re_schema"] = 1
    del props["re_preview_frame"]
    assert schema.migrate(props) is True
    assert props["re_schema"] == schema.SCHEMA_VERSION
    assert props["re_preview_frame"] == 0


def test_migrate_v2_pins_shadow_owner_to_the_background():
    # An M2-era element: full v2 property set, no re_shadow_owner. Every
    # shadow went into the plate before the property existed, so the
    # migration must say so outright rather than let a fader pick up the
    # kind-derived default and silently re-render differently.
    props = dict(schema.DEFAULTS)
    props["re_schema"] = 2
    props["re_kind"] = "fader_handle"
    del props["re_shadow_owner"]
    assert schema.migrate(props) is True
    assert props["re_shadow_owner"] == "background"


def test_migrate_v3_adds_rotor():
    # An M2-era element: full v3 property set, no re_rotor. It starts
    # unrecorded; the Blender side backfills the name at first rig time.
    props = dict(schema.DEFAULTS)
    props["re_schema"] = 3
    props["re_kind"] = "knob"
    del props["re_rotor"]
    assert schema.migrate(props) is True
    assert props["re_schema"] == schema.SCHEMA_VERSION
    assert props["re_rotor"] == ""


def test_migrate_current_version_is_a_noop():
    props = dict(schema.DEFAULTS)
    assert schema.migrate(props) is False


def test_newer_schema_refuses():
    props = {"re_schema": schema.SCHEMA_VERSION + 1}
    with pytest.raises(ValueError, match="newer"):
        schema.migrate(props)


def test_every_version_gap_has_a_migration():
    assert set(schema.MIGRATIONS) == set(range(schema.SCHEMA_VERSION))


def _sample_data():
    return ElementData(
        node="lamp_signal",
        path="Lamp_15x15_2frames",
        kind="lamp",
        frames=2,
        frame_w=15,
        frame_h=15,
        placements=(
            Placement("front", "lamp_signal", 300, 100),
            Placement("front", "lamp_silence", 330, 100),
        ),
        # Properties always carry re_rotor, so reading them back yields ""
        # (recorded as absent), never None (unknown) — say so explicitly to
        # keep the round-trip comparison exact.
        rotor="",
    )


def test_props_roundtrip():
    data = _sample_data()
    props = schema.data_to_props(data)
    assert props["re_schema"] == schema.SCHEMA_VERSION
    assert props["re_node"] == "lamp_signal"
    assert props["re_panel"] == "front"
    assert (props["re_offset_x"], props["re_offset_y"]) == (300, 100)

    back = schema.props_to_data(props)
    assert back == data


def test_props_without_placements_fall_back_to_singular_fields():
    props = dict(schema.DEFAULTS)
    props.update(re_node="knob_a", re_path="Knob", re_panel="front",
                 re_offset_x=10, re_offset_y=20, re_placements="not json")
    data = schema.props_to_data(props)
    assert data.placements == (Placement("front", "knob_a", 10.0, 20.0),)


def test_is_element():
    assert schema.is_element({"re_path": "Knob"})
    assert schema.is_element({"re_node": "knob_a"})
    assert not schema.is_element({"other": 1})


def _placed(x=100.0, y=200.0):
    return ElementData(
        node="knob_a", path="Knob",
        placements=(Placement("front", "knob_a", x, y),),
    )


def test_effective_placements_prefer_the_scene_reading():
    data = _placed()
    assert data.effective_placements == data.placements   # no scene reading yet
    data.derived_placements = (Placement("front", "knob_a", 140.0, 185.0),)
    assert data.effective_placements == data.derived_placements


def test_moved_pairs_stored_with_derived():
    data = _placed()
    data.derived_placements = (Placement("front", "knob_a", 140.0, 185.0),)
    (stored, derived), = data.moved
    assert (stored.x, derived.x) == (100.0, 140.0)


def test_moved_ignores_sub_pixel_jitter():
    data = _placed()
    data.derived_placements = (Placement("front", "knob_a", 100.4, 199.6),)
    assert data.moved == ()  # rounds to the same whole pixel the Lua stores


def test_moved_ignores_placements_the_scene_says_nothing_about():
    data = _placed()
    data.derived_placements = (Placement("folded_front", "knob_a", 9.0, 9.0),)
    assert data.moved == ()  # different node/panel: nothing to compare against


def test_shadow_owner_defaults_by_kind():
    assert ElementData(node="k", path="Knob", kind="knob").shadow_owner == "background"
    assert ElementData(node="f", path="Fader",
                       kind="fader_handle").shadow_owner == "element"


def test_a_stored_shadow_owner_survives_the_props_round_trip():
    # The designer's override outranks the kind default in both directions.
    data = ElementData(node="k", path="Knob", kind="knob", shadow_owner="element")
    props = schema.data_to_props(data)
    assert props["re_shadow_owner"] == "element"
    assert schema.props_to_data(props).shadow_owner == "element"


def test_a_blanked_shadow_owner_falls_back_to_the_kind_default():
    props = dict(schema.DEFAULTS)
    props.update(re_node="f", re_path="Fader", re_kind="fader_handle",
                 re_shadow_owner="")
    assert schema.props_to_data(props).shadow_owner == "element"


def test_states_round_trip_through_props():
    props = dict(schema.DEFAULTS)
    props.update(re_node="knob_a", re_path="Knob", re_states='{"states": []}')
    assert schema.props_to_data(props).states == '{"states": []}'


def test_rotor_round_trips_through_props():
    data = ElementData(node="knob_a", path="Knob", kind="knob", rotor="Rotor.001")
    props = schema.data_to_props(data)
    assert props["re_rotor"] == "Rotor.001"
    assert schema.props_to_data(props).rotor == "Rotor.001"


def test_unknown_rotor_persists_as_recorded_absent():
    # Spec-derived data never knows the rotor (None); once written to props
    # and read back it is "" — a real answer: read, and none recorded yet.
    data = ElementData(node="knob_a", path="Knob", kind="knob")
    assert data.rotor is None
    props = schema.data_to_props(data)
    assert props["re_rotor"] == ""
    assert schema.props_to_data(props).rotor == ""


def test_panels_deduplicate_in_order():
    data = ElementData(
        node="DeviceName",
        path="Tape",
        placements=(
            Placement("front", "DeviceName", 0, 0),
            Placement("back", "DeviceName", 0, 0),
            Placement("front", "DeviceName", 5, 5),
        ),
    )
    assert data.panels == ("front", "back")
