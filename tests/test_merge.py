"""Re-import merge diff (§6.1): added / removed / changed detection."""

import pytest

from reblend.model.schema import ElementData, Placement
from reblend.project import merge
from reblend.project.link import load_project


@pytest.fixture
def project(silence_detector):
    return load_project(silence_detector)


def scene_elements(project):
    """A scene that mirrors the project exactly (a just-imported state)."""
    return [spec.to_element_data() for spec in project.specs]


def test_identical_scene_diffs_empty(project):
    assert merge.diff_link(project.specs, scene_elements(project)) == []


def test_new_node_in_lua_is_added(project):
    elements = [e for e in scene_elements(project) if e.path != "Knob_65x65_61frames"]
    items = merge.diff_link(project.specs, elements)
    assert [(i.path, i.status) for i in items] == [("Knob_65x65_61frames", merge.ADDED)]
    assert items[0].spec is not None and items[0].element is None
    assert "knob" in items[0].summary


def test_scene_element_missing_from_lua_is_removed_flag_only(project):
    elements = scene_elements(project)
    elements.append(
        ElementData(node="old_knob", path="Old_Knob", kind="knob", frames=61,
                    placements=(Placement("front", "old_knob", 5, 5),))
    )
    items = merge.diff_link(project.specs, elements)
    assert [(i.path, i.status) for i in items] == [("Old_Knob", merge.REMOVED)]
    assert "kept" in items[0].summary  # flagged, never auto-deleted


def test_changed_values_list_their_fields(project):
    elements = scene_elements(project)
    knob = next(e for e in elements if e.path == "Knob_65x65_61frames")
    knob.frames = 31
    knob.placements = (Placement("front", "knob_threshold", 900, 130),)
    items = merge.diff_link(project.specs, elements)
    assert len(items) == 1 and items[0].status == merge.CHANGED
    fields = {change.field for change in items[0].changes}
    assert fields == {"frames", "placements"}
    frames = next(c for c in items[0].changes if c.field == "frames")
    assert (frames.mine, frames.theirs) == ("31", "61")


def test_a_dragged_registration_empty_is_a_scene_side_change(project):
    # Moving an empty changes derived_placements only; the stored mirror still
    # agrees with the Lua. The diff must see the move, or "I repositioned it
    # and nothing noticed" is the outcome.
    elements = scene_elements(project)
    knob = next(e for e in elements if e.path == "Knob_65x65_61frames")
    stored = knob.placements[0]
    knob.derived_placements = (Placement(stored.panel, stored.node,
                                         stored.x + 40, stored.y - 15),)
    items = merge.diff_link(project.specs, elements)
    assert [(i.path, i.status) for i in items] == [
        ("Knob_65x65_61frames", merge.CHANGED)
    ]
    change = next(c for c in items[0].changes if c.field == "placements")
    assert change.mine != change.theirs
    assert f"{stored.x + 40:.0f}" in change.mine


def test_an_unmoved_empty_still_diffs_empty(project):
    elements = scene_elements(project)
    for element in elements:
        element.derived_placements = element.placements
    assert merge.diff_link(project.specs, elements) == []


def test_unsized_side_never_conflicts_on_frame_size(project):
    # The probed spec size is authoritative only when both sides are set and
    # disagree; an unset scene size (fresh import, no sheet yet) is not a diff.
    elements = scene_elements(project)
    for element in elements:
        element.frame_w = element.frame_h = 0
    assert merge.diff_link(project.specs, elements) == []


def test_kind_change_is_reported(project):
    elements = scene_elements(project)
    switch = next(e for e in elements if e.path == "Button_50x35_2frames")
    switch.kind = "lamp"
    items = merge.diff_link(project.specs, elements)
    assert len(items) == 1
    change = items[0].changes[0]
    assert (change.field, change.mine, change.theirs) == ("kind", "lamp", "button_toggle")


def test_elements_without_a_path_are_ignored(project):
    elements = scene_elements(project) + [ElementData(node="x", path="")]
    assert merge.diff_link(project.specs, elements) == []
