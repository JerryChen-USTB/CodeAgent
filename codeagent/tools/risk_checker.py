"""Repair patch risk checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from codeagent.services.patch_service import PatchRiskFinding, PatchValidationResult


RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class RepairRiskFinding:
    kind: str
    path: str
    message: str
    severity: RiskLevel

    def to_json_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RepairRiskReport:
    level: RiskLevel = "low"
    findings: list[RepairRiskFinding] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.level != "high"

    def to_json_dict(self) -> dict:
        return {
            "level": self.level,
            "allowed": self.allowed,
            "findings": [finding.to_json_dict() for finding in self.findings],
        }


class RepairRiskChecker:
    """Fail closed on suspicious repair patches."""

    def assess(self, validation: PatchValidationResult) -> RepairRiskReport:
        findings: list[RepairRiskFinding] = []
        for finding in validation.risk_report.findings:
            findings.append(_from_patch_finding(finding))
        for path in validation.changed_files:
            if _is_test_path(path):
                findings.append(
                    RepairRiskFinding(
                        kind="test_modification",
                        path=path,
                        message="repair patch modifies test infrastructure or a test path",
                        severity="high",
                    )
                )
        level: RiskLevel = "low"
        if any(finding.severity == "high" for finding in findings):
            level = "high"
        elif findings:
            level = "medium"
        return RepairRiskReport(level=level, findings=findings)


def _from_patch_finding(finding: PatchRiskFinding) -> RepairRiskFinding:
    return RepairRiskFinding(
        kind=finding.kind,
        path=finding.path,
        message=finding.message,
        severity=finding.severity,
    )


def _is_test_path(path: str | Path) -> bool:
    posix = PurePosixPath(str(path).replace("\\", "/"))
    name = posix.name.lower()
    return (
        "tests" in posix.parts
        or name.startswith("test_")
        or name in {"conftest.py", "pytest.ini", "tox.ini", "noxfile.py"}
        or name in {"setup.cfg", "pyproject.toml"}
    )
