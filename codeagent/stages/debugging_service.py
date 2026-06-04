"""Debugging stage orchestration service."""

from __future__ import annotations

import json
import os
import re
import shlex
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from codeagent import filesystem as fs
from codeagent.config.schema import Stage
from codeagent.context.sensitive_filter import SensitiveFilter
from codeagent.errors import ErrorRecord
from codeagent.errors.exceptions import utc_timestamp
from codeagent.reports import ArtifactKind, ArtifactRecord
from codeagent.reports.schemas import HumanDecision, StageResult
from codeagent.reports.writer import ReportWriter
from codeagent.runtime.commands import CommandApproval, ShellResult
from codeagent.runtime.run_context import RunContext
from codeagent.tools.hitl import ApprovalDecision
from codeagent.tools.pytest_tools import parse_shell_result, parse_test_result
from codeagent.tools.shell_tools import CommandDeniedError, ShellRunner
from codeagent.workflow.progress_events import emit_progress


DEBUGGING_STAGE = "debugging"
REPRODUCTION_COMMAND_INTERRUPT_ID = "debugging_reproduction_command"


class FaultCandidate(BaseModel):
    """Candidate source location with evidence."""

    __test__: ClassVar[bool] = False
    model_config = ConfigDict(extra="forbid")

    file_path: Path
    function_name: str | None = Field(default=None, max_length=200)
    line_number: int | None = Field(default=None, ge=1)
    confidence: Literal["high", "medium", "low"]
    evidence: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=4000)


class FaultLocalization(BaseModel):
    """Persisted fault-localization payload."""

    __test__: ClassVar[bool] = False
    model_config = ConfigDict(extra="forbid")

    failing_tests: list[str] = Field(default_factory=list)
    candidates: list[FaultCandidate] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]
    reproduction_status: Literal["reproduced", "not_reproduced", "not_executed"]
    root_cause: str = Field(default="", max_length=8000)
    repair_plan: str = Field(default="", max_length=8000)


@dataclass(frozen=True)
class DebuggingRequest:
    __test__: ClassVar[bool] = False

    test_command: str | None = None
    command_approval: ApprovalDecision = field(
        default_factory=lambda: ApprovalDecision(
            interrupt_id=REPRODUCTION_COMMAND_INTERRUPT_ID,
            decision_type="reject",
            auto=True,
        )
    )
    failure_logs: list[Path] = field(default_factory=list)
    test_report_path: Path | None = None
    expected_behavior: str | None = None
    framework: Literal["pytest", "unittest"] = "pytest"
    command_timeout_seconds: float | None = None
    attempt_index: int | None = None


@dataclass(frozen=True)
class DebuggingApprovalPreview:
    payload: dict[str, object] | None = None
    result: StageResult | None = None


@dataclass(frozen=True)
class _EvidenceBundle:
    reproduction_status: Literal["reproduced", "not_reproduced", "not_executed"]
    command: str | None
    shell_result: ShellResult | None
    combined_text: str
    parsed_result: object | None
    static_log_paths: list[Path]
    command_executed: bool


@dataclass(frozen=True)
class _TestHarnessFailureDiagnosis:
    summary: str
    message: str
    root_cause: str
    repair_plan: str
    next_suggestion: str


