"""Shadow ownership: who casts, who catches, who sits it out (§5.1)."""

import pytest

from reblend.model import kinds
from reblend.render import shadows

BACKGROUND = kinds.SHADOW_BACKGROUND
ELEMENT = kinds.SHADOW_ELEMENT


def role(active_owner=BACKGROUND, sibling_kind=kinds.KNOB,
         sibling_owner=BACKGROUND, inactive_render=shadows.INACTIVE_SHADOW):
    return shadows.sibling_role(
        active_owner, sibling_kind, sibling_owner, inactive_render
    )


# --- the default: nothing changes for a scene that never touches the setting


def test_background_owners_cast_under_shadow_isolation():
    assert role() == shadows.ROLE_CASTER


def test_hidden_isolation_still_hides_everything():
    assert role(inactive_render=shadows.INACTIVE_HIDDEN) == shadows.ROLE_HIDDEN


def test_backdrop_is_an_ordinary_caster_for_a_background_owner():
    # A knob's shadow belongs in the plate, so the plate is not a catcher
    # here — it is just another neighbour that may shadow the knob.
    assert role(sibling_kind=kinds.BACKDROP) == shadows.ROLE_CASTER


# --- the two-sided switch


def test_element_owner_catches_its_shadow_on_the_backdrop():
    assert role(active_owner=ELEMENT, sibling_kind=kinds.BACKDROP) == (
        shadows.ROLE_CATCHER
    )


def test_element_owner_admits_no_other_caster():
    # Exactly one shadow lands in an element-owned sheet: its own. A
    # neighbour's is already baked into the plate, so catching it here too
    # would darken the overlap twice.
    assert role(active_owner=ELEMENT, sibling_kind=kinds.KNOB) == shadows.ROLE_HIDDEN


def test_element_owner_catches_even_under_hidden_isolation():
    # Ownership is a correctness property; the isolation preference does not
    # get to suppress the catcher and silently drop the shadow.
    assert role(
        active_owner=ELEMENT,
        sibling_kind=kinds.BACKDROP,
        inactive_render=shadows.INACTIVE_HIDDEN,
    ) == shadows.ROLE_CATCHER


def test_an_element_owner_never_casts_into_someone_elses_sheet():
    # The other side of the switch: a fader that carries its own shadow must
    # not also bake one into the backdrop it is being rendered over.
    assert role(sibling_kind=kinds.FADER_HANDLE, sibling_owner=ELEMENT) == (
        shadows.ROLE_HIDDEN
    )


# --- kind-derived defaults


def test_fader_defaults_to_owning_its_shadow():
    # sequence_fader bakes the handle's entire travel, one frame per position,
    # so the handle is drawn somewhere different in every frame.
    assert kinds.default_shadow_owner(kinds.FADER_HANDLE) == ELEMENT


@pytest.mark.parametrize(
    "kind",
    [kinds.KNOB, kinds.BUTTON_TOGGLE, kinds.BUTTON_MOMENTARY, kinds.LAMP,
     kinds.STATIC, kinds.BACKDROP, kinds.SELECTOR],
)
def test_stationary_kinds_default_to_the_background(kind):
    assert kinds.default_shadow_owner(kind) == BACKGROUND


def test_unknown_kind_defaults_to_the_background():
    assert kinds.default_shadow_owner("something_a_future_sdk_adds") == BACKGROUND


# --- reducing per-frame samples to a travel measurement
#
# Samples are (along the camera axis, in-plane u, in-plane v) per frame.


def test_a_still_element_has_no_travel():
    travel = shadows.travel_from_samples({"knob": [(0.0, 0.0, 0.0)] * 4}, ppb=10.0)
    assert (travel.across, travel.depth) == (0.0, 0.0)
    assert not travel.moves


def test_in_plane_movement_is_measured_in_panel_pixels():
    samples = [(0.0, 0.0, 0.0), (0.0, 1.5, 0.0)]
    assert shadows.travel_from_samples({"handle": samples}, ppb=10.0).across == 15.0


def test_a_diagonal_slide_counts_its_true_distance():
    # Not max(u, v): 3-4-5, so the answer is 5 units, not 4.
    samples = [(0.0, 0.0, 0.0), (0.0, 3.0, 4.0)]
    assert shadows.travel_from_samples({"handle": samples}, ppb=1.0).across == 5.0


def test_depth_and_in_plane_are_reported_separately():
    samples = [(0.0, 0.0, 0.0), (2.0, 1.0, 0.0)]
    travel = shadows.travel_from_samples({"cap": samples}, ppb=1.0)
    assert (travel.across, travel.depth) == (1.0, 2.0)


def test_travel_is_the_widest_single_object_not_the_average():
    # A moving handle beside a static bracket in one collection: measuring
    # their combined bounds would water the handle's travel down.
    travel = shadows.travel_from_samples(
        {
            "bracket": [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0)],
            "handle": [(0.0, 0.0, 0.0), (0.0, 6.0, 0.0)],
        },
        ppb=1.0,
    )
    assert travel.across == 6.0


def test_a_single_frame_sample_cannot_move():
    assert not shadows.travel_from_samples({"knob": [(9.0, 9.0, 9.0)]}, ppb=1.0).moves
