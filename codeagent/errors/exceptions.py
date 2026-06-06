"""Structured errors used by workflow state and reports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ErrorCategory = Literal[
    "model",
    "tool",
    "shell",
    "validation",
    "user",
    "pytest_failure",
    "patch",
    "hitl",
    "checkpoint",
    "unknown",
]
ErrorSeverity = Literal["info", "warning", "error", "critical"]


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class ErrorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_id: str
    stage: str | None = None
    node: str | None = None
    category: ErrorCategory
    message: str = Field(min_length=1, max_length=4000)
    severity: ErrorSeverity = "error"
    retryable: bool = False
    artifact_ids: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=utc_timestamp)
    details_summary: str = Field(default="", max_length=4000)


class CodeAgentError(RuntimeError):
    """Base exception that can be safely persisted as an ErrorRecord."""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        node: str | None = None,
        category: ErrorCategory = "unknown",
        severity: ErrorSeverity = "error",
        retryable: bool = False,
        artifact_ids: list[str] | None = None,
        details_summary: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.stage = stage
        self.node = node
        self.category = category
        self.severity = severity
        self.retryable = retryable
        self.artifact_ids = artifact_ids or []
        self.details_summary = details_summary

    def to_record(self, *, error_id: str) -> ErrorRecord:
        return ErrorRecord(
            error_id=error_id,
            stage=self.stage,
            node=self.node,
            category=self.category,
            message=self.message,
            severity=self.severity,
            retryable=self.retryable,
            artifact_ids=self.artifact_ids,
            details_summary=self.details_summary,
        )