class DebuggingService:
    """Run reproduction, failure summarization, localization, and reports."""

    __test__: ClassVar[bool] = False

    def __init__(
        self,
        *,
        run_context: RunContext,
        shell_runner: ShellRunner | None = None,
    ) -> None:
        self.run_context = run_context
        self.stage_dir = run_context.stage_dirs[Stage.DEBUG]
        self.shell_runner = shell_runner or ShellRunner(
            logs_dir=self.stage_dir / "logs",
            max_output_chars=run_context.task_config.runtime.log_truncation_chars,
        )
        self.writer = ReportWriter(
            run_dir=run_context.run_dir,
            artifact_store=run_context.artifact_store,
            transcript=run_context.transcript,
            decision_trace=run_context.decision_trace,
            stage_dirs=run_context.stage_dirs,
        )

    def prepare_reproduction_approval(
        self,
        request: DebuggingRequest,
    ) -> DebuggingApprovalPreview:
        fs.mkdir(self.stage_dir)
        if not request.test_command:
            return DebuggingApprovalPreview(result=self.run(request))
        return DebuggingApprovalPreview(
            payload={
                "interrupt_id": REPRODUCTION_COMMAND_INTERRUPT_ID,
                "action": "approve_reproduction_command",
                "title": "运行此调试复现命令？",
                "summary": request.test_command,
                "risk_level": "medium",
                "allowed_decisions": ["approve", "reject"],
                "default_decision": "approve",
                "payload": {
                    "command": request.test_command,
                    "framework": request.framework,
                    "cwd": self.run_context.task_config.project_path.as_posix(),
                },
            }
        )

    def run_after_approval(
        self,
        request: DebuggingRequest,
        *,
        command_approval: ApprovalDecision,
    ) -> StageResult:
        return self.run(replace(request, command_approval=command_approval))

    def run(self, request: DebuggingRequest) -> StageResult:
        started_at = utc_timestamp()
        fs.mkdir(self.stage_dir)
        artifacts: list[str] = []
        attempt_index = request.attempt_index or 1
        self.run_context.workflow_trace.record(
            "debugging_attempt_started",
            stage=DEBUGGING_STAGE,
            attempt=attempt_index,
            failure_logs=[path.as_posix() for path in request.failure_logs],
            test_report_path=(
                request.test_report_path.as_posix()
                if request.test_report_path is not None
                else None
            ),
            reproduction_command=bool(request.test_command),
            reproduction_decision=request.command_approval.decision_type,
        )
        emit_progress(
            "agent_status",
            stage=DEBUGGING_STAGE,
            message=_debugging_entry_message(request, attempt_index=attempt_index),
        )

        evidence = self._collect_evidence(request, started_at=started_at)
        if isinstance(evidence, StageResult):
            return self._finalize_result(evidence, artifact_ids=artifacts)
        emit_progress(
            "agent_status",
            stage=DEBUGGING_STAGE,
            message=_debugging_evidence_message(
                request,
                evidence,
                attempt_index=attempt_index,
            ),
        )

        reproduction_path = self._write_reproduction_report(evidence)
        artifacts.append(
            self._record_artifact(
                "debugging_reproduction_report",
                ArtifactKind.REPORT,
                reproduction_path,
                "Debugging reproduction report",
            )
        )
        if evidence.command_executed:
            before_log_path = self._write_before_test_log(evidence)
            artifacts.append(
                self._record_artifact(
                    "debugging_before_test_log",
                    ArtifactKind.LOG,
                    before_log_path,
                    "Reproduction command combined log",
                )
            )

        failure_summary = _build_failure_summary(evidence)
        failure_summary_path = self._write_failure_summary(failure_summary)
        artifacts.append(
            self._record_artifact(
                "debugging_failure_summary",
                ArtifactKind.REPORT,
                failure_summary_path,
                "Debugging failure summary",
            )
        )

        failing_tests = _extract_failing_tests(evidence)
        candidates = self._localize_faults(evidence.combined_text, failing_tests)
        test_harness_failure = _detect_generated_test_harness_failure(
            evidence=evidence,
            failing_tests=failing_tests,
        )
        if test_harness_failure is not None:
            candidates = []
            confidence: Literal["high", "medium", "low"] = "low"
            root_cause = test_harness_failure.root_cause
            repair_plan = test_harness_failure.repair_plan
        else:
            confidence = _overall_confidence(
                reproduction_status=evidence.reproduction_status,
                candidates=candidates,
                failing_tests=failing_tests,
            )
            root_cause = _build_root_cause(
                evidence=evidence,
                candidates=candidates,
                confidence=confidence,
            )
            repair_plan = _build_repair_plan(
                candidates=candidates,
                confidence=confidence,
                expected_behavior=request.expected_behavior,
            )
        localization = FaultLocalization(
            failing_tests=failing_tests,
            candidates=candidates,
            confidence=confidence,
            reproduction_status=evidence.reproduction_status,
            root_cause=root_cause,
            repair_plan=repair_plan,
        )

        fault_path = self._write_fault_localization(localization)
        root_cause_path = self._write_root_cause(root_cause)
        repair_plan_path = self._write_repair_plan(repair_plan)
        debug_trace_path = self._write_debug_trace(
            request=request,
            evidence=evidence,
            localization=localization,
        )
        debug_report_path = self._write_debug_report(
            evidence=evidence,
            failure_summary=failure_summary,
            localization=localization,
        )
        self.run_context.workflow_trace.record(
            "debugging_attempt_finished",
            stage=DEBUGGING_STAGE,
            attempt=attempt_index,
            reproduction_status=evidence.reproduction_status,
            confidence=confidence,
            top_suspect=(
                candidates[0].file_path.as_posix() if candidates else None
            ),
            debug_report_path=_run_relative_path(
                debug_report_path,
                run_dir=self.run_context.run_dir,
            ),
            failing_tests=failing_tests,
            test_harness_failure=test_harness_failure is not None,
        )
        if test_harness_failure is not None:
            self.run_context.workflow_trace.record(
                "debugging_test_harness_failure",
                stage=DEBUGGING_STAGE,
                attempt=attempt_index,
                failing_tests=failing_tests,
                message=test_harness_failure.message,
                debug_report_path=_run_relative_path(
                    debug_report_path,
                    run_dir=self.run_context.run_dir,
                ),
            )
            emit_progress(
                "agent_status",
                stage=DEBUGGING_STAGE,
                message=_debugging_harness_failure_message(
                    test_harness_failure,
                    debug_report_path=debug_report_path,
                    run_dir=self.run_context.run_dir,
                    attempt_index=attempt_index,
                ),
            )
        else:
            emit_progress(
                "agent_status",
                stage=DEBUGGING_STAGE,
                message=_debugging_finished_message(
                    localization,
                    debug_report_path=debug_report_path,
                    run_dir=self.run_context.run_dir,
                    attempt_index=attempt_index,
                ),
            )
        artifacts.extend(
            [
                self._record_artifact(
                    "debugging_fault_localization",
                    ArtifactKind.JSON,
                    fault_path,
                    "Fault localization candidates",
                ),
                self._record_artifact(
                    "debugging_root_cause",
                    ArtifactKind.REPORT,
                    root_cause_path,
                    "Root cause analysis",
                ),
                self._record_artifact(
                    "debugging_repair_plan",
                    ArtifactKind.REPORT,
                    repair_plan_path,
                    "Repair plan for repair stage",
                ),
                self._record_artifact(
                    "debugging_debug_trace",
                    ArtifactKind.LOG,
                    debug_trace_path,
                    "Debugging trace",
                ),
                self._record_artifact(
                    "debugging_debug_report",
                    ArtifactKind.REPORT,
                    debug_report_path,
                    "Debugging report",
                ),
            ]
        )
        if test_harness_failure is not None:
            result = self._failed_result(
                started_at=started_at,
                summary=test_harness_failure.summary,
                category="validation",
                message=test_harness_failure.message,
                artifact_ids=artifacts,
                next_suggestion=test_harness_failure.next_suggestion,
            )
            return self._finalize_result(result, artifact_ids=artifacts)
        summary = _result_summary(confidence=confidence, candidates=candidates)
        result = StageResult(
            stage=DEBUGGING_STAGE,
            status="succeeded",
            started_at=started_at,
            ended_at=utc_timestamp(),
            summary=summary,
            artifact_ids=artifacts,
            next_suggestion=(
                "Continue to repair using debugging/repair_plan.md and "
                "debugging/fault_localization.json."
            ),
        )
        return self._finalize_result(result, artifact_ids=artifacts)

    def _collect_evidence(
        self,
        request: DebuggingRequest,
        *,
        started_at: str,
    ) -> _EvidenceBundle | StageResult:
        static_logs = self._read_static_logs(request)
        if isinstance(static_logs, StageResult):
            return static_logs
        command = self._command_from_decision(request)
        if isinstance(command, StageResult):
            return command
        if command is None:
            return _EvidenceBundle(
                reproduction_status="not_executed",
                command=None,
                shell_result=None,
                combined_text="\n".join(static_logs),
                parsed_result=(
                    parse_test_result(
                        framework=request.framework,
                        stdout="\n".join(static_logs),
                        stderr="",
                        exit_code=1 if static_logs else None,
                    )
                    if static_logs
                    else None
                ),
                static_log_paths=list(request.failure_logs),
                command_executed=False,
            )
        try:
            shell = self._run_command(command, request)
        except RuntimeError as exc:
            return self._failed_result(
                started_at=started_at,
                summary="Debugging reproduction command could not start.",
                category="shell",
                message=str(exc),
                artifact_ids=[],
                next_suggestion=(
                    "Approve an allowed pytest or unittest command, or provide "
                    "failure logs for static analysis."
                ),
            )
        parsed = parse_shell_result(framework=request.framework, shell_result=shell)
        combined = "\n".join(
            value
            for value in [shell.stdout, shell.stderr, "\n".join(static_logs)]
            if value.strip()
        )
        reproduced = bool(
            parsed.failed or parsed.errors or shell.exit_code not in (0, None)
        )
        return _EvidenceBundle(
            reproduction_status="reproduced" if reproduced else "not_reproduced",
            command=command,
            shell_result=shell,
            combined_text=combined,
            parsed_result=parsed,
            static_log_paths=list(request.failure_logs),
            command_executed=True,
        )

    def _command_from_decision(
        self,
        request: DebuggingRequest,
    ) -> str | StageResult | None:
        if not request.test_command:
            return None
        approval = request.command_approval
        if approval.interrupt_id != REPRODUCTION_COMMAND_INTERRUPT_ID:
            return self._failed_result(
                started_at=utc_timestamp(),
                summary="Debugging reproduction approval did not match the expected interrupt.",
                category="hitl",
                message="approval decision interrupt_id does not match debugging reproduction command",
                artifact_ids=[],
                next_suggestion="Resume with a decision for the debugging reproduction command.",
            )
        self._record_decision(approval)
        if approval.decision_type == "approve":
            hidden_error = _hidden_command_path_error(request.test_command)
            if hidden_error:
                return self._failed_result(
                    started_at=utc_timestamp(),
                    summary="Debugging command targets hidden benchmark material.",
                    category="validation",
                    message=hidden_error,
                    artifact_ids=[],
                    next_suggestion="Use a visible pytest/unittest command.",
                )
            return request.test_command
        if approval.decision_type == "edit":
            edited = approval.edited_payload or {}
            edited_command = edited.get("command")
            if isinstance(edited_command, str) and edited_command.strip():
                hidden_error = _hidden_command_path_error(edited_command)
                if hidden_error:
                    return self._failed_result(
                        started_at=utc_timestamp(),
                        summary="Debugging command targets hidden benchmark material.",
                        category="validation",
                        message=hidden_error,
                        artifact_ids=[],
                        next_suggestion="Use a visible pytest/unittest command.",
                    )
                return edited_command
            return self._failed_result(
                started_at=utc_timestamp(),
                summary="Debugging command edit did not include a command.",
                category="hitl",
                message="edited_payload.command is required for command edit decisions",
                artifact_ids=[],
                next_suggestion="Resume with an edited command or reject reproduction.",
            )
        if approval.decision_type == "cancel":
            return StageResult(
                stage=DEBUGGING_STAGE,
                status="cancelled",
                started_at=utc_timestamp(),
                ended_at=utc_timestamp(),
                summary="Debugging reproduction was cancelled.",
                error=ErrorRecord(
                    error_id="debugging_cancelled",
                    stage=DEBUGGING_STAGE,
                    node="approve_reproduction_command",
                    category="hitl",
                    message="debugging reproduction command was cancelled",
                    retryable=False,
                ),
                next_suggestion="Run debugging again with logs or an approved reproduction command.",
            )
        return None

    def _read_static_logs(self, request: DebuggingRequest) -> list[str] | StageResult:
        contents: list[str] = []
        for path in request.failure_logs:
            allowed = _safe_debug_input_path(
                path,
                project_root=self.run_context.task_config.project_path,
                run_dir=self.run_context.run_dir,
            )
            if allowed is None:
                return self._failed_result(
                    started_at=utc_timestamp(),
                    summary="Debugging failure log path is not allowed.",
                    category="validation",
                    message=f"failure log path is denied: {path}",
                    artifact_ids=[],
                    next_suggestion="Provide a visible non-secret failure log path.",
            )
            try:
                contents.append(fs.read_text(allowed))
            except OSError as exc:
                return self._failed_result(
                    started_at=utc_timestamp(),
                    summary="Debugging failure log could not be read.",
                    category="io",
                    message=str(exc),
                    artifact_ids=[],
                    next_suggestion="Provide an existing readable failure log.",
                )
        if request.test_report_path is not None:
            allowed_report = _safe_debug_input_path(
                request.test_report_path,
                project_root=self.run_context.task_config.project_path,
                run_dir=self.run_context.run_dir,
            )
            if allowed_report is None:
                return self._failed_result(
                    started_at=utc_timestamp(),
                    summary="Debugging test report path is not allowed.",
                    category="validation",
                    message=f"test report path is denied: {request.test_report_path}",
                    artifact_ids=[],
                    next_suggestion="Provide a visible non-secret test report path.",
            )
            try:
                contents.append(fs.read_text(allowed_report))
            except OSError as exc:
                return self._failed_result(
                    started_at=utc_timestamp(),
                    summary="Debugging test report could not be read.",
                    category="io",
                    message=str(exc),
                    artifact_ids=[],
                    next_suggestion="Provide an existing readable test report.",
                )
        if not contents and not request.test_command:
            return self._failed_result(
                started_at=utc_timestamp(),
                summary="Debugging requires a reproduction command or failure evidence.",
                category="validation",
                message="missing test_command, failure_logs, and test_report_path",
                artifact_ids=[],
                next_suggestion="Provide a test command, failure log, or test report.",
            )
        return contents

    def _localize_faults(
        self,
        text: str,
        failing_tests: list[str],
    ) -> list[FaultCandidate]:
        root = self.run_context.task_config.project_path.resolve()
        trace_candidates = _candidates_from_traceback(text, root)
        search_candidates = self._candidates_from_source_search(text, failing_tests)
        merged: dict[str, dict[str, object]] = {}
        for candidate in [*trace_candidates, *search_candidates]:
            key = candidate.file_path.as_posix()
            existing = merged.setdefault(
                key,
                {
                    "file_path": candidate.file_path,
                    "function_name": candidate.function_name,
                    "line_number": candidate.line_number,
                    "confidence": candidate.confidence,
                    "evidence": [],
                    "rationale": candidate.rationale,
                },
            )
            existing_evidence = existing["evidence"]
            if isinstance(existing_evidence, list):
                for item in candidate.evidence:
                    if item not in existing_evidence:
                        existing_evidence.append(item)
            if existing.get("line_number") is None and candidate.line_number is not None:
                existing["line_number"] = candidate.line_number
            if existing.get("function_name") is None and candidate.function_name:
                existing["function_name"] = candidate.function_name
        candidates = [
            FaultCandidate.model_validate(item)
            for item in merged.values()
            if isinstance(item.get("evidence"), list) and item["evidence"]
        ]
        return sorted(candidates, key=_candidate_sort_key)[:5]

    def _candidates_from_source_search(
        self,
        text: str,
        failing_tests: list[str],
    ) -> list[FaultCandidate]:
        tokens = _interesting_tokens(text, failing_tests)
        if not tokens:
            return []
        root = self.run_context.task_config.project_path.resolve()
        sensitive_filter = SensitiveFilter(root)
        candidates: list[FaultCandidate] = []
        for path in _iter_project_python_files(root, sensitive_filter):
            try:
                content = fs.read_text(path)
            except OSError:
                continue
            matched = [
                token
                for token in tokens
                if re.search(rf"\b{re.escape(token)}\b", content)
            ]
            if not matched:
                continue
            relative = path.relative_to(root)
            if _is_test_path(relative.as_posix()):
                continue
            candidates.append(
                FaultCandidate(
                    file_path=Path(relative.as_posix()),
                    function_name=_first_function_name(content),
                    line_number=_line_for_first_token(content, matched[0]),
                    confidence="medium",
                    evidence=[f"source search matched token `{matched[0]}`"],
                    rationale="Project source contains identifiers from the failure evidence.",
                )
            )
        return candidates

    def _run_command(self, command: str, request: DebuggingRequest) -> ShellResult:
        timeout = (
            request.command_timeout_seconds
            or self.run_context.task_config.test_command.timeout_seconds
        )
        approval = CommandApproval.approve(
            operation_id="debugging_reproduce",
            approved_by="workflow",
            reason="Run approved debugging reproduction command.",
        )
        emit_progress(
            "tool_started",
            stage=DEBUGGING_STAGE,
            tool_name="run_shell",
            message=f"正在执行调试复现命令：{command}",
        )
        try:
            result = self.shell_runner.run(
                command,
                cwd=self.run_context.task_config.project_path,
                timeout_seconds=timeout,
                approval=approval,
            )
        except (CommandDeniedError, ValueError) as exc:
            raise RuntimeError(f"debugging command failed before execution: {exc}") from exc
        emit_progress(
            "tool_finished",
            stage=DEBUGGING_STAGE,
            tool_name="run_shell",
            status="succeeded" if result.exit_code in (0, None) else "failed",
            message=f"复现命令退出码：{result.exit_code}",
        )
        return result

    def _record_decision(self, approval: ApprovalDecision) -> None:
        self.writer.record_human_decision(
            HumanDecision(
                interrupt_id=approval.interrupt_id,
                action="approve_reproduction_command",
                decision_type=approval.decision_type,
                edited_payload=approval.edited_payload,
                comment=approval.comment,
                timestamp=approval.decided_at,
                auto=approval.auto,
                decision_source=approval.decision_source,
                presented_to_user=approval.presented_to_user,
                decided_by=approval.decided_by,
            )
        )
        self.run_context.workflow_trace.record(
            "approval_decision",
            stage=DEBUGGING_STAGE,
            action="approve_reproduction_command",
            interrupt_id=approval.interrupt_id,
            decision_type=approval.decision_type,
            auto=approval.auto,
            decision_source=approval.decision_source,
            presented_to_user=approval.presented_to_user,
            decided_by=approval.decided_by,
            comment=approval.comment,
        )

    def _write_reproduction_report(self, evidence: _EvidenceBundle) -> Path:
        path = self.stage_dir / "reproduction_report.md"
        lines = [
            "# Reproduction Report",
            "",
            f"- Status: {evidence.reproduction_status}",
            f"- Command: `{evidence.command or ''}`",
            f"- Executed: {evidence.command_executed}",
        ]
        if not evidence.command_executed:
            lines.append(
                "- Note: reproduction command not executed; static evidence was used."
            )
        fs.write_text(path, "\n".join(lines) + "\n")
        return path

    def _write_before_test_log(self, evidence: _EvidenceBundle) -> Path:
        path = self.stage_dir / "before_test.log"
        shell = evidence.shell_result
        lines = [
            f"Command: {evidence.command or ''}",
            f"Exit code: {shell.exit_code if shell else ''}",
            "",
            "## stdout",
            shell.stdout if shell else "",
            "",
            "## stderr",
            shell.stderr if shell else "",
        ]
        fs.write_text(path, "\n".join(lines))
        return path

    def _write_failure_summary(self, summary: str) -> Path:
        path = self.stage_dir / "failure_summary.md"
        fs.write_text(path, summary)
        return path

    def _write_fault_localization(self, localization: FaultLocalization) -> Path:
        path = self.stage_dir / "fault_localization.json"
        fs.write_text(
            path,
            json.dumps(localization.model_dump(mode="json"), indent=2, ensure_ascii=False),
        )
        return path

    def _write_root_cause(self, root_cause: str) -> Path:
        path = self.stage_dir / "root_cause.md"
        fs.write_text(path, f"# Root Cause\n\n{root_cause}\n")
        return path

    def _write_repair_plan(self, repair_plan: str) -> Path:
        path = self.stage_dir / "repair_plan.md"
        fs.write_text(path, f"# Repair Plan\n\n{repair_plan}\n")
        return path

    def _write_debug_trace(
        self,
        *,
        request: DebuggingRequest,
        evidence: _EvidenceBundle,
        localization: FaultLocalization,
    ) -> Path:
        path = self.stage_dir / "debug_trace.jsonl"
        events = [
            {
                "type": "reproduction",
                "status": evidence.reproduction_status,
                "command_executed": evidence.command_executed,
                "command": evidence.command,
            },
            {
                "type": "static_logs",
                "count": len(request.failure_logs),
                "paths": [item.as_posix() for item in request.failure_logs],
            },
            {
                "type": "fault_localization",
                "candidate_count": len(localization.candidates),
                "confidence": localization.confidence,
            },
        ]
        fs.write_text(
            path,
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        )
        return path

    def _write_debug_report(
        self,
        *,
        evidence: _EvidenceBundle,
        failure_summary: str,
        localization: FaultLocalization,
    ) -> Path:
        path = self.stage_dir / "debug_report.md"
        lines = [
            "# Debug Report",
            "",
            "## Reproduction",
            "",
            f"- Status: {evidence.reproduction_status}",
            f"- Command: `{evidence.command or ''}`",
            "",
            "## Failure Summary",
            "",
            failure_summary.strip(),
            "",
            "## Fault Localization",
            "",
            f"- Confidence: {localization.confidence}",
        ]
        if localization.candidates:
            for index, candidate in enumerate(localization.candidates, start=1):
                lines.append(
                    f"- {index}. `{candidate.file_path.as_posix()}` "
                    f"({candidate.confidence}): {candidate.rationale}"
                )
        else:
            lines.append("- No concrete source candidate found.")
        lines.extend(
            [
                "",
                "## Root Cause",
                "",
                localization.root_cause,
                "",
                "## Repair Plan",
                "",
                localization.repair_plan,
            ]
        )
        fs.write_text(path, "\n".join(lines) + "\n")
        return path

    def _failed_result(
        self,
        *,
        started_at: str,
        summary: str,
        category: str,
        message: str,
        artifact_ids: list[str],
        next_suggestion: str,
    ) -> StageResult:
        return StageResult(
            stage=DEBUGGING_STAGE,
            status="failed",
            started_at=started_at,
            ended_at=utc_timestamp(),
            summary=summary,
            artifact_ids=artifact_ids,
            error=ErrorRecord(
                error_id=f"debugging_{category}",
                stage=DEBUGGING_STAGE,
                node=DEBUGGING_STAGE,
                category=category,  # type: ignore[arg-type]
                message=message or summary,
                artifact_ids=artifact_ids,
                retryable=True,
            ),
            next_suggestion=next_suggestion,
        )

    def _finalize_result(
        self,
        result: StageResult,
        *,
        artifact_ids: list[str],
    ) -> StageResult:
        error = result.error
        if error is not None:
            error = error.model_copy(update={"artifact_ids": artifact_ids})
        finalized = result.model_copy(
            update={
                "artifact_ids": artifact_ids or result.artifact_ids,
                "error": error,
                "ended_at": result.ended_at or utc_timestamp(),
            }
        )
        self.writer.write_stage_report(finalized)
        return finalized

    def _record_artifact(
        self,
        artifact_id: str,
        kind: ArtifactKind,
        path: Path,
        summary: str,
    ) -> str:
        self.run_context.artifact_store.record(
            ArtifactRecord(
                artifact_id=artifact_id,
                stage=DEBUGGING_STAGE,
                kind=kind,
                path=path,
                summary=summary,
            )
        )
        self.run_context.artifact_store.write()
        return artifact_id


