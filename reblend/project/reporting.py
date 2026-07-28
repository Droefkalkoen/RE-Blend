"""Save-able validation and sync reports (§6.3, §6.1).

The panels show findings and merge items transiently; this module renders the
same rows into files a designer can keep — a plain-text log for reading and a
JSON document for diffing in review (the shape the M3 render manifest will
join). Pure on purpose: the Blender layer hands in attribute-shaped rows (the
``PropertyGroup`` mirrors satisfy the same attribute contract as
:class:`~reblend.project.validation.Finding` and
:class:`~reblend.project.merge.MergeItem`), and the caller supplies the
timestamp so output is deterministic under test.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

__all__ = [
    "format_findings",
    "findings_json",
    "format_merge",
    "merge_json",
]


def _header(title: str, project_root: str, addon_version: str,
            timestamp: str) -> list[str]:
    lines = [f"RE-Blend {title}", "=" * len(f"RE-Blend {title}")]
    if timestamp:
        lines.append(f"generated: {timestamp}")
    if project_root:
        lines.append(f"project:   {project_root}")
    if addon_version:
        lines.append(f"add-on:    {addon_version}")
    lines.append("")
    return lines


def _finding_line(finding: Any) -> str:
    where = f" [{finding.panel}]" if finding.panel else ""
    who = f" {finding.subject}:" if finding.subject else ""
    return f"[{finding.severity.upper()}]{where}{who} {finding.code} — {finding.message}"


def format_findings(findings: Iterable[Any], *, kind: str = "validation",
                    project_root: str = "", addon_version: str = "",
                    timestamp: str = "") -> str:
    """The findings list as a readable text report.

    ``findings`` rows need ``severity``/``code``/``message``/``subject``/
    ``panel`` attributes; ``kind`` names what produced them ("validation" or
    "render"), because both write the same store and a saved file must say
    which one it is.
    """
    rows = list(findings)
    errors = [f for f in rows if f.severity == "error"]
    title = "render report" if kind == "render" else "validation report"
    lines = _header(title, project_root, addon_version, timestamp)
    lines.append(f"{len(errors)} error(s), {len(rows) - len(errors)} warning(s)")
    lines.append("")
    if not rows:
        lines.append("no findings — clean")
    # Errors first: the file mirrors the panel's priority, not scan order.
    for finding in sorted(rows, key=lambda f: f.severity != "error"):
        lines.append(_finding_line(finding))
    return "\n".join(lines) + "\n"


def findings_json(findings: Iterable[Any], *, kind: str = "validation",
                  project_root: str = "", addon_version: str = "",
                  timestamp: str = "") -> str:
    rows = list(findings)
    doc = {
        "report": "render" if kind == "render" else "validation",
        "generated": timestamp,
        "project": project_root,
        "addon_version": addon_version,
        "errors": sum(1 for f in rows if f.severity == "error"),
        "warnings": sum(1 for f in rows if f.severity != "error"),
        "findings": [
            {
                "severity": f.severity,
                "code": f.code,
                "message": f.message,
                "subject": f.subject,
                "panel": f.panel,
            }
            for f in rows
        ],
    }
    return json.dumps(doc, indent=2) + "\n"


def _merge_line(item: Any) -> str:
    resolution = str(getattr(item, "resolution", "") or "")
    chosen = f"  ->  {resolution.lower()}" if resolution else ""
    return f"[{item.status.upper()}] {item.path} — {item.summary}{chosen}"


def format_merge(items: Iterable[Any], *, project_root: str = "",
                 addon_version: str = "", timestamp: str = "") -> str:
    """The sync diff as a readable text log.

    ``items`` rows need ``path``/``status``/``summary`` attributes; a
    ``resolution`` attribute (the panel rows carry one, the pure
    :class:`~reblend.project.merge.MergeItem` does not) is included when
    present, so a saved log records the choices as well as the diff.
    """
    rows = list(items)
    lines = _header("sync log", project_root, addon_version, timestamp)
    if not rows:
        lines.append("scene and project are in sync — no differences")
        return "\n".join(lines) + "\n"
    counts: dict[str, int] = {}
    for item in rows:
        counts[item.status] = counts.get(item.status, 0) + 1
    lines.append(", ".join(f"{count} {status}" for status, count in counts.items()))
    lines.append("")
    for item in rows:
        lines.append(_merge_line(item))
    return "\n".join(lines) + "\n"


def merge_json(items: Iterable[Any], *, project_root: str = "",
               addon_version: str = "", timestamp: str = "") -> str:
    rows = list(items)
    doc = {
        "report": "sync",
        "generated": timestamp,
        "project": project_root,
        "addon_version": addon_version,
        "items": [
            {
                "path": item.path,
                "status": item.status,
                "summary": item.summary,
                "resolution": str(getattr(item, "resolution", "") or ""),
            }
            for item in rows
        ],
    }
    return json.dumps(doc, indent=2) + "\n"
