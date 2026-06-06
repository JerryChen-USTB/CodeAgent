"""Pydantic schemas for persisted workflow reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from codeagent.errors.exceptions import ErrorRecord


StageStatus = Literal["pending", "running", "succeeded", "failed", "skipped", "cancelled"]
ToolCallStatus = Literal["succeeded", "failed", "denied", "blocked", "skipped"]
DecisionType = Literal["approve", "edit", "reject", "respond", "cancel"]
ChangeType = Literal["added", "modified", "deleted"]
Confidence = Literal["high", "medium", "low"]


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StageResult(ReportModel):
    stage: str
    status: StageStatus
    started_at: str
    ended_at: str | None = None
    summary: str = Field(default="", max_length=8000)
    artifact_ids: list[str] = Field(default_factory=list)
    report_path: str | Path | None = None
    error: ErrorRecord | None = None
    next_suggestion: str = Field(default="", max_length=4000)

    @field_validator("report_path")
    @classmethod
    def normalize_report_path(cls, value: str | Path | None) -> str | None:
        return _normalize_optional_path(value)


class ToolCallRecord(ReportModel):
    call_id: str
    tool_name: str
    args_summary: dict[str, Any]
    result_summary: str = Field(default="", max_length=4000)
    status: ToolCallStatus
    artifact_ids: list[str] = Field(default_factory=list)
    timestamp: str


class HumanDecision(ReportModel):
    interrupt_id: str
    action: str
    decision_type: DecisionType
    edited_payload: dict[str, Any] | None = None
    comment: str | None = Field(default=None, max_length=4000)
    timestamp: str
    auto: bool = False
    event_type: str = "approval_decision"
    decision_source: str | None = None
    presented_to_user: bool | None = None
    decided_by: str | None = None


class CodeChange(ReportModel):
    file_path: str | Path
    change_type: ChangeType
    diff_artifact_id: str | None = None
    approved: bool

    @field_validator("file_path")
    @classmethod
    def normalize_file_path(cls, value: str | Path) -> str:
        return _normalize_path(value)


class TestResultRecord(ReportModel):
    __test__: ClassVar[bool] = False

    command: str
    exit_code: int | None = None
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(default=0, ge=0)
    log_paths: list[str | Path] = Field(default_factory=list)
    success: bool | None = None
    error_summary: str = Field(default="", max_length=4000)

    @field_validator("log_paths")
    @classmethod
    def normalize_log_paths(cls, value: list[str | Path]) -> list[str]:
        return [_normalize_path(item) for item in value]

    @model_validator(mode="after")
    def infer_success(self) -> "TestResultRecord":
        if self.success is None:
            self.success = (
                self.exit_code == 0 and self.failed == 0 and self.errors == 0
            )
        return self


class DebugResult(ReportModel):
    failing_tests: list[str] = Field(default_factory=list)
    suspect_files: list[str | Path] = Field(default_factory=list)
    root_cause: str = Field(default="", max_length=8000)
    confidence: Confidence

    @field_validator("suspect_files")
    @classmethod
    def normalize_suspect_files(cls, value: list[str | Path]) -> list[str]:
        return [_normalize_path(item) for item in value]


class RepairResult(ReportModel):
    patch_path: str | Path | None = None
    changed_files: list[str | Path] = Field(default_factory=list)
    before_result: TestResultRecord | None = None
    after_result: TestResultRecord | None = None
    success: bool

    @field_validator("patch_path")
    @classmethod
    def normalize_patch_path(cls, value: str | Path | None) -> str | None:
        return _normalize_optional_path(value)

    @field_validator("changed_files")
    @classmethod
    def normalize_changed_files(cls, value: list[str | Path]) -> list[str]:
        return [_normalize_path(item) for item in value]


def _normalize_optional_path(value: str | Path | None) -> str | None:
    if value is None:
        return None
    return _normalize_path(value)


def _normalize_path(value: str | Path) -> str:
    return Path(value).as_posix()