def _build_failure_summary(evidence: _EvidenceBundle) -> str:
    parsed = evidence.parsed_result
    lines = ["# Failure Summary", ""]
    if parsed is not None and hasattr(parsed, "to_json_dict"):
        data = parsed.to_json_dict()
        lines.extend(
            [
                f"- Success: {data.get('success')}",
                f"- Passed: {data.get('passed')}",
                f"- Failed: {data.get('failed')}",
                f"- Errors: {data.get('errors')}",
                f"- Timed out: {data.get('timed_out')}",
            ]
        )
        failing = data.get("failing_tests") or []
        if failing:
            lines.extend(["", "## Failing Tests", ""])
            for item in failing:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('nodeid')}: {item.get('message') or ''}")
        if data.get("error_summary"):
            lines.extend(["", "## Error Summary", "", str(data["error_summary"])])
    elif evidence.combined_text.strip():
        lines.extend(["Static log evidence was used.", "", evidence.combined_text[:4000]])
    else:
        lines.append("No failure output was available.")
    return "\n".join(lines).rstrip() + "\n"


def _debugging_entry_message(
    request: DebuggingRequest,
    *,
    attempt_index: int,
) -> str:
    evidence_parts: list[str] = []
    if request.failure_logs:
        evidence_parts.append(f"失败日志 {len(request.failure_logs)} 个")
    if request.test_report_path is not None:
        evidence_parts.append(
            f"测试报告 {_safe_path_name(request.test_report_path)}"
        )
    if not evidence_parts:
        evidence_parts.append("无现成失败日志")
    reproduction = (
        "将按审批结果运行复现命令"
        if request.test_command
        else "不重跑命令，使用已有证据静态分析"
    )
    return f"第 {attempt_index} 次调试：读取{'、'.join(evidence_parts)}；{reproduction}。"


