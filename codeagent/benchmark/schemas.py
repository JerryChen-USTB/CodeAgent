"""Data structures for benchmark execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeagent.config.schema import BenchmarkConfig, TaskConfig


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    config_path: Path
    source_case_dir: Path
    enabled: bool = True
    difficulty: str | None = None
    project_type: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class LoadedBenchmark:
    config_path: Path
    config: BenchmarkConfig
    cases: list[BenchmarkCase]

    @property
    def enabled_cases(self) -> list[BenchmarkCase]:
        return [case for case in self.cases if case.enabled]

    @property
    def blocked_cases(self) -> list[BenchmarkCase]:
        return [case for case in self.cases if not case.enabled]


@dataclass(frozen=True)
class CaseExecutionContext:
    case_id: str
    source_case_dir: Path
    run_case_dir: Path
    copied_config_path: Path
    task_config: TaskConfig
    visible_paths: list[Path]
    hidden_paths: list[Path]
    source_snapshot_before: str | None = None
    oracle_logs_dir: Path | None = None
    oracle_command: str | None = None
    oracle_framework: str | None = None


@dataclass(frozen=True)
class CaseEvaluation:
    case_id: str
    success: bool
    score: float
    final_status: str
    run_dir: Path | None
    run_case_dir: Path
    failure_reason: str = ""
    oracle_success: bool | None = None
    oracle_command: str | None = None
    oracle_logs_dir: Path | None = None
    source_snapshot_before: str | None = None
    source_snapshot_after: str | None = None
    source_unchanged: bool | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "success": self.success,
            "score": self.score,
            "final_status": self.final_status,
            "run_dir": self.run_dir.as_posix() if self.run_dir else None,
            "run_case_dir": self.run_case_dir.as_posix(),
            "failure_reason": self.failure_reason,
            "oracle_success": self.oracle_success,
            "oracle_command": self.oracle_command,
            "oracle_logs_dir": (
                self.oracle_logs_dir.as_posix() if self.oracle_logs_dir else None
            ),
            "source_snapshot_before": self.source_snapshot_before,
            "source_snapshot_after": self.source_snapshot_after,
            "source_unchanged": self.source_unchanged,
        }


@dataclass(frozen=True)
class BenchmarkResult:
    benchmark_id: str
    benchmark_run_dir: Path
    total_cases: int
    success_cases: int
    failed_cases: int
    success_rate: float
    cases: list[CaseEvaluation]
    blocked_cases: int = 0
    blockers: list[CaseEvaluation] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "benchmark_run_dir": self.benchmark_run_dir.as_posix(),
            "total_cases": self.total_cases,
            "success_cases": self.success_cases,
            "failed_cases": self.failed_cases,
            "blocked_cases": self.blocked_cases,
            "success_rate": self.success_rate,
            "cases": [case.to_json_dict() for case in self.cases],
            "blockers": [case.to_json_dict() for case in self.blockers],
        }
