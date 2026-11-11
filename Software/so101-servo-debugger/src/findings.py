"""Common result type: every check returns findings, each with a fix."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    OK = 0
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4


@dataclass
class Finding:
    severity: Severity
    title: str
    detail: str = ""
    fix: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"[{self.severity.name:8}] {self.title}"]
        if self.detail:
            lines += [f"           {line}" for line in self.detail.splitlines()]
        for step in self.fix:
            lines.append(f"           fix: {step}")
        return "\n".join(lines)


def print_findings(findings: list[Finding]) -> None:
    for finding in findings:
        print(finding.render())


def worst(findings: list[Finding]) -> Severity:
    return max((f.severity for f in findings), default=Severity.OK)