def _debugging_evidence_message(
    request: DebuggingRequest,
    evidence: _EvidenceBundle,
    *,
    attempt_index: int,
) -> str:
    counts = _parsed_counts(evidence)
    failing_tests = _extract_failing_tests(evidence)
    names = "、".join(failing_tests[:3]) if failing_tests else "未解析到具体用例名"
    if len(failing_tests) > 3:
        names += f" 等 {len(failing_tests)} 个"
    source = _evidence_source_summary(request, evidence)
    action = "已运行复现命令" if evidence.command_executed else "未重跑命令，使用已有失败证据"
    return (
        f"第 {attempt_index} 次调试证据：{counts['failed']} failed, "
        f"{counts['errors']} errors；失败用例：{names}；来源：{source}；{action}。"
    )


def _debugging_finished_message(
    localization: FaultLocalization,
    *,
    debug_report_path: Path,
    run_dir: Path,
    attempt_index: int,
) -> str:
    top_suspect = (
        localization.candidates[0].file_path.as_posix()
        if localization.candidates
        else "未定位到具体文件"
    )
    report = _run_relative_path(debug_report_path, run_dir=run_dir)
    return (
        f"第 {attempt_index} 次调试完成：复现状态 {localization.reproduction_status}；"
        f"置信度 {localization.confidence}；首要嫌疑 {top_suspect}；报告 {report}。"
    )


