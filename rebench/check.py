"""Run every rule over a project and tag each finding with its rule's source."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from reblend.project.link import ProjectLink, load_project
from reblend.project.validation import validate_link

from .catalogue import rule_for
from .layout import check_layout
from .profile import Profile, load_profile

__all__ = ["BenchFinding", "CheckResult", "run_checks"]


@dataclass(frozen=True)
class BenchFinding:
    severity: str
    code: str
    message: str
    subject: str
    panel: str
    source: str
    rule: str
    ref: str

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        where = f" [{self.panel}]" if self.panel else ""
        who = f" {self.subject}:" if self.subject else ""
        return f"{self.severity.upper()} {self.code} ({self.source}){where}{who} {self.message}"


@dataclass
class CheckResult:
    project: Path
    findings: list[BenchFinding]

    @property
    def errors(self) -> list[BenchFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[BenchFinding]:
        return [f for f in self.findings if f.severity == "warning"]


def _tag(severity, code, message, subject, panel) -> BenchFinding:
    rule = rule_for(code)
    return BenchFinding(severity, code, message, subject, panel, rule.source, rule.title, rule.ref)


def run_checks(root: Path | str, profile: Profile | None = None,
               link: ProjectLink | None = None) -> CheckResult:
    """Check an RE project from its files alone (no Blender scene)."""
    root = Path(root)
    profile = profile or load_profile(root)
    link = link or load_project(root)

    findings: list[BenchFinding] = []
    report = validate_link(link, [spec.to_element_data() for spec in link.specs])
    for f in report.findings:
        if rule_for(f.code).scene_only:
            continue
        findings.append(_tag(f.severity, f.code, f.message, f.subject, f.panel))
    for f in check_layout(link, profile):
        findings.append(_tag(f.severity, f.code, f.message, f.subject, f.panel))

    order = {"error": 0, "warning": 1}
    findings.sort(key=lambda f: (order.get(f.severity, 2), f.code, f.panel, f.subject))
    return CheckResult(root, findings)
