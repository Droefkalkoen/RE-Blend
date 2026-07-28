"""Save-able validation and sync reports (§6.3, §6.1): text and JSON output.

The formatters take attribute-shaped rows — the pure Finding/MergeItem
classes here, the PropertyGroup mirrors inside Blender — and the caller
supplies the timestamp, so every byte of output is deterministic under test.
"""

import json
from dataclasses import dataclass

from reblend.model.schema import ElementData, Placement
from reblend.project import merge, reporting
from reblend.project.validation import Finding


@dataclass
class MergeRow:
    """A panel-shaped sync row: a MergeItem plus the chosen resolution."""

    path: str
    status: str
    summary: str
    resolution: str = ""


FINDINGS = [
    Finding("error", "missing-art", "no RE Element produces this sheet",
            subject="Knob_65x65", panel="front"),
    Finding("warning", "orphan-element", "no node in device_2D.lua",
            subject="Old_Knob"),
    Finding("warning", "view-transform", "scene view transform is 'AgX'"),
]


def test_text_report_carries_header_counts_and_findings():
    text = reporting.format_findings(
        FINDINGS, project_root="/dev/mydevice", addon_version="0.1.0",
        timestamp="2026-07-28 12:00:00")
    assert "RE-Blend validation report" in text
    assert "generated: 2026-07-28 12:00:00" in text
    assert "project:   /dev/mydevice" in text
    assert "add-on:    0.1.0" in text
    assert "1 error(s), 2 warning(s)" in text
    assert "[ERROR] [front] Knob_65x65: missing-art — " in text
    assert "[WARNING] Old_Knob: orphan-element — " in text


def test_text_report_lists_errors_before_warnings():
    text = reporting.format_findings(
        [Finding("warning", "w", "later"), Finding("error", "e", "first")])
    assert text.index("[ERROR]") < text.index("[WARNING]")


def test_render_reports_say_so():
    # Validate and the render queue share one store; a saved file must name
    # its producer or two saved reports become indistinguishable.
    text = reporting.format_findings(FINDINGS, kind="render")
    assert "RE-Blend render report" in text
    doc = json.loads(reporting.findings_json(FINDINGS, kind="render"))
    assert doc["report"] == "render"


def test_empty_report_is_still_a_valid_file():
    text = reporting.format_findings(
        [], project_root="/dev/mydevice", timestamp="2026-07-28 12:00:00")
    assert "0 error(s), 0 warning(s)" in text
    assert "no findings — clean" in text


def test_findings_json_round_trips_every_field():
    doc = json.loads(reporting.findings_json(
        FINDINGS, project_root="/dev/mydevice", addon_version="0.1.0",
        timestamp="2026-07-28 12:00:00"))
    assert doc["report"] == "validation"
    assert doc["project"] == "/dev/mydevice"
    assert doc["errors"] == 1 and doc["warnings"] == 2
    assert doc["findings"][0] == {
        "severity": "error", "code": "missing-art",
        "message": "no RE Element produces this sheet",
        "subject": "Knob_65x65", "panel": "front",
    }


def test_merge_log_includes_status_summary_and_resolution():
    rows = [
        MergeRow("New_Lamp", merge.ADDED,
                 "in the Lua, not in the scene: lamp, 2 frame(s)"),
        MergeRow("Old_Knob", merge.REMOVED,
                 "no longer in Lua — kept until you delete it", "DELETE"),
    ]
    text = reporting.format_merge(
        rows, project_root="/dev/mydevice", timestamp="2026-07-28 12:00:00")
    assert "RE-Blend sync log" in text
    assert "1 added, 1 removed" in text
    assert ("[ADDED] New_Lamp — in the Lua, not in the scene: "
            "lamp, 2 frame(s)" in text)
    assert "[REMOVED] Old_Knob — " in text
    assert "->  delete" in text


def test_merge_log_accepts_pure_merge_items():
    # The pure MergeItem has no resolution attribute; the formatter must not
    # require the panel-row shape.
    element = ElementData(node="old", path="Old_Knob", kind="knob", frames=61,
                          placements=(Placement("front", "old", 5, 5),))
    items = [merge.MergeItem("Old_Knob", merge.REMOVED, element=element)]
    text = reporting.format_merge(items)
    assert "[REMOVED] Old_Knob" in text
    assert "->" not in text  # no resolution recorded, none invented


def test_merge_json_round_trips():
    rows = [MergeRow("Old_Knob", merge.REMOVED, "gone", "MINE")]
    doc = json.loads(reporting.merge_json(rows, timestamp="t"))
    assert doc["report"] == "sync"
    assert doc["items"] == [{"path": "Old_Knob", "status": "removed",
                             "summary": "gone", "resolution": "MINE"}]


def test_empty_merge_log_says_in_sync():
    assert "in sync" in reporting.format_merge([])
