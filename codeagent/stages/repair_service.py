"""Repair stage orchestration service."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
from codeagent.services.patch_service import (
    FileChange,
    PatchApplyError,
    PatchService,
    PatchSummary,
    PatchValidationError,
    PatchValidationResult,
)
from codeagent.tools.hitl import ApprovalDecision
from codeagent.tools.pytest_tools import parse_shell_result
from codeagent.tools.risk_checker import RepairRiskChecker, RepairRiskReport
from codeagent.tools.shell_tools import CommandDeniedError, ShellRunner
from codeagent.workflow.progress_events import emit_progress


REPAIR_STAGE = "repair"
REPAIR_PLAN_INTERRUPT_ID = "repair_plan"
REPAIR_PATCH_INTERRUPT_ID = "repair_patch"
REPAIR_COMMAND_INTERRUPT_ID = "repair_regression_command"


class RepairFileChange(BaseModel):
    """Pure repair planning item without file contents."""

    __test__: ClassVar[bool] = False
    model_config = ConfigDict(extra="forbid")

    path: Path
    change_type: Literal["add", "modify", "delete"] = "modify"
    rationale: str = Field(min_length=1, max_length=4000)
    expected_effect: str = Field(min_length=1, max_length=4000)


class RepairPatchFileChange(BaseModel):
    """Concrete repair file content generated only after repair plan approval."""

    __test__: ClassVar[bool] = False
    model_config = ConfigDict(extra="forbid")

    path: Path
    old_content: str | None = None
    new_content: str | None = None
    rationale: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def require_real_change(self) -> "RepairPatchFileChange":
        if self.old_content is None and self.new_content is None:
            raise ValueError("repair file change must include old_content or new_content")
        return self


class RepairPlan(BaseModel):
    """Validated pure repair plan reviewed before repair code generation."""

    __test__: ClassVar[bool] = False
    model_config = ConfigDict(extra="forbid")

    root_cause: str = Field(min_length=1, max_length=8000)
    strategy: str = Field(min_length=1, max_length=8000)
    changes: list[RepairFileChange] = Field(min_length=1)
    verification_command: str = Field(min_length=1, max_length=1000)
    framework: Literal["pytest", "unittest"] = "pytest"
    failure_origin: Literal[
        "product_code",
        "generated_test_code",
        "mixed",
        "test_harness",
        "inconclusive",
    ] = "product_code"
    test_repair_allowed: bool = False
    test_repair_rationale: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_test_repair_permission(self) -> "RepairPlan":
        if self.test_repair_allowed and self.failure_origin not in {
            "generated_test_code",
            "mixed",
            "test_harness",
        }:
            raise ValueError(
                "test_repair_allowed requires failure_origin generated_test_code, "
                "mixed, or test_harness"
            )
        if self.test_repair_allowed and not (self.test_repair_rationale or "").strip():
            raise ValueError("test_repair_rationale is required when test repair is allowed")
        return self


class RepairPatchDraft(BaseModel):
    """Concrete repair patch draft generated after repair plan approval."""

    __test__: ClassVar[bool] = False
    model_config = ConfigDict(extra="forbid")

    plan_summary: str = Field(min_length=1, max_length=8000)
    changes: list[RepairPatchFileChange] = Field(min_length=1)
    verification_command: str = Field(min_length=1, max_length=1000)
    framework: Literal["pytest", "unittest"] = "pytest"


@dataclass(frozen=True)
class RepairRequest:
    __test__: ClassVar[bool] = False

    plan: RepairPlan
    patch_approval: ApprovalDecision
    command_approval: ApprovalDecision
    plan_review: ApprovalDecision | None = None
    patch_draft: RepairPatchDraft | None = None
    alternate_plans: list[RepairPlan] = field(default_factory=list)
    alternate_patch_drafts: list[RepairPatchDraft] = field(default_factory=list)
    max_patch_attempts: int = 3
    command_timeout_seconds: float | None = None


@dataclass(frozen=True)
class _PreparedRepairPatch:
    plan: RepairPlan
    draft: RepairPatchDraft
    validation: PatchValidationResult
    summary: PatchSummary
    risk: RepairRiskReport
    patch_text: str


@dataclass(frozen=True)
class RepairApprovalPreview:
    payload: dict[str, object] | None = None
    result: StageResult | None = None


class RepairService:
    """Run repair planning, patch approval/application, verification, and reports."""

    __test__: ClassVar[bool] = False

    def __init__(
        self,
        *,
        run_context: RunContext,
        patch_service: PatchService | None = None,
        shell_runner: ShellRunner | None = None,
        risk_checker: RepairRiskChecker | None = None,
    ) -> None:
        self.run_context = run_context
        self.patch_service = patch_service or PatchService()
        self.risk_checker = risk_checker or RepairRiskChecker()
        self.stage_dir = run_context.stage_dirs[Stage.REPAIR]
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

    def prepare_plan_review(self, request: RepairRequest) -> RepairApprovalPreview:
        fs.mkdir(self.stage_dir)
        artifacts = self._write_plan_artifacts(request.plan)
        return RepairApprovalPreview(
            payload={
                "interrupt_id": REPAIR_PLAN_INTERRUPT_ID,
                "action": "review_repair_plan",
                "title": "实施此修复计划？",
                "summary": request.plan.strategy,
                "risk_level": "medium",
                "allowed_decisions": ["approve", "respond"],
                "default_decision": "approve",
                "payload": {
                    "plan_path": "repair/repair_plan.md",
                    "plan_json_path": "repair/repair_plan.json",
                    "root_cause": request.plan.root_cause,
                    "failure_origin": request.plan.failure_origin,
                    "test_repair_allowed": request.plan.test_repair_allowed,
                    "changed_files": [
                        change.path.as_posix() for change in request.plan.changes
                    ],
                    "verification_command": request.plan.verification_command,
                    "artifact_ids": artifacts,
                },
            }
        )

    def run(self, request: RepairRequest) -> StageResult:
        started_at = utc_timestamp()
        plan_review = request.plan_review or ApprovalDecision(
            interrupt_id=REPAIR_PLAN_INTERRUPT_ID,
            decision_type="approve",
            comment="Approve repair plan for non-interactive run.",
            auto=True,
            decision_source="auto_default",
            presented_to_user=False,
        )
        edited = self._request_from_patch_edit(request, started_at=started_at)
        if isinstance(edited, StageResult):
            return self._finalize_result(edited, artifact_ids=[])
        if edited is not None:
            return self.run(edited)
        preview = self.prepare_patch_approval(request, plan_review=plan_review)
        if preview.result is not None:
            return preview.result
        patch_path = self.stage_dir / "repair.patch.diff"
        approved_hash = (
            _sha256_text(fs.read_text(patch_path))
            if fs.exists(patch_path)
            else None
        )
        command_preview = self.apply_patch_and_prepare_command(
            request,
            patch_approval=request.patch_approval,
            approved_patch_sha256=approved_hash,
        )
        if command_preview.result is not None:
            return command_preview.result
        return self.run_prepared_command(
            request,
            command_approval=request.command_approval,
        )

    def prepare_patch_approval(
        self,
        request: RepairRequest,
        *,
        plan_review: ApprovalDecision | None = None,
        record_plan_review: bool = True,
    ) -> RepairApprovalPreview:
        started_at = utc_timestamp()
        fs.mkdir(self.stage_dir)
        review = plan_review or request.plan_review
        if review is None:
            artifacts = self._write_plan_artifacts(request.plan)
            result = self._failed_result(
                started_at=started_at,
                summary="Repair plan approval is missing.",
                category="hitl",
                message="repair plan must be approved before patch generation",
                artifact_ids=artifacts,
                next_suggestion="Approve the repair plan or provide feedback to regenerate it.",
            )
            return RepairApprovalPreview(
                result=self._finalize_result(result, artifact_ids=artifacts)
            )
        plan_decision = self._handle_plan_review(
            review,
            started_at,
            record_decision=record_plan_review,
        )
        if plan_decision is not None:
            return RepairApprovalPreview(
                result=self._finalize_result(plan_decision, artifact_ids=[])
            )
        edited = self._request_from_patch_edit(request, started_at=started_at)
        if isinstance(edited, StageResult):
            return RepairApprovalPreview(
                result=self._finalize_result(edited, artifact_ids=[])
            )
        if edited is not None:
            return self.prepare_patch_approval(
                edited,
                plan_review=review,
                record_plan_review=record_plan_review,
            )

        if request.patch_draft is None:
            artifacts = self._write_plan_artifacts(request.plan)
            result = self._failed_result(
                started_at=started_at,
                summary="Repair patch draft is missing.",
                category="model",
                message="repair patch generation must run after repair plan approval",
                artifact_ids=artifacts,
                next_suggestion="Generate a RepairPatchDraft from the approved repair plan.",
            )
            return RepairApprovalPreview(
                result=self._finalize_result(result, artifact_ids=artifacts)
            )

        candidates = [request.patch_draft, *request.alternate_patch_drafts][
            : max(1, request.max_patch_attempts)
        ]
        prepared, attempts = self._prepare_patch_candidates(request.plan, candidates)
        plan = prepared.plan if prepared is not None else request.plan
        draft = prepared.draft if prepared is not None else candidates[0]
        artifacts = self._write_plan_artifacts(plan)
        draft_json_path = self._write_patch_draft_json(draft)
        artifacts.append(
            self._record_artifact(
                "repair_patch_draft_json",
                ArtifactKind.JSON,
                draft_json_path,
                "Structured repair patch draft",
            )
        )
        attempts_path = self._write_attempts(attempts)
        artifacts.append(
            self._record_artifact(
                "repair_patch_attempts",
                ArtifactKind.JSON,
                attempts_path,
                "Repair patch validation attempts",
            )
        )
        if prepared is None:
            result = self._failed_result(
                started_at=started_at,
                summary="Repair patch validation failed before approval.",
                category="patch",
                message=_last_attempt_error(attempts),
                artifact_ids=artifacts,
                next_suggestion="Revise the approved repair plan or regenerate the repair patch draft.",
            )
            return RepairApprovalPreview(
                result=self._finalize_result(result, artifact_ids=artifacts)
            )

        patch_path = self.stage_dir / "repair.patch.diff"
        fs.write_text(patch_path, prepared.patch_text)
        risk_path = self._write_risk_report(prepared.risk)
        artifacts.extend(
            [
                self._record_artifact(
                    "repair_patch",
                    ArtifactKind.PATCH,
                    patch_path,
                    "Repair patch diff",
                ),
                self._record_artifact(
                    "repair_risk",
                    ArtifactKind.JSON,
                    risk_path,
                    "Repair patch risk report",
                ),
            ]
        )
        if not prepared.risk.allowed:
            result = self._failed_result(
                started_at=started_at,
                summary="Repair patch is too risky to approve.",
                category="patch",
                message=_risk_message(prepared.risk),
                artifact_ids=artifacts,
                next_suggestion="Regenerate a repair patch that modifies implementation code only.",
            )
            return RepairApprovalPreview(
                result=self._finalize_result(result, artifact_ids=artifacts)
            )
        return RepairApprovalPreview(
            payload={
                "interrupt_id": REPAIR_PATCH_INTERRUPT_ID,
                "action": "approve_repair_patch",
                "title": "应用此修复补丁？",
                "summary": "在修改项目文件前审查生成的修复补丁。",
                "risk_level": prepared.risk.level,
                "allowed_decisions": ["approve", "respond"],
                "default_decision": "approve",
                "payload": {
                    "patch_path": "repair/repair.patch.diff",
                    "patch_draft_json_path": "repair/repair_patch_draft.json",
                    "changed_files": prepared.validation.changed_files,
                    "added_lines": prepared.summary.added_lines,
                    "removed_lines": prepared.summary.removed_lines,
                    "risk": prepared.risk.to_json_dict(),
                    "patch_sha256": _sha256_text(prepared.patch_text),
                    "artifact_ids": artifacts,
                },
            }
        )

    def apply_patch_and_prepare_command(
        self,
        request: RepairRequest,
        *,
        patch_approval: ApprovalDecision,
        approved_patch_sha256: str | None = None,
        ) -> RepairApprovalPreview:
        started_at = utc_timestamp()
        plan = self._load_prepared_plan(request.plan)
        draft = self._load_prepared_patch_draft(request.patch_draft)
        artifacts = self._register_existing_artifacts(plan, draft)
        edited = self._request_from_patch_edit(
            replace(request, patch_approval=patch_approval),
            started_at=started_at,
        )
        if isinstance(edited, StageResult):
            return RepairApprovalPreview(
                result=self._finalize_result(edited, artifact_ids=artifacts)
            )
        if edited is not None:
            return self.prepare_patch_approval(
                edited,
                plan_review=request.plan_review,
            )
        patch_decision = self._handle_patch_approval(patch_approval, started_at)
        if patch_decision is not None:
            patch_decision = patch_decision.model_copy(update={"artifact_ids": artifacts})
            return RepairApprovalPreview(
                result=self._finalize_result(patch_decision, artifact_ids=artifacts)
        )
        patch_path = self.stage_dir / "repair.patch.diff"
        if not fs.exists(patch_path):
            result = self._failed_result(
                started_at=started_at,
                summary="Approved repair patch is missing.",
                category="patch",
                message="repair/repair.patch.diff was not found at resume time",
                artifact_ids=artifacts,
                next_suggestion="Regenerate and approve the repair patch again.",
            )
            return RepairApprovalPreview(
                result=self._finalize_result(result, artifact_ids=artifacts)
            )
        patch_text = fs.read_text(patch_path)
        if approved_patch_sha256 and _sha256_text(patch_text) != approved_patch_sha256:
            result = self._failed_result(
                started_at=started_at,
                summary="Approved repair patch changed before application.",
                category="patch",
                message="approved repair patch hash mismatch",
                artifact_ids=artifacts,
                next_suggestion="Review and approve the current repair patch before applying it.",
            )
            return RepairApprovalPreview(
                result=self._finalize_result(result, artifact_ids=artifacts)
            )
        try:
            validation = self.patch_service.validate_patch(
                patch_path,
                self.run_context.task_config.project_path,
            )
            if not validation.valid:
                raise PatchValidationError("; ".join(validation.errors))
            risk = self.risk_checker.assess(
                validation,
                allow_test_modification=_repair_allows_test_modification(plan),
            )
            if not risk.allowed:
                raise PatchValidationError(_risk_message(risk))
            applied = self.patch_service.apply_patch(
                patch_path,
                self.run_context.task_config.project_path,
                operation_id="repair_apply_patch",
            )
        except (PatchApplyError, PatchValidationError) as exc:
            result = self._failed_result(
                started_at=started_at,
                summary="Repair patch could not be applied.",
                category="patch",
                message=str(exc),
                artifact_ids=artifacts,
                next_suggestion="Regenerate the repair patch against the current project files.",
            )
            return RepairApprovalPreview(
                result=self._finalize_result(result, artifact_ids=artifacts)
            )
        changed_files_path = self._write_changed_files(applied.changed_files)
        artifacts.append(
            self._record_artifact(
                "repair_changed_files",
                ArtifactKind.JSON,
                changed_files_path,
                "Repair changed files",
            )
        )
        return RepairApprovalPreview(
            payload={
                "interrupt_id": REPAIR_COMMAND_INTERRUPT_ID,
                "action": "approve_regression_command",
                "title": "运行此回归验证命令？",
                "summary": (
                    draft.verification_command if draft is not None else plan.verification_command
                ),
                "risk_level": "medium",
                "allowed_decisions": ["approve", "edit", "reject", "cancel"],
                "default_decision": "approve",
                "payload": {
                    "command": (
                        draft.verification_command
                        if draft is not None
                        else plan.verification_command
                    ),
                    "framework": draft.framework if draft is not None else plan.framework,
                    "changed_files": applied.changed_files,
                    "artifact_ids": artifacts,
                },
            }
        )

    def run_prepared_command(
        self,
        request: RepairRequest,
        *,
        command_approval: ApprovalDecision,
        ) -> StageResult:
        started_at = utc_timestamp()
        plan = self._load_prepared_plan(request.plan)
        draft = self._load_prepared_patch_draft(request.patch_draft)
        artifacts = self._register_existing_artifacts(plan, draft)
        changed_files = _read_changed_files(self.stage_dir / "changed_files.json")
        planned_command = (
            draft.verification_command if draft is not None else plan.verification_command
        )
        framework = draft.framework if draft is not None else plan.framework
        command = self._command_from_decision(planned_command, command_approval)
        if isinstance(command, StageResult):
            command = command.model_copy(update={"artifact_ids": artifacts})
            return self._finalize_result(command, artifact_ids=artifacts)
        try:
            emit_progress(
                "tool_started",
                stage=REPAIR_STAGE,
                tool_name="run_shell",
                message=f"正在执行修复回归验证命令：{command}",
            )
            shell = self._run_command(command, request)
        except RuntimeError as exc:
            result = self._failed_result(
                started_at=started_at,
                summary="Repair verification command was denied or could not start.",
                category="shell",
                message=str(exc),
                artifact_ids=artifacts,
                next_suggestion="Approve an allowed pytest or unittest command.",
            )
            return self._finalize_result(result, artifact_ids=artifacts)
        parsed = parse_shell_result(framework=framework, shell_result=shell)
        emit_progress(
            "tool_finished",
            stage=REPAIR_STAGE,
            tool_name="run_shell",
            status="succeeded" if shell.exit_code in (0, None) else "failed",
            message=f"回归验证命令退出码：{shell.exit_code}",
        )
        emit_progress(
            "test_result",
            stage=REPAIR_STAGE,
            passed=parsed.passed,
            failed=parsed.failed,
            errors=parsed.errors,
            skipped=parsed.skipped,
            total=parsed.total,
        )
        after_log_path = self._write_after_test_log(shell)
        result_path = self._write_test_result(parsed.to_json_dict())
        artifacts.extend(
            [
                self._record_artifact(
                    "repair_after_test_log",
                    ArtifactKind.LOG,
                    after_log_path,
                    "Repair verification combined log",
                ),
                self._record_artifact(
                    "repair_test_result",
                    ArtifactKind.JSON,
                    result_path,
                    "Repair verification parsed result",
                ),
            ]
        )
        report_path = self._write_repair_report(
            plan=plan,
            patch_draft=draft,
            command=command,
            changed_files=changed_files,
            test_result=parsed.to_json_dict(),
            success=parsed.success,
        )
        artifacts.append(
            self._record_artifact(
                "repair_report",
                ArtifactKind.REPORT,
                report_path,
                "Repair report",
            )
        )
        if parsed.success:
            emit_progress(
                "agent_status",
                stage=REPAIR_STAGE,
                message=(
                    f"修复验证通过：{parsed.passed} 个测试通过；报告 "
                    f"{_run_relative_path(report_path, run_dir=self.run_context.run_dir)}。"
                ),
            )
            result = StageResult(
                stage=REPAIR_STAGE,
                status="succeeded",
                started_at=started_at,
                ended_at=utc_timestamp(),
                summary="Repair verification passed.",
                artifact_ids=artifacts,
                next_suggestion="Continue to final reporting.",
            )
        else:
            emit_progress(
                "agent_status",
                stage=REPAIR_STAGE,
                message=(
                    "修复验证仍失败："
                    f"{parsed.failed} failed, {parsed.errors} errors；"
                    f"结果 {_run_relative_path(result_path, run_dir=self.run_context.run_dir)}；"
                    f"报告 {_run_relative_path(report_path, run_dir=self.run_context.run_dir)}。"
                ),
            )
            result = self._failed_result(
                started_at=started_at,
                summary="Repair verification failed.",
                category="pytest_failure",
                message=parsed.error_summary or "Testing failed after repair.",
                artifact_ids=artifacts,
                next_suggestion="Return to debugging if repair attempts remain.",
            )
        return self._finalize_result(result, artifact_ids=artifacts)

    def _prepare_patch_candidates(
        self,
        plan: RepairPlan,
        candidates: list[RepairPatchDraft],
    ) -> tuple[_PreparedRepairPatch | None, list[dict[str, object]]]:
        attempts: list[dict[str, object]] = []
        for index, draft in enumerate(candidates, start=1):
            try:
                precheck_error = self._precheck_repair_paths(draft)
                if precheck_error:
                    attempts.append(
                        {
                            "attempt": index,
                            "status": "validation_failed",
                            "error": precheck_error,
                            "warnings": [],
                        }
                    )
                    continue
                patch = self.patch_service.create_unified_diff(
                    self._file_changes_for_patch_draft(draft)
                )
                candidate_path = self.stage_dir / f"repair_patch_attempt_{index}.diff"
                fs.write_text(candidate_path, patch.text)
                validation = self.patch_service.validate_patch(
                    candidate_path,
                    self.run_context.task_config.project_path,
                )
                if not validation.valid:
                    attempts.append(
                        {
                            "attempt": index,
                            "status": "validation_failed",
                            "error": "; ".join(validation.errors),
                            "warnings": validation.warnings,
                        }
                    )
                    continue
                risk = self.risk_checker.assess(
                    validation,
                    allow_test_modification=_repair_allows_test_modification(plan),
                )
                summary = self.patch_service.summarize_patch(candidate_path)
                attempts.append(
                    {
                        "attempt": index,
                        "status": "valid",
                        "changed_files": validation.changed_files,
                        "warnings": validation.warnings,
                        "risk": risk.to_json_dict(),
                    }
                )
                return (
                    _PreparedRepairPatch(
                        plan=plan,
                        draft=draft,
                        validation=validation,
                        summary=summary,
                        risk=risk,
                        patch_text=patch.text,
                    ),
                    attempts,
                )
            except (OSError, PatchValidationError, ValueError) as exc:
                attempts.append(
                    {
                        "attempt": index,
                        "status": "validation_failed",
                        "error": str(exc),
                        "warnings": [],
                    }
                )
        return None, attempts

    def _precheck_repair_paths(self, draft: RepairPatchDraft) -> str:
        root = self.run_context.task_config.project_path.resolve()
        sensitive_filter = SensitiveFilter(root)
        errors: list[str] = []
        for change in draft.changes:
            normalized = _normalize_plan_path(change.path)
            if normalized is None:
                errors.append(f"repair path outside project root: {change.path}")
                continue
            target = (root / Path(normalized)).resolve()
            if not _is_relative_to(target, root):
                errors.append(f"repair path outside project root: {change.path}")
                continue
            if _is_hidden_benchmark_path(normalized):
                errors.append(f"repair path targets hidden benchmark material: {normalized}")
                continue
            if sensitive_filter.is_denied(target):
                errors.append(f"repair path targets sensitive or generated path: {normalized}")
        return "; ".join(errors)

    def _file_changes_for_patch_draft(self, draft: RepairPatchDraft) -> list[FileChange]:
        root = self.run_context.task_config.project_path
        changes: list[FileChange] = []
        for change in draft.changes:
            old_content = change.old_content
            if old_content is None:
                old_content = self._read_existing_content_if_safe(root, change.path)
            changes.append(
                FileChange(
                    path=change.path,
                    old_content=old_content,
                    new_content=change.new_content,
                )
            )
        return changes

    def _read_existing_content_if_safe(self, root: Path, relative_path: Path) -> str | None:
        if relative_path.is_absolute() or any(part == ".." for part in relative_path.parts):
            return None
        target = (root / relative_path).resolve()
        if not _is_relative_to(target, root.resolve()):
            return None
        if SensitiveFilter(root).is_denied(target):
            return None
        try:
            relative = target.relative_to(root.resolve()).as_posix()
        except ValueError:
            return None
        if _is_hidden_benchmark_path(relative):
            return None
        if not fs.is_file(target):
            return None
        return fs.read_text(target)

    def _request_from_patch_edit(
        self,
        request: RepairRequest,
        *,
        started_at: str,
    ) -> RepairRequest | StageResult | None:
        approval = request.patch_approval
        if approval.decision_type != "edit":
            return None
        self._record_decision(approval, action="approve_repair_patch")
        raw_draft = (approval.edited_payload or {}).get("patch_draft")
        if not isinstance(raw_draft, dict):
            raw_draft = (approval.edited_payload or {}).get("plan")
        if not isinstance(raw_draft, dict):
            return self._failed_result(
                started_at=started_at,
                summary="Repair edit did not include an edited patch draft.",
                category="hitl",
                message="edited_payload.patch_draft is required for repair patch edit decisions",
                artifact_ids=[],
                next_suggestion="Resume with edited_payload.patch_draft or regenerate repair.",
            )
        try:
            draft = RepairPatchDraft.model_validate(raw_draft)
        except ValidationError as exc:
            return self._failed_result(
                started_at=started_at,
                summary="Repair edit payload failed schema validation.",
                category="hitl",
                message=str(exc),
                artifact_ids=[],
                next_suggestion="Provide an edited repair plan that matches the schema.",
            )
        return replace(
            request,
            patch_draft=draft,
            patch_approval=ApprovalDecision(
                interrupt_id=REPAIR_PATCH_INTERRUPT_ID,
                decision_type="approve",
                comment=approval.comment or "Apply edited repair patch.",
                decided_by=approval.decided_by,
                auto=approval.auto,
            ),
        )

    def _handle_plan_review(
        self,
        approval: ApprovalDecision,
        started_at: str,
        *,
        record_decision: bool = True,
    ) -> StageResult | None:
        if approval.interrupt_id != REPAIR_PLAN_INTERRUPT_ID:
            return self._failed_result(
                started_at=started_at,
                summary="Repair plan review decision did not match the expected interrupt.",
                category="hitl",
                message="approval decision interrupt_id does not match repair plan",
                artifact_ids=[],
                next_suggestion="Resume with a decision for the repair_plan interrupt.",
            )
        if record_decision:
            self._record_decision(approval, action="review_repair_plan")
        return _result_from_non_approve_decision(
            approval,
            node="review_repair_plan",
            started_at=started_at,
        )

    def _handle_patch_approval(
        self,
        approval: ApprovalDecision,
        started_at: str,
    ) -> StageResult | None:
        if approval.interrupt_id != REPAIR_PATCH_INTERRUPT_ID:
            return self._failed_result(
                started_at=started_at,
                summary="Repair patch approval decision did not match the expected interrupt.",
                category="hitl",
                message="approval decision interrupt_id does not match repair patch",
                artifact_ids=[],
                next_suggestion="Resume with a decision for the repair_patch interrupt.",
            )
        self._record_decision(approval, action="approve_repair_patch")
        return _result_from_non_approve_decision(
            approval,
            node="approve_repair_patch",
            started_at=started_at,
        )

    def _command_from_decision(
        self,
        command: str,
        approval: ApprovalDecision,
    ) -> str | StageResult:
        if approval.interrupt_id != REPAIR_COMMAND_INTERRUPT_ID:
            return self._failed_result(
                started_at=utc_timestamp(),
                summary="Repair regression approval did not match the expected interrupt.",
                category="hitl",
                message="approval decision interrupt_id does not match repair regression command",
                artifact_ids=[],
                next_suggestion="Resume with a decision for the repair regression command.",
            )
        self._record_decision(approval, action="approve_regression_command")
        if approval.decision_type == "approve":
            hidden_error = _hidden_command_path_error(command)
            if hidden_error:
                return self._failed_result(
                    started_at=utc_timestamp(),
                    summary="Repair regression command targets hidden benchmark material.",
                    category="validation",
                    message=hidden_error,
                    artifact_ids=[],
                    next_suggestion="Use a visible pytest/unittest command.",
                )
            return command
        if approval.decision_type == "edit":
            edited_command = (approval.edited_payload or {}).get("command")
            if isinstance(edited_command, str) and edited_command.strip():
                hidden_error = _hidden_command_path_error(edited_command)
                if hidden_error:
                    return self._failed_result(
                        started_at=utc_timestamp(),
                        summary="Repair regression command targets hidden benchmark material.",
                        category="validation",
                        message=hidden_error,
                        artifact_ids=[],
                        next_suggestion="Use a visible pytest/unittest command.",
                    )
                return edited_command
            return self._failed_result(
                started_at=utc_timestamp(),
                summary="Repair command edit did not include a command.",
                category="hitl",
                message="edited_payload.command is required for command edit decisions",
                artifact_ids=[],
                next_suggestion="Resume with an edited command or reject verification.",
            )
        return _result_from_non_approve_decision(
            approval,
            node="approve_regression_command",
            started_at=utc_timestamp(),
        )

    def _run_command(self, command: str, request: RepairRequest) -> ShellResult:
        timeout = (
            request.command_timeout_seconds
            or self.run_context.task_config.test_command.timeout_seconds
        )
        approval = CommandApproval.approve(
            operation_id="repair_verify",
            approved_by="workflow",
            reason="Run approved repair verification command.",
        )
        try:
            return self.shell_runner.run(
                command,
                cwd=self.run_context.task_config.project_path,
                timeout_seconds=timeout,
                approval=approval,
            )
        except (CommandDeniedError, ValueError) as exc:
            raise RuntimeError(f"repair command failed before execution: {exc}") from exc

    def _record_decision(self, approval: ApprovalDecision, *, action: str) -> None:
        self.writer.record_human_decision(
            HumanDecision(
                interrupt_id=approval.interrupt_id,
                action=action,
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
            stage=REPAIR_STAGE,
            action=action,
            interrupt_id=approval.interrupt_id,
            decision_type=approval.decision_type,
            auto=approval.auto,
            decision_source=approval.decision_source,
            presented_to_user=approval.presented_to_user,
            decided_by=approval.decided_by,
            comment=approval.comment,
        )

    def _write_plan_artifacts(self, plan: RepairPlan) -> list[str]:
        plan_path = self.stage_dir / "repair_plan.md"
        fs.write_text(plan_path, _render_plan(plan))
        plan_json_path = self.stage_dir / "repair_plan.json"
        fs.write_text(
            plan_json_path,
            json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=False),
        )
        return [
            self._record_artifact(
                "repair_plan_final",
                ArtifactKind.REPORT,
                plan_path,
                "Final repair plan",
            ),
            self._record_artifact(
                "repair_plan_final_json",
                ArtifactKind.JSON,
                plan_json_path,
                "Structured final repair plan",
            ),
        ]

    def _load_prepared_plan(self, fallback_plan: RepairPlan) -> RepairPlan:
        path = self.stage_dir / "repair_plan.json"
        if not fs.exists(path):
            return fallback_plan
        try:
            return RepairPlan.model_validate(json.loads(fs.read_text(path)))
        except (OSError, json.JSONDecodeError, ValidationError):
            return fallback_plan

    def _write_patch_draft_json(self, draft: RepairPatchDraft) -> Path:
        path = self.stage_dir / "repair_patch_draft.json"
        fs.write_text(
            path,
            json.dumps(draft.model_dump(mode="json"), indent=2, ensure_ascii=False),
        )
        return path

    def _load_prepared_patch_draft(
        self,
        fallback_draft: RepairPatchDraft | None,
    ) -> RepairPatchDraft | None:
        path = self.stage_dir / "repair_patch_draft.json"
        if not fs.exists(path):
            return fallback_draft
        try:
            return RepairPatchDraft.model_validate(json.loads(fs.read_text(path)))
        except (OSError, json.JSONDecodeError, ValidationError):
            return fallback_draft

    def _write_attempts(self, attempts: list[dict[str, object]]) -> Path:
        path = self.stage_dir / "repair_patch_attempts.json"
        fs.write_text(
            path,
            json.dumps({"attempts": attempts}, indent=2, ensure_ascii=False),
        )
        return path

    def _write_risk_report(self, risk: RepairRiskReport) -> Path:
        path = self.stage_dir / "repair_risk.json"
        fs.write_text(
            path,
            json.dumps(risk.to_json_dict(), indent=2, ensure_ascii=False),
        )
        return path

    def _write_changed_files(self, changed_files: list[str]) -> Path:
        path = self.stage_dir / "changed_files.json"
        fs.write_text(
            path,
            json.dumps(
                {"stage": REPAIR_STAGE, "changed_files": changed_files},
                indent=2,
                ensure_ascii=False,
            ),
        )
        return path

    def _write_after_test_log(self, shell: ShellResult) -> Path:
        path = self.stage_dir / "after_test.log"
        fs.write_text(
            path,
            "\n".join(
                [
                    f"Command: {shell.command}",
                    f"Exit code: {shell.exit_code}",
                    "",
                    "## stdout",
                    shell.stdout,
                    "",
                    "## stderr",
                    shell.stderr,
                ]
            ),
        )
        return path

    def _write_test_result(self, payload: dict[str, object]) -> Path:
        path = self.stage_dir / "repair_test_result.json"
        fs.write_text(
            path,
            json.dumps(payload, indent=2, ensure_ascii=False),
        )
        return path

    def _write_repair_report(
        self,
        *,
        plan: RepairPlan,
        patch_draft: RepairPatchDraft | None = None,
        command: str,
        changed_files: list[str],
        test_result: dict[str, object],
        success: bool,
    ) -> Path:
        path = self.stage_dir / "repair_report.md"
        lines = [
            "# 修复报告",
            "",
            "## 根因",
            "",
            plan.root_cause,
            "",
            "## 修复策略",
            "",
            plan.strategy,
            "",
            "## 故障归因",
            "",
            f"- 来源: `{plan.failure_origin}`",
            f"- 允许修复可见测试: `{plan.test_repair_allowed}`",
            f"- 理由: {plan.test_repair_rationale or '未授权测试修改'}",
            "",
            "## 已变更文件",
            "",
        ]
        lines.extend(f"- `{item}`" for item in changed_files)
        if patch_draft is not None:
            lines.extend(["", "## 生成的修复文件", ""])
            for change in patch_draft.changes:
                lines.append(f"- `{change.path.as_posix()}`: {change.rationale}")
        lines.extend(
            [
                "",
                "## 验证",
                "",
                f"- 命令: `{command}`",
                f"- 成功: {success}",
                f"- 通过: {test_result.get('passed')}",
                f"- 失败: {test_result.get('failed')}",
                f"- 错误: {test_result.get('errors')}",
            ]
        )
        if not success:
            lines.extend(["", "## 测试失败", "", str(test_result.get("error_summary") or "")])
        fs.write_text(path, "\n".join(lines) + "\n")
        return path

    def _register_existing_artifacts(
        self,
        fallback_plan: RepairPlan,
        fallback_draft: RepairPatchDraft | None = None,
    ) -> list[str]:
        if not fs.exists(self.stage_dir / "repair_plan.md"):
            artifacts = self._write_plan_artifacts(fallback_plan)
        else:
            artifacts = [
                self._record_artifact(
                    "repair_plan_final",
                    ArtifactKind.REPORT,
                    self.stage_dir / "repair_plan.md",
                    "Final repair plan",
                ),
                self._record_artifact(
                    "repair_plan_final_json",
                    ArtifactKind.JSON,
                    self.stage_dir / "repair_plan.json",
                    "Structured final repair plan",
                ),
            ]
        draft_json_path = self.stage_dir / "repair_patch_draft.json"
        if not fs.exists(draft_json_path) and fallback_draft is not None:
            draft_json_path = self._write_patch_draft_json(fallback_draft)
        if fs.exists(draft_json_path):
            artifacts.append(
                self._record_artifact(
                    "repair_patch_draft_json",
                    ArtifactKind.JSON,
                    draft_json_path,
                    "Structured repair patch draft",
                )
            )
        existing = [
            ("repair_patch", ArtifactKind.PATCH, "repair.patch.diff", "Repair patch diff"),
            ("repair_risk", ArtifactKind.JSON, "repair_risk.json", "Repair patch risk report"),
            ("repair_patch_attempts", ArtifactKind.JSON, "repair_patch_attempts.json", "Repair patch validation attempts"),
            ("repair_changed_files", ArtifactKind.JSON, "changed_files.json", "Repair changed files"),
        ]
        for artifact_id, kind, filename, summary in existing:
            path = self.stage_dir / filename
            if fs.exists(path):
                artifacts.append(self._record_artifact(artifact_id, kind, path, summary))
        return artifacts

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
            stage=REPAIR_STAGE,
            status="failed",
            started_at=started_at,
            ended_at=utc_timestamp(),
            summary=summary,
            artifact_ids=artifact_ids,
            error=ErrorRecord(
                error_id=f"repair_{category}",
                stage=REPAIR_STAGE,
                node=REPAIR_STAGE,
                category=category,  # type: ignore[arg-type]
                message=message or summary,
                artifact_ids=artifact_ids,
                retryable=True,
            ),
            next_suggestion=next_suggestion,
        )

    def _finalize_result(self, result: StageResult, *, artifact_ids: list[str]) -> StageResult:
        if fs.exists(self.stage_dir) and not fs.exists(self.stage_dir / "repair_report.md"):
            report_path = self.stage_dir / "repair_report.md"
            fs.write_text(
                report_path,
                "# 修复报告\n\n"
                f"## 状态\n\n{result.summary}\n\n"
                f"## 下一步\n\n{result.next_suggestion}\n",
            )
            artifact_ids = [
                *artifact_ids,
                self._record_artifact(
                    "repair_report",
                    ArtifactKind.REPORT,
                    report_path,
                    "Repair report",
                ),
            ]
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
                stage=REPAIR_STAGE,
                kind=kind,
                path=path,
                summary=summary,
            )
        )
        self.run_context.artifact_store.write()
        return artifact_id


def _run_relative_path(path: Path, *, run_dir: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _result_from_non_approve_decision(
    approval: ApprovalDecision,
    *,
    node: str,
    started_at: str,
) -> StageResult | None:
    if approval.decision_type == "approve":
        return None
    status: Literal["failed", "cancelled"] = (
        "cancelled" if approval.decision_type == "cancel" else "failed"
    )
    return StageResult(
        stage=REPAIR_STAGE,
        status=status,
        started_at=started_at,
        ended_at=utc_timestamp(),
        summary=f"Repair approval returned {approval.decision_type}.",
        error=ErrorRecord(
            error_id=f"repair_{approval.decision_type}",
            stage=REPAIR_STAGE,
            node=node,
            category="hitl",
            message=f"approval decision: {approval.decision_type}",
            retryable=status != "cancelled",
        ),
        next_suggestion=(
            "Run was cancelled by the approval decision."
            if status == "cancelled"
            else "Revise or approve the repair artifact before continuing."
        ),
    )


def _render_plan(plan: RepairPlan) -> str:
    lines = [
        "# 最终修复计划",
        "",
        "## 根因",
        "",
        plan.root_cause,
        "",
        "## 修复策略",
        "",
        plan.strategy,
        "",
        "## 故障归因",
        "",
        f"- 来源: `{plan.failure_origin}`",
        f"- 允许修复可见测试: `{plan.test_repair_allowed}`",
        f"- 理由: {plan.test_repair_rationale or '未授权测试修改'}",
        "",
        "## 计划变更",
        "",
    ]
    for change in plan.changes:
        lines.extend(
            [
                f"- `{change.path.as_posix()}`",
                f"  - 理由: {change.rationale}",
                f"  - 预期效果: {change.expected_effect}",
            ]
        )
    lines.extend(["", "## 验证", "", f"`{plan.verification_command}`"])
    return "\n".join(lines) + "\n"


def _repair_allows_test_modification(plan: RepairPlan) -> bool:
    return bool(
        plan.test_repair_allowed
        and plan.failure_origin in {"generated_test_code", "mixed", "test_harness"}
        and (plan.test_repair_rationale or "").strip()
    )


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


def _normalize_plan_path(path: Path) -> str | None:
    raw = str(path).replace("\\", "/")
    posix_path = PurePosixPath(raw)
    if (
        not raw
        or posix_path.is_absolute()
        or any(part in {"", ".."} for part in posix_path.parts)
        or (posix_path.parts and ":" in posix_path.parts[0])
    ):
        return None
    return posix_path.as_posix()


def _is_hidden_benchmark_path(path: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(path.replace("\\", "/")).parts]
    return (
        "evaluation" in parts
        or "oracle_tests" in parts
        or any(part == "expected_result.json" for part in parts)
    )


def _risk_message(risk: RepairRiskReport) -> str:
    return "; ".join(
        f"{finding.kind} at {finding.path}: {finding.message}"
        for finding in risk.findings
    ) or "repair patch risk is high"


def _last_attempt_error(attempts: list[dict[str, object]]) -> str:
    if not attempts:
        return "no repair patch attempts were generated"
    return str(attempts[-1].get("error") or attempts[-1].get("status") or "unknown")


def _read_changed_files(path: Path) -> list[str]:
    if not fs.exists(path):
        return []
    try:
        data = json.loads(fs.read_text(path))
    except (OSError, json.JSONDecodeError):
        return []
    changed = data.get("changed_files") if isinstance(data, dict) else None
    return [str(item) for item in changed] if isinstance(changed, list) else []


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
