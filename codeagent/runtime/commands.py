"""Command approval, policy, and result models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


DecisionType = Literal["approve", "reject"]


@dataclass(frozen=True)
class CommandApproval:
    operation_id: str
    approved: bool
    decision_type: DecisionType
    decided_by: str
    reason: str
    auto: bool = False

    @classmethod
    def approve(
        cls, *, operation_id: str, approved_by: str, reason: str
    ) -> CommandApproval:
        return cls(
            operation_id=operation_id,
            approved=True,
            decision_type="approve",
            decided_by=approved_by,
            reason=reason,
        )

    @classmethod
    def reject(
        cls, *, operation_id: str, rejected_by: str, reason: str
    ) -> CommandApproval:
        return cls(
            operation_id=operation_id,
            approved=False,
            decision_type="reject",
            decided_by=rejected_by,
            reason=reason,
        )

    @classmethod
    def benchmark_auto_approve(
        cls, *, operation_id: str, reason: str
    ) -> CommandApproval:
        return cls(
            operation_id=operation_id,
            approved=True,
            decision_type="approve",
            decided_by="benchmark",
            reason=reason,
            auto=True,
        )

    def to_record(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CommandPolicyDecision:
    allowed: bool
    reason: str
    argv: list[str]


@dataclass(frozen=True)
class ShellResult:
    operation_id: str
    command: str
    argv: list[str]
    cwd: Path
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_original_chars: int
    stderr_original_chars: int
    duration_seconds: float
    timed_out: bool
    stdout_log: Path
    stderr_log: Path
    record_path: Path


@dataclass(frozen=True)
class CommandOperationRecord:
    operation_id: str
    command: str
    argv: list[str]
    cwd: str
    timeout_seconds: float
    approval: dict
    policy: dict
    exit_code: int | None
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_original_chars: int
    stderr_original_chars: int
    duration_seconds: float
    stdout_log: str
    stderr_log: str

    def to_json_dict(self) -> dict:
        return asdict(self)