def _debugging_harness_failure_message(
    diagnosis: _TestHarnessFailureDiagnosis,
    *,
    debug_report_path: Path,
    run_dir: Path,
    attempt_index: int,
) -> str:
    report = _run_relative_path(debug_report_path, run_dir=run_dir)
    return (
        f"第 {attempt_index} 次调试发现自测脚手架问题：失败来自生成测试的运行目录，"
        f"不进入修复阶段；报告 {report}。{diagnosis.next_suggestion}"
    )


def _detect_generated_test_harness_failure(
    *,
    evidence: _EvidenceBundle,
    failing_tests: list[str],
) -> _TestHarnessFailureDiagnosis | None:
    text = evidence.combined_text
    lowered = text.lower()
    if not (
        "notadirectoryerror" in lowered
        or "winerror 267" in lowered
        or "no such file or directory" in lowered
    ):
        return None
    if "subprocess" not in lowered or "cwd" not in lowered:
        return None
    test_frame_runs_subprocess = _test_frame_runs_subprocess_with_cwd(text)
    if not test_frame_runs_subprocess:
        return None
    if failing_tests:
        test_failures = [
            nodeid for nodeid in failing_tests if _is_test_nodeid(nodeid)
        ]
        if len(test_failures) / len(failing_tests) < 0.6:
            return None

    cwd_hint = _extract_cwd_hint(text)
    cwd_detail = f" Detected cwd: `{cwd_hint}`." if cwd_hint else ""
    message = (
        "Generated self-test harness appears invalid: pytest reaches a generated "
        "test helper that calls subprocess with a non-existent cwd before product "
        f"code can run.{cwd_detail}"
    )
    root_cause = (
        "The failure evidence points to the generated testing harness, not to the "
        "implementation. The subprocess call fails while preparing the child "
        "process working directory, so the product CLI is never executed."
        f"{cwd_detail}"
    )
    repair_plan = (
        "Do not repair product code from this evidence. Regenerate the testing "
        "stage patch so subprocess tests run from the real configured project root "
        "and avoid hard-coded project/workspace cwd suffixes."
    )
    return _TestHarnessFailureDiagnosis(
        summary="Debugging stopped because the generated self-test harness is invalid.",
        message=message,
        root_cause=root_cause,
        repair_plan=repair_plan,
        next_suggestion=(
            "请重新生成测试补丁，让 CLI/subprocess 测试从真实项目根目录运行。"
        ),
    )


