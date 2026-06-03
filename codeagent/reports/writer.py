"""Report rendering and audit-log persistence."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from codeagent import filesystem as fs
from codeagent.reports.artifact_store import ArtifactKind, ArtifactRecord, ArtifactStore
from codeagent.reports.decision_trace import DecisionTraceWriter
from codeagent.reports.schemas import HumanDecision, StageResult, ToolCallRecord
from codeagent.reports.transcript import JsonlRecorder


class ReportReferenceError(ValueError):
    """Raised when a report would reference missing or unverifiable records."""


@dataclass(frozen=True)
class StageReportPaths:
    stage_result_path: Path
    stage_report_path: Path


class ReportWriter:
    def __init__(
        self,
        *,
        run_dir: Path,
        artifact_store: ArtifactStore,
        transcript: JsonlRecorder | None = None,
        decision_trace: JsonlRecorder | None = None,
        stage_dirs: Mapping[str, Path] | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.artifact_store = artifact_store
        self.stage_dirs = dict(stage_dirs or {})
        self.transcript = transcript or JsonlRecorder(run_dir / "transcript.jsonl")
        self.decision_trace = DecisionTraceWriter(
            decision_trace or JsonlRecorder(run_dir / "decision_trace.jsonl")
        )

    def write_stage_report(self, result: StageResult) -> StageReportPaths:
        self._validate_stage_result(result)
        stage_dirname = _stage_dir_name(result.stage)
        stage_dir = self._stage_dir(result.stage)
        fs.mkdir(stage_dir)
        stage_result_path = stage_dir / "stage_result.json"
        stage_report_path = stage_dir / "stage_report.md"
        result_with_report = result.model_copy(
            update={"report_path": _relative_to_run_dir(self.run_dir, stage_report_path)}
        )

        fs.write_text(
            stage_result_path,
            json.dumps(
                result_with_report.model_dump(mode="json", exclude_none=True),
                indent=2,
                ensure_ascii=False,
            ),
        )
        fs.write_text(stage_report_path, self._render_stage_report(result_with_report))
        self._record_artifact(
            ArtifactRecord(
                artifact_id=f"{stage_dirname}_stage_result",
                stage=stage_dirname,
                kind=ArtifactKind.JSON,
                path=stage_result_path,
                summary=f"{result.stage} stage result: {result.status}",
            )
        )
        self._record_artifact(
            ArtifactRecord(
                artifact_id=f"{stage_dirname}_stage_report",
                stage=stage_dirname,
                kind=ArtifactKind.REPORT,
                path=stage_report_path,
                summary=f"{result.stage} stage report",
            )
        )
        self.artifact_store.write()
        self.transcript.append(
            {
                "type": "stage_result",
                "stage": result.stage,
                "status": result.status,
                "summary": result.summary,
                "artifact_ids": result.artifact_ids,
                "report_path": _relative_to_run_dir(self.run_dir, stage_report_path),
            }
        )
        return StageReportPaths(
            stage_result_path=stage_result_path,
            stage_report_path=stage_report_path,
        )

    def write_final_report(self, stage_results: Iterable[StageResult]) -> Path:
        results = list(stage_results)
        for result in results:
            self._validate_stage_result(result)

        final_report_path = self.run_dir / "final_report.md"
        self._record_artifact(
            ArtifactRecord(
                artifact_id="final_report",
                stage="final",
                kind=ArtifactKind.REPORT,
                path=final_report_path,
                summary="Final run report",
            )
        )
        self.artifact_store.write()
        fs.write_text(final_report_path, self._render_final_report(results))
        self.transcript.append(
            {
                "type": "final_report",
                "path": "final_report.md",
                "stage_count": len(results),
            }
        )
        return final_report_path

    def record_human_decision(self, decision: HumanDecision) -> dict:
        return self.decision_trace.append_human_decision(decision)

    def record_route_decision(
        self,
        *,
        from_stage: str,
        to_stage: str,
        reason: str,
    ) -> dict:
        return self.decision_trace.append_route_decision(
            from_stage=from_stage,
            to_stage=to_stage,
            reason=reason,
        )

    def record_tool_call(self, record: ToolCallRecord) -> dict:
        payload = record.model_dump(mode="json", exclude_none=True)
        payload["type"] = "tool_call"
        return self.transcript.append(payload)

    def _validate_stage_result(self, result: StageResult) -> None:
        self._validate_artifact_ids(result.artifact_ids, context=result.stage)
        if result.error is not None:
            self._validate_artifact_ids(
                result.error.artifact_ids,
                context=f"{result.stage} error",
            )
        if result.status in {"failed", "cancelled"}:
            if result.error is None and not result.summary.strip():
                raise ReportReferenceError(
                    f"{result.stage} {result.status} report requires a failure reason"
                )
            if not result.next_suggestion.strip():
                raise ReportReferenceError(
                    f"{result.stage} {result.status} report requires a next suggestion"
                )

    def _validate_artifact_ids(self, artifact_ids: Iterable[str], *, context: str) -> None:
        missing = [
            artifact_id
            for artifact_id in artifact_ids
            if self.artifact_store.find(artifact_id) is None
        ]
        if missing:
            raise ReportReferenceError(
                f"{context} references unregistered artifacts: {', '.join(missing)}"
            )

    def _record_artifact(self, record: ArtifactRecord) -> ArtifactRecord:
        return self.artifact_store.record(record)

    def _stage_dir(self, stage: str) -> Path:
        stage_dirname = _stage_dir_name(stage)
        for key in _stage_lookup_keys(stage, stage_dirname):
            configured = self.stage_dirs.get(key)
            if configured is not None:
                return configured
        return self.run_dir / stage_dirname

    def _render_stage_report(self, result: StageResult) -> str:
        lines = [
            f"# {result.stage} Stage Report",
            "",
            "## Status",
            "",
            f"- Status: {result.status}",
            f"- Started: {result.started_at}",
            f"- Ended: {result.ended_at or ''}",
            f"- Summary: {result.summary}",
            "",
            "## Artifacts",
            "",
            "| artifact_id | path | kind | summary |",
            "|---|---|---|---|",
        ]
        for artifact_id in result.artifact_ids:
            artifact = self.artifact_store.find(artifact_id)
            if artifact is not None:
                lines.append(
                    f"| {_markdown_cell(artifact.artifact_id)} | "
                    f"{_markdown_cell(artifact.path)} | "
                    f"{_markdown_cell(artifact.kind)} | "
                    f"{_markdown_cell(artifact.summary)} |"
                )
        if result.error is not None:
            lines.extend(
                [
                    "",
                    "## Failure Reason",
                    "",
                    f"- Error ID: {result.error.error_id}",
                    f"- Category: {result.error.category}",
                    f"- Message: {result.error.message}",
                ]
            )
        if result.next_suggestion:
            lines.extend(
                [
                    "",
                    "## Next Suggestion",
                    "",
                    result.next_suggestion,
                ]
            )
        return "\n".join(lines) + "\n"

    def _render_final_report(self, stage_results: list[StageResult]) -> str:
        lines = [
            "# 智能体运行总结报告",
            "",
            "## 阶段结果总览",
            "",
            "| 阶段 | 状态 | 关键产物 | 说明 |",
            "|---|---|---|---|",
        ]
        for result in stage_results:
            artifact_summary = ", ".join(result.artifact_ids) or "-"
            lines.append(
                f"| {_markdown_cell(result.stage)} | {_markdown_cell(result.status)} | "
                f"{_markdown_cell(artifact_summary)} | {_markdown_cell(result.summary)} |"
            )

        failed_or_cancelled = [
            result
            for result in stage_results
            if result.status in {"failed", "cancelled"}
        ]
        if failed_or_cancelled:
            lines.extend(
                [
                    "",
                    "## 失败与取消详情",
                    "",
                    "| 阶段 | 错误 ID | 类别 | 原因 | 相关产物 | 下一步建议 |",
                    "|---|---|---|---|---|---|",
                ]
            )
            for result in failed_or_cancelled:
                error_id = result.error.error_id if result.error else "-"
                category = result.error.category if result.error else "-"
                message = result.error.message if result.error else result.summary
                error_artifacts = (
                    ", ".join(result.error.artifact_ids)
                    if result.error and result.error.artifact_ids
                    else ", ".join(result.artifact_ids)
                )
                lines.append(
                    f"| {_markdown_cell(result.stage)} | {_markdown_cell(error_id)} | "
                    f"{_markdown_cell(category)} | {_markdown_cell(message)} | "
                    f"{_markdown_cell(error_artifacts or '-')} | "
                    f"{_markdown_cell(result.next_suggestion)} |"
                )

        lines.extend(
            [
                "",
                "## 输出产物索引",
                "",
                "| artifact_id | stage | kind | path | summary |",
                "|---|---|---|---|---|",
            ]
        )
        for artifact in self.artifact_store.artifacts:
            lines.append(
                f"| {_markdown_cell(artifact.artifact_id)} | "
                f"{_markdown_cell(artifact.stage)} | {_markdown_cell(artifact.kind)} | "
                f"{_markdown_cell(artifact.path)} | {_markdown_cell(artifact.summary)} |"
            )
        lines.extend(
            [
                "",
                "## 审计记录",
                "",
                f"- decision_trace 事件数: {_jsonl_line_count(self.run_dir / 'decision_trace.jsonl')}",
                f"- transcript 事件数: {_jsonl_line_count(self.run_dir / 'transcript.jsonl')}",
            ]
        )
        return "\n".join(lines) + "\n"


def _stage_dir_name(stage: str) -> str:
    aliases = {
        "implement": "implementation",
        "implementation": "implementation",
        "test": "testing",
        "testing": "testing",
        "debug": "debugging",
        "debugging": "debugging",
        "repair": "repair",
    }
    return aliases.get(stage, stage)


def _stage_lookup_keys(stage: str, stage_dirname: str) -> list[str]:
    aliases = {
        "implementation": "implement",
        "testing": "test",
        "debugging": "debug",
        "repair": "repair",
    }
    keys = [stage, stage_dirname]
    canonical = aliases.get(stage_dirname)
    if canonical is not None:
        keys.append(canonical)
    deduped: list[str] = []
    for key in keys:
        if key not in deduped:
            deduped.append(key)
    return deduped


def _relative_to_run_dir(run_dir: Path, path: Path) -> str:
    run_text = _normal_absolute_path(run_dir)
    path_text = _normal_absolute_path(path)
    if os.path.commonpath([run_text, path_text]) != run_text:
        raise ValueError(f"path is outside run directory: {path}")
    return Path(os.path.relpath(path_text, run_text)).as_posix()


def _jsonl_line_count(path: Path) -> int:
    if not fs.exists(path):
        return 0
    return len([line for line in fs.read_text(path).splitlines() if line])


def _normal_absolute_path(path: Path) -> str:
    resolved = str(fs.portable_path(path).resolve())
    if os.name == "nt":
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = "\\\\" + resolved[len("\\\\?\\UNC\\") :]
        elif resolved.startswith("\\\\?\\"):
            resolved = resolved[len("\\\\?\\") :]
        resolved = os.path.normcase(resolved)
    return os.path.normpath(resolved)


def _markdown_cell(value: object) -> str:
    text = str(value)
    return text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")