def _test_frame_runs_subprocess_with_cwd(text: str) -> bool:
    frame_pattern = re.compile(
        r'File "([^"]*(?:tests[/\\]test_[^/\\"]+\.py|test_[^/\\"]+\.py))", '
        r"line \d+, in [^\n]+"
    )
    for match in frame_pattern.finditer(text):
        frame_body = text[match.end() : match.end() + 600].lower()
        if "subprocess" in frame_body and "cwd" in frame_body:
            return True
    return False


def _is_test_nodeid(nodeid: str) -> bool:
    file_part = nodeid.split("::", 1)[0]
    return _is_test_path(file_part)


def _extract_cwd_hint(text: str) -> str | None:
    patterns = [
        r"cwd\s*=\s*['\"]([^'\"]+)['\"]",
        r"cwd\s*=\s*str\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _parsed_counts(evidence: _EvidenceBundle) -> dict[str, int]:
    parsed = evidence.parsed_result
    if parsed is not None and hasattr(parsed, "to_json_dict"):
        data = parsed.to_json_dict()
        return {
            "passed": _int_value(data.get("passed")),
            "failed": _int_value(data.get("failed")),
            "errors": _int_value(data.get("errors")),
            "skipped": _int_value(data.get("skipped")),
            "total": _int_value(data.get("total")),
        }
    return {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "total": 0}


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _evidence_source_summary(
    request: DebuggingRequest,
    evidence: _EvidenceBundle,
) -> str:
    parts: list[str] = []
    if request.test_report_path is not None:
        parts.append(_safe_path_name(request.test_report_path))
    parts.extend(_safe_path_name(path) for path in evidence.static_log_paths[:2])
    if evidence.command_executed:
        parts.append("复现命令输出")
    if not parts:
        return "运行上下文"
    return "、".join(parts)


def _safe_path_name(path: Path) -> str:
    return path.name or path.as_posix()


def _run_relative_path(path: Path, *, run_dir: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _extract_failing_tests(evidence: _EvidenceBundle) -> list[str]:
    parsed = evidence.parsed_result
    tests: list[str] = []
    if parsed is not None and hasattr(parsed, "failing_tests"):
        for failure in getattr(parsed, "failing_tests", ()):
            nodeid = getattr(failure, "nodeid", "")
            if nodeid and nodeid not in tests:
                tests.append(nodeid)
    for match in re.finditer(r"FAILED\s+([^\s]+)", evidence.combined_text):
        nodeid = match.group(1).strip()
        if nodeid and nodeid not in tests:
            tests.append(nodeid)
    for match in re.finditer(r"_{3,}\s+([A-Za-z_][\w]*)\s+_{3,}", evidence.combined_text):
        name = match.group(1)
        if name not in tests:
            tests.append(name)
    return tests


def _candidates_from_traceback(text: str, root: Path) -> list[FaultCandidate]:
    candidates: list[FaultCandidate] = []
    pattern = re.compile(r'File "([^"]+)", line (\d+), in ([A-Za-z_][\w.]*)')
    for match in pattern.finditer(text):
        raw_path = match.group(1)
        line_number = int(match.group(2))
        function_name = match.group(3)
        normalized = _normalize_candidate_path(raw_path, root)
        if normalized is None:
            continue
        confidence: Literal["high", "medium", "low"] = (
            "medium" if _is_test_path(normalized.as_posix()) else "high"
        )
        candidates.append(
            FaultCandidate(
                file_path=normalized,
                function_name=function_name,
                line_number=line_number,
                confidence=confidence,
                evidence=[f"traceback references {normalized.as_posix()}:{line_number}"],
                rationale="The Python traceback reached this project file during failure.",
            )
        )
    return candidates


def _normalize_candidate_path(raw_path: str, root: Path) -> Path | None:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(root)
        except (OSError, ValueError):
            return None
        if SensitiveFilter(root).is_denied(resolved):
            return None
        if _is_hidden_benchmark_path(relative.as_posix()):
            return None
        return Path(relative.as_posix())
    normalized = Path(raw_path.replace("\\", "/"))
    if any(part == ".." for part in normalized.parts):
        return None
    target = (root / normalized).resolve()
    try:
        relative = target.relative_to(root)
    except ValueError:
        return None
    if not fs.exists(target):
        return None
    if _is_hidden_benchmark_path(relative.as_posix()):
        return None
    return Path(relative.as_posix())


def _overall_confidence(
    *,
    reproduction_status: str,
    candidates: list[FaultCandidate],
    failing_tests: list[str],
) -> Literal["high", "medium", "low"]:
    if not candidates:
        return "low"
    top = candidates[0]
    if (
        reproduction_status == "reproduced"
        and failing_tests
        and top.confidence == "high"
        and not _is_test_path(top.file_path.as_posix())
    ):
        return "high"
    if top.confidence in {"high", "medium"}:
        return "medium"
    return "low"


def _build_root_cause(
    *,
    evidence: _EvidenceBundle,
    candidates: list[FaultCandidate],
    confidence: str,
) -> str:
    if not candidates:
        return (
            "No concrete project source location could be tied to the failure evidence. "
            f"Confidence is {confidence}; collect a reproducible traceback before repair."
        )
    top = candidates[0]
    reproduction = (
        "The failure was reproduced by the approved command."
        if evidence.reproduction_status == "reproduced"
        else "The analysis used static failure evidence without a fresh reproduction."
    )
    return (
        f"{reproduction} The strongest suspect is `{top.file_path.as_posix()}`"
        f"{':' + str(top.line_number) if top.line_number else ''}"
        f"{' in `' + top.function_name + '`' if top.function_name else ''}. "
        f"Evidence: {'; '.join(top.evidence)}. "
        f"Confidence: {confidence}."
    )


def _build_repair_plan(
    *,
    candidates: list[FaultCandidate],
    confidence: str,
    expected_behavior: str | None,
) -> str:
    if not candidates:
        return (
            "Do not patch blindly. Add or rerun a focused failing test, capture a traceback, "
            "then update the smallest implementation file supported by that evidence."
        )
    top = candidates[0]
    expected = f" Expected behavior: {expected_behavior}" if expected_behavior else ""
    return (
        f"Inspect `{top.file_path.as_posix()}`"
        f"{' around line ' + str(top.line_number) if top.line_number else ''}. "
        "Modify implementation code only, keep tests intact, and rerun the failing test. "
        f"Treat this as a {confidence}-confidence repair input.{expected}"
    )


def _result_summary(
    *,
    confidence: str,
    candidates: list[FaultCandidate],
) -> str:
    if not candidates:
        return "Debugging completed with low confidence and no concrete source candidate."
    top = candidates[0]
    return (
        f"Debugging completed with {confidence} confidence; top suspect "
        f"`{top.file_path.as_posix()}`."
    )


def _candidate_sort_key(candidate: FaultCandidate) -> tuple[int, int, str]:
    confidence_rank = {"high": 0, "medium": 1, "low": 2}[candidate.confidence]
    test_rank = 1 if _is_test_path(candidate.file_path.as_posix()) else 0
    return (test_rank, confidence_rank, candidate.file_path.as_posix())


def _interesting_tokens(text: str, failing_tests: list[str]) -> list[str]:
    tokens: dict[str, int] = defaultdict(int)
    for source in [text, " ".join(failing_tests)]:
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", source):
            lowered = token.lower()
            if lowered in _STOPWORDS or lowered.startswith("test_"):
                continue
            tokens[token] += 1
    return [
        token
        for token, _count in sorted(tokens.items(), key=lambda item: (-item[1], item[0]))
    ][:12]


def _iter_project_python_files(root: Path, sensitive_filter: SensitiveFilter):
    for current_root, dirnames, filenames in os.walk(root):
        current = Path(current_root)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not sensitive_filter.is_denied(current / dirname)
            and not _is_hidden_benchmark_path((current / dirname).relative_to(root).as_posix())
        ]
        for filename in filenames:
            path = current / filename
            if path.suffix != ".py":
                continue
            if sensitive_filter.is_denied(path):
                continue
            if _is_hidden_benchmark_path(path.relative_to(root).as_posix()):
                continue
            yield path


def _first_function_name(content: str) -> str | None:
    match = re.search(r"^def\s+([A-Za-z_][\w]*)\s*\(", content, flags=re.MULTILINE)
    return match.group(1) if match else None


def _line_for_first_token(content: str, token: str) -> int | None:
    for index, line in enumerate(content.splitlines(), start=1):
        if re.search(rf"\b{re.escape(token)}\b", line):
            return index
    return None


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or Path(path).name.startswith("test_")
    )


def _safe_debug_input_path(
    path: Path,
    *,
    project_root: Path,
    run_dir: Path,
) -> Path | None:
    if any(part == ".." for part in path.parts):
        return None
    resolved = path.resolve()
    resolved_project_root = project_root.resolve()
    resolved_run_dir = run_dir.resolve()
    run_relative = _relative_to_or_none(resolved, resolved_run_dir)
    project_relative = _relative_to_or_none(resolved, resolved_project_root)
    if run_relative is not None:
        relative = run_relative
        apply_project_filter = False
    elif project_relative is not None:
        relative = project_relative
        apply_project_filter = True
    else:
        return None
    if _is_hidden_benchmark_path(relative.as_posix()) or _is_hidden_benchmark_path(
        resolved.as_posix()
    ):
        return None
    if _has_secret_like_part(relative.parts):
        return None
    if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".crt", ".cer"}:
        return None
    if apply_project_filter and SensitiveFilter(resolved_project_root).is_denied(resolved):
        return None
    return resolved


def _relative_to_or_none(path: Path, root: Path) -> Path | None:
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _has_secret_like_part(parts: tuple[str, ...]) -> bool:
    for part in parts:
        lowered = part.lower()
        if lowered == "software engineering project.txt":
            return True
        if lowered.startswith(".env"):
            return True
        if any(secret in lowered for secret in ("secret", "token", "credential")):
            return True
    return False


def _hidden_command_path_error(command: str) -> str | None:
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return f"command could not be parsed: {exc}"
    for arg in argv:
        values = [arg]
        if "=" in arg:
            values.append(arg.split("=", 1)[1])
        for value in values:
            if _is_hidden_benchmark_path(value):
                return f"command references hidden benchmark path: {value}"
    return None


def _is_hidden_benchmark_path(path: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(path.replace("\\", "/")).parts]
    return (
        "evaluation" in parts
        or "oracle_tests" in parts
        or any(part == "expected_result.json" for part in parts)
    )


_STOPWORDS = {
    "assert",
    "assertionerror",
    "call",
    "error",
    "expected",
    "failed",
    "file",
    "from",
    "line",
    "most",
    "none",
    "recent",
    "return",
    "test",
    "tests",
    "traceback",
    "true",
    "false",
}
