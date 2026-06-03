"""Testing stage orchestration service."""

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
    PatchArtifact,
    PatchService,
    PatchSummary,
    PatchValidationError,
    PatchValidationResult,
)
from codeagent.tools.hitl import ApprovalDecision
from codeagent.tools.pytest_tools import parse_shell_result
from codeagent.tools.shell_tools import CommandDeniedError, ShellRunner


TESTING_STAGE = "testing"
TEST_PLAN_INTERRUPT_ID = "testing_plan"
TEST_PATCH_INTERRUPT_ID = "testing_patch"
TEST_COMMAND_INTERRUPT_ID = "testing_command"


class TestFileChange(BaseModel):
    """Structured test-file change intent."""

    __test__: ClassVar[bool] = False
    model_config = ConfigDict(extra="forbid")

    path: Path
    old_content: str | None = None
    new_content: str | None = None
    rationale: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def require_real_change(self) -> "TestFileChange":
        if self.old_content is None and self.new_content is None:
            raise ValueError("test file change must include old_content or new_content")
        return self


class TestingPlan(BaseModel):
    """Validated test plan and test patch intent."""

    __test__: ClassVar[bool] = False
    model_config = ConfigDict(extra="forbid")

    target_summary: str = Field(min_length=1, max_length=8000)
    strategy: str = Field(min_length=1, max_length=8000)
    acceptance_criteria: list[str] = Field(min_length=1)
    changes: list[TestFileChange] = Field(min_length=1)
    command: str = Field(min_length=1, max_length=1000)
    framework: Literal["pytest", "unittest"] = "pytest"


@dataclass(frozen=True)
class TestingRequest:
    __test__: ClassVar[bool] = False

    plan: TestingPlan
    plan_review: ApprovalDecision
    patch_approval: ApprovalDecision
    command_approval: ApprovalDecision
    alternate_plans: list[TestingPlan] = field(default_factory=list)
    max_patch_attempts: int = 3
    command_timeout_seconds: float | None = None


@dataclass(frozen=True)
class _PreparedTestPatch:
    plan: TestingPlan
    patch: PatchArtifact
    validation: PatchValidationResult
    summary: PatchSummary


@dataclass(frozen=True)
class TestingApprovalPreview:
    payload: dict[str, object] | None = None
    result: StageResult | None = None


class TestingService:
    """Run testing-stage plan review, patch, command, parsing, and reports."""

    __test__: ClassVar[bool] = False

    def __init__(
        self,
        *,
        run_context: RunContext,
        patch_service: PatchService | None = None,
        shell_runner: ShellRunner | None = None,
    ) -> None:
        self.run_context = run_context
        self.patch_service = patch_service or PatchService()
        self.stage_dir = run_context.stage_dirs[Stage.TEST]
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

    def prepare_plan_review(self, request: TestingRequest) -> TestingApprovalPreview:
        fs.mkdir(self.stage_dir)
        plan_path = self._write_plan(request.plan)
        plan_json_path = self._write_plan_json(request.plan)
        artifacts = [
            self._record_artifact(
                "testing_test_plan",
                ArtifactKind.REPORT,
                plan_path,
                "Testing plan",
            ),
            self._record_artifact(
                "testing_test_plan_json",
                ArtifactKind.JSON,
                plan_json_path,
                "Structured testing plan",
            ),
        ]
        return TestingApprovalPreview(
            payload={
                "interrupt_id": TEST_PLAN_INTERRUPT_ID,
                "action": "review_test_plan",
                "title": "Review test plan",
                "summary": request.plan.target_summary,
                "risk_level": "low",
                "allowed_decisions": ["approve", "edit", "reject", "respond", "cancel"],
                "default_decision": "reject",
                "payload": {
                    "plan_path": "testing/test_plan.md",
                    "plan_json_path": "testing/test_plan.json",
                    "strategy": request.plan.strategy,
                    "acceptance_criteria": request.plan.acceptance_criteria,
                    "artifact_ids": artifacts,
                },
            }
        )

    def prepare_patch_approval(
        self,
        request: TestingRequest,
        *,
        plan_review: ApprovalDecision,
    ) -> TestingApprovalPreview:
        started_at = utc_timestamp()
        reviewed = replace(request, plan_review=plan_review)
        edited_plan = self._request_from_plan_edit(reviewed, started_at=started_at)
        if isinstance(edited_plan, StageResult):
            return TestingApprovalPreview(
                result=self._finalize_result(
                    edited_plan,
                    plan=reviewed.plan,
                    artifact_ids=self._register_existing_artifacts(reviewed.plan),
                    attempts=[],
                )
            )
        if edited_plan is not None:
            return self.prepare_patch_approval(
                edited_plan,
                plan_review=edited_plan.plan_review,
            )
        plan_decision = self._handle_plan_review(plan_review, started_at)
        if plan_decision is not None:
            return TestingApprovalPreview(
                result=self._finalize_result(
                    plan_decision,
                    plan=reviewed.plan,
                    artifact_ids=self._register_existing_artifacts(reviewed.plan),
                    attempts=[],
                )
            )
        candidates = [reviewed.plan, *reviewed.alternate_plans][
            : max(1, reviewed.max_patch_attempts)
        ]
        prepared, attempts = self._prepare_patch_candidates(candidates)
        plan_path = self._write_plan(reviewed.plan)
        plan_json_path = self._write_plan_json(reviewed.plan)
        artifacts = [
            self._record_artifact(
                "testing_test_plan",
                ArtifactKind.REPORT,
                plan_path,
                "Testing plan",
            ),
            self._record_artifact(
                "testing_test_plan_json",
                ArtifactKind.JSON,
                plan_json_path,
                "Structured testing plan",
            ),
        ]
        attempts_path = self._write_attempts(attempts)
        artifacts.append(
            self._record_artifact(
                "testing_test_patch_attempts",
                ArtifactKind.JSON,
                attempts_path,
                "Testing patch validation attempts",
            )
        )
        if prepared is None:
            result = self._failed_result(
                started_at=started_at,
                summary="Testing patch validation failed before approval.",
                category="patch",
                message=_last_attempt_error(attempts),
                artifact_ids=artifacts,
                next_suggestion="Revise the test plan or test patch candidate and retry.",
            )
            return TestingApprovalPreview(
                result=self._finalize_result(
                    result,
                    plan=reviewed.plan,
                    artifact_ids=artifacts,
                    attempts=attempts,
                )
            )
        patch_path = self.stage_dir / "test.patch.diff"
        fs.write_text(patch_path, prepared.patch.text)
        artifacts.append(
            self._record_artifact(
                "testing_test_patch",
                ArtifactKind.PATCH,
                patch_path,
                "Testing patch diff",
            )
        )
        return TestingApprovalPreview(
            payload={
                "interrupt_id": TEST_PATCH_INTERRUPT_ID,
                "action": "approve_test_patch",
                "title": "Approve test patch",
                "summary": "Review the generated test patch before test files are modified.",
                "risk_level": prepared.validation.risk_report.level,
                "allowed_decisions": ["approve", "edit", "reject", "respond", "cancel"],
                "default_decision": "reject",
                "payload": {
                    "patch_path": "testing/test.patch.diff",
                    "changed_files": prepared.validation.changed_files,
                    "added_lines": prepared.summary.added_lines,
                    "removed_lines": prepared.summary.removed_lines,
                    "patch_sha256": _sha256_text(prepared.patch.text),
                    "artifact_ids": artifacts,
                },
            }
        )

    def apply_patch_and_prepare_command(
        self,
        request: TestingRequest,
        *,
        patch_approval: ApprovalDecision,
        approved_patch_sha256: str | None = None,
    ) -> TestingApprovalPreview:
        started_at = utc_timestamp()
        plan = self._load_prepared_plan(request.plan)
        artifacts = self._register_existing_artifacts(plan)
        edited_patch = self._request_from_patch_edit(request, started_at=started_at)
        if isinstance(edited_patch, StageResult):
            return TestingApprovalPreview(
                result=self._finalize_result(
                    edited_patch,
                    plan=plan,
                    artifact_ids=artifacts,
                    attempts=_read_attempts(self.stage_dir / "test_patch_attempts.json"),
                )
            )
        if edited_patch is not None:
            plan_review = ApprovalDecision(
                interrupt_id=TEST_PLAN_INTERRUPT_ID,
                decision_type="approve",
                comment=request.patch_approval.comment
                or "Approve edited testing plan for patch regeneration.",
                decided_by=request.patch_approval.decided_by,
                auto=request.patch_approval.auto,
            )
            return self.prepare_patch_approval(
                replace(edited_patch, plan_review=plan_review),
                plan_review=plan_review,
            )
        patch_decision = self._handle_patch_approval(patch_approval, started_at)
        if patch_decision is not None:
            patch_decision = patch_decision.model_copy(update={"artifact_ids": artifacts})
            return TestingApprovalPreview(
                result=self._finalize_result(
                    patch_decision,
                    plan=plan,
                    artifact_ids=artifacts,
                    attempts=_read_attempts(self.stage_dir / "test_patch_attempts.json"),
                )
        )
        patch_path = self.stage_dir / "test.patch.diff"
        if not fs.exists(patch_path):
            result = self._failed_result(
                started_at=started_at,
                summary="Approved testing patch is missing.",
                category="patch",
                message="testing/test.patch.diff was not found at resume time",
                artifact_ids=artifacts,
                next_suggestion="Regenerate and approve the testing patch again.",
            )
            return TestingApprovalPreview(
                result=self._finalize_result(
                    result,
                    plan=plan,
                    artifact_ids=artifacts,
                    attempts=_read_attempts(self.stage_dir / "test_patch_attempts.json"),
                )
            )
        patch_text = fs.read_text(patch_path)
        if approved_patch_sha256 and _sha256_text(patch_text) != approved_patch_sha256:
            result = self._failed_result(
                started_at=started_at,
                summary="Approved testing patch changed before application.",
                category="patch",
                message="approved testing patch hash mismatch",
                artifact_ids=artifacts,
                next_suggestion="Review and approve the current testing patch before applying it.",
            )
            return TestingApprovalPreview(
                result=self._finalize_result(
                    result,
                    plan=plan,
                    artifact_ids=artifacts,
                    attempts=_read_attempts(self.stage_dir / "test_patch_attempts.json"),
                )
            )
        try:
            applied = self.patch_service.apply_patch(
                patch_path,
                self.run_context.task_config.project_path,
                operation_id="testing_apply_patch",
            )
        except (PatchApplyError, PatchValidationError) as exc:
            result = self._failed_result(
                started_at=started_at,
                summary="Testing patch could not be applied.",
                category="patch",
                message=str(exc),
                artifact_ids=artifacts,
                next_suggestion="Regenerate the testing patch against the current project files.",
            )
            return TestingApprovalPreview(
                result=self._finalize_result(
                    result,
                    plan=plan,
                    artifact_ids=artifacts,
                    attempts=_read_attempts(self.stage_dir / "test_patch_attempts.json"),
                )
            )
        changed_files_path = self._write_changed_files(applied.changed_files)
        artifacts.append(
            self._record_artifact(
                "testing_changed_files",
                ArtifactKind.JSON,
                changed_files_path,
                "Testing changed files",
            )
        )
        return TestingApprovalPreview(
            payload={
                "interrupt_id": TEST_COMMAND_INTERRUPT_ID,
                "action": "approve_test_command",
                "title": "Approve test command",
                "summary": plan.command,
                "risk_level": "medium",
                "allowed_decisions": ["approve", "edit", "reject", "cancel"],
                "default_decision": "reject",
                "payload": {
                    "command": plan.command,
                    "framework": plan.framework,
                    "changed_files": applied.changed_files,
                    "artifact_ids": artifacts,
                },
            }
        )

    def run_prepared_command(
        self,
        request: TestingRequest,
        *,
        command_approval: ApprovalDecision,
    ) -> StageResult:
        started_at = utc_timestamp()
        plan = self._load_prepared_plan(request.plan)
        artifacts = self._register_existing_artifacts(plan)
        changed_files = _read_changed_files(self.stage_dir / "changed_files.json")
        command = self._command_from_decision(plan.command, command_approval)
        if isinstance(command, StageResult):
            command_path = self._write_command_record(
                command=plan.command,
                executed=False,
                decision=command_approval.decision_type,
            )
            artifacts.append(
                self._record_artifact(
                    "testing_test_command",
                    ArtifactKind.JSON,
                    command_path,
                    "Testing command approval record",
                )
            )
            command = command.model_copy(update={"artifact_ids": artifacts})
            return self._finalize_result(
                command,
                plan=plan,
                artifact_ids=artifacts,
                attempts=_read_attempts(self.stage_dir / "test_patch_attempts.json"),
                changed_files=changed_files,
            )
        command_path = self._write_command_record(
            command=command,
            executed=True,
            decision=command_approval.decision_type,
        )
        artifacts.append(
            self._record_artifact(
                "testing_test_command",
                ArtifactKind.JSON,
                command_path,
                "Testing command approval record",
            )
        )
        try:
            shell = self._run_command(command, request)
        except RuntimeError as exc:
            result = self._failed_result(
                started_at=started_at,
                summary="Testing command was denied or could not start.",
                category="shell",
                message=str(exc),
                artifact_ids=artifacts,
                next_suggestion="Approve an allowed pytest, unittest, or py_compile command.",
            )
            return self._finalize_result(
                result,
                plan=plan,
                artifact_ids=artifacts,
                attempts=_read_attempts(self.stage_dir / "test_patch_attempts.json"),
                changed_files=changed_files,
            )
        parsed = parse_shell_result(framework=plan.framework, shell_result=shell)
        result_path = self._write_test_result(parsed.to_json_dict())
        artifacts.append(
            self._record_artifact(
                "testing_test_result",
                ArtifactKind.JSON,
                result_path,
                "Parsed test result",
            )
        )
        report_path = self._write_test_report(
            plan=plan,
            test_result=parsed.to_json_dict(),
            command=command,
        )
        artifacts.append(
            self._record_artifact(
                "testing_test_report",
                ArtifactKind.REPORT,
                report_path,
                "Testing report",
            )
        )
        if parsed.success:
            result = StageResult(
                stage=TESTING_STAGE,
                status="succeeded",
                started_at=started_at,
                ended_at=utc_timestamp(),
                summary="Testing command passed.",
                artifact_ids=artifacts,
            )
        else:
            result = self._failed_result(
                started_at=started_at,
                summary="Testing command failed.",
                category="pytest_failure",
                message=parsed.error_summary or "tests failed",
                artifact_ids=artifacts,
                next_suggestion="Enter debugging stage with the saved test logs and parsed failures.",
            )
        return self._finalize_result(
            result,
            plan=plan,
            artifact_ids=artifacts,
            attempts=_read_attempts(self.stage_dir / "test_patch_attempts.json"),
            changed_files=changed_files,
            test_result=parsed.to_json_dict(),
        )

    def run(self, request: TestingRequest) -> StageResult:
        started_at = utc_timestamp()
        edited_plan = self._request_from_plan_edit(request, started_at=started_at)
        if isinstance(edited_plan, StageResult):
            return self._finalize_result(
                edited_plan,
                plan=request.plan,
                artifact_ids=[],
                attempts=[],
            )
        if edited_plan is not None:
            return self.run(edited_plan)

        plan_decision = self._handle_plan_review(request.plan_review, started_at)
        if plan_decision is not None:
            return self._finalize_result(
                plan_decision,
                plan=request.plan,
                artifact_ids=[],
                attempts=[],
            )

        fs.mkdir(self.stage_dir)
        candidates = [request.plan, *request.alternate_plans][
            : max(1, request.max_patch_attempts)
        ]
        prepared, attempts = self._prepare_patch_candidates(candidates)
        artifacts: list[str] = []
        if prepared is None:
            plan_path = self._write_plan(candidates[0])
            plan_json_path = self._write_plan_json(candidates[0])
            attempts_path = self._write_attempts(attempts)
            artifacts.extend(
                [
                    self._record_artifact(
                        "testing_test_plan",
                        ArtifactKind.REPORT,
                        plan_path,
                        "Testing plan",
                    ),
                    self._record_artifact(
                        "testing_test_plan_json",
                        ArtifactKind.JSON,
                        plan_json_path,
                        "Structured testing plan",
                    ),
                    self._record_artifact(
                        "testing_test_patch_attempts",
                        ArtifactKind.JSON,
                        attempts_path,
                        "Testing patch validation attempts",
                    ),
                ]
            )
            result = self._failed_result(
                started_at=started_at,
                summary="Testing patch validation failed before approval.",
                category="patch",
                message=_last_attempt_error(attempts),
                artifact_ids=artifacts,
                next_suggestion="Revise the test plan or test patch candidate and retry.",
            )
            return self._finalize_result(
                result,
                plan=candidates[0],
                artifact_ids=artifacts,
                attempts=attempts,
            )

        plan_path = self._write_plan(prepared.plan)
        plan_json_path = self._write_plan_json(prepared.plan)
        patch_path = self.stage_dir / "test.patch.diff"
        fs.write_text(patch_path, prepared.patch.text)
        attempts_path = self._write_attempts(attempts)
        artifacts.extend(
            [
                self._record_artifact(
                    "testing_test_plan",
                    ArtifactKind.REPORT,
                    plan_path,
                    "Testing plan",
                ),
                self._record_artifact(
                    "testing_test_plan_json",
                    ArtifactKind.JSON,
                    plan_json_path,
                    "Structured testing plan",
                ),
                self._record_artifact(
                    "testing_test_patch",
                    ArtifactKind.PATCH,
                    patch_path,
                    "Testing patch diff",
                ),
                self._record_artifact(
                    "testing_test_patch_attempts",
                    ArtifactKind.JSON,
                    attempts_path,
                    "Testing patch validation attempts",
                ),
            ]
        )

        edited_patch = self._request_from_patch_edit(request, started_at=started_at)
        if isinstance(edited_patch, StageResult):
            return self._finalize_result(
                edited_patch,
                plan=prepared.plan,
                artifact_ids=artifacts,
                attempts=attempts,
                patch_summary=prepared.summary,
            )
        if edited_patch is not None:
            return self.run(edited_patch)

        patch_decision = self._handle_patch_approval(request.patch_approval, started_at)
        if patch_decision is not None:
            patch_decision = patch_decision.model_copy(update={"artifact_ids": artifacts})
            return self._finalize_result(
                patch_decision,
                plan=prepared.plan,
                artifact_ids=artifacts,
                attempts=attempts,
                patch_summary=prepared.summary,
            )

        try:
            applied = self.patch_service.apply_patch(
                patch_path,
                self.run_context.task_config.project_path,
                operation_id="testing_apply_patch",
            )
        except (PatchApplyError, PatchValidationError) as exc:
            result = self._failed_result(
                started_at=started_at,
                summary="Testing patch could not be applied.",
                category="patch",
                message=str(exc),
                artifact_ids=artifacts,
                next_suggestion="Regenerate the testing patch against the current project files.",
            )
            return self._finalize_result(
                result,
                plan=prepared.plan,
                artifact_ids=artifacts,
                attempts=attempts,
                patch_summary=prepared.summary,
            )

        changed_files_path = self._write_changed_files(applied.changed_files)
        artifacts.append(
            self._record_artifact(
                "testing_changed_files",
                ArtifactKind.JSON,
                changed_files_path,
                "Testing changed files",
            )
        )

        command = self._command_from_decision(prepared.plan.command, request.command_approval)
        if isinstance(command, StageResult):
            command_path = self._write_command_record(
                command=prepared.plan.command,
                executed=False,
                decision=request.command_approval.decision_type,
            )
            artifacts.append(
                self._record_artifact(
                    "testing_test_command",
                    ArtifactKind.JSON,
                    command_path,
                    "Testing command approval record",
                )
            )
            command = command.model_copy(update={"artifact_ids": artifacts})
            return self._finalize_result(
                command,
                plan=prepared.plan,
                artifact_ids=artifacts,
                attempts=attempts,
                patch_summary=prepared.summary,
                changed_files=applied.changed_files,
            )

        command_path = self._write_command_record(
            command=command,
            executed=True,
            decision=request.command_approval.decision_type,
        )
        artifacts.append(
            self._record_artifact(
                "testing_test_command",
                ArtifactKind.JSON,
                command_path,
                "Testing command approval record",
            )
        )
        try:
            shell = self._run_command(command, request)
        except RuntimeError as exc:
            result = self._failed_result(
                started_at=started_at,
                summary="Testing command was denied or could not start.",
                category="shell",
                message=str(exc),
                artifact_ids=artifacts,
                next_suggestion="Approve an allowed pytest, unittest, or py_compile command.",
            )
            return self._finalize_result(
                result,
                plan=prepared.plan,
                artifact_ids=artifacts,
                attempts=attempts,
                patch_summary=prepared.summary,
                changed_files=applied.changed_files,
            )
        parsed = parse_shell_result(framework=prepared.plan.framework, shell_result=shell)
        result_path = self._write_test_result(parsed.to_json_dict())
        artifacts.append(
            self._record_artifact(
                "testing_test_result",
                ArtifactKind.JSON,
                result_path,
                "Parsed test result",
            )
        )
        report_path = self._write_test_report(
            plan=prepared.plan,
            test_result=parsed.to_json_dict(),
            command=command,
        )
        artifacts.append(
            self._record_artifact(
                "testing_test_report",
                ArtifactKind.REPORT,
                report_path,
                "Testing report",
            )
        )

        if parsed.success:
            result = StageResult(
                stage=TESTING_STAGE,
                status="succeeded",
                started_at=started_at,
                ended_at=utc_timestamp(),
                summary="Testing command passed.",
                artifact_ids=artifacts,
            )
        else:
            result = self._failed_result(
                started_at=started_at,
                summary="Testing command failed.",
                category="pytest_failure",
                message=parsed.error_summary or "tests failed",
                artifact_ids=artifacts,
                next_suggestion="Enter debugging stage with the saved test logs and parsed failures.",
            )
        return self._finalize_result(
            result,
            plan=prepared.plan,
            artifact_ids=artifacts,
            attempts=attempts,
            patch_summary=prepared.summary,
            changed_files=applied.changed_files,
            test_result=parsed.to_json_dict(),
        )

    def _request_from_plan_edit(
        self,
        request: TestingRequest,
        *,
        started_at: str,
    ) -> TestingRequest | StageResult | None:
        approval = request.plan_review
        if approval.decision_type != "edit":
            return None
        self._record_decision(approval, action="review_test_plan")
        edited = _plan_from_payload(approval.edited_payload)
        if isinstance(edited, StageResult):
            return edited
        return replace(
            request,
            plan=edited,
            plan_review=ApprovalDecision(
                interrupt_id=TEST_PLAN_INTERRUPT_ID,
                decision_type="approve",
                comment=approval.comment or "Apply edited testing plan.",
                decided_by=approval.decided_by,
                auto=approval.auto,
            ),
        )

    def _request_from_patch_edit(
        self,
        request: TestingRequest,
        *,
        started_at: str,
    ) -> TestingRequest | StageResult | None:
        approval = request.patch_approval
        if approval.decision_type != "edit":
            return None
        self._record_decision(approval, action="approve_test_patch")
        edited = _plan_from_payload(approval.edited_payload)
        if isinstance(edited, StageResult):
            return edited
        return replace(
            request,
            plan=edited,
            patch_approval=ApprovalDecision(
                interrupt_id=TEST_PATCH_INTERRUPT_ID,
                decision_type="approve",
                comment=approval.comment or "Apply edited testing patch.",
                decided_by=approval.decided_by,
                auto=approval.auto,
            ),
        )

    def _handle_plan_review(
        self,
        approval: ApprovalDecision,
        started_at: str,
    ) -> StageResult | None:
        if approval.interrupt_id != TEST_PLAN_INTERRUPT_ID:
            return self._failed_result(
                started_at=started_at,
                summary="Testing plan review decision did not match the expected interrupt.",
                category="hitl",
                message="approval decision interrupt_id does not match testing plan",
                artifact_ids=[],
                next_suggestion="Resume with a decision for the testing_plan interrupt.",
            )
        self._record_decision(approval, action="review_test_plan")
        return _result_from_non_approve_decision(
            approval,
            stage=TESTING_STAGE,
            node="review_test_plan",
            started_at=started_at,
        )

    def _handle_patch_approval(
        self,
        approval: ApprovalDecision,
        started_at: str,
    ) -> StageResult | None:
        if approval.interrupt_id != TEST_PATCH_INTERRUPT_ID:
            return self._failed_result(
                started_at=started_at,
                summary="Testing patch approval decision did not match the expected interrupt.",
                category="hitl",
                message="approval decision interrupt_id does not match testing patch",
                artifact_ids=[],
                next_suggestion="Resume with a decision for the testing_patch interrupt.",
            )
        self._record_decision(approval, action="approve_test_patch")
        return _result_from_non_approve_decision(
            approval,
            stage=TESTING_STAGE,
            node="approve_test_patch",
            started_at=started_at,
        )

    def _command_from_decision(
        self,
        command: str,
        approval: ApprovalDecision,
    ) -> str | StageResult:
        if approval.interrupt_id != TEST_COMMAND_INTERRUPT_ID:
            return self._failed_result(
                started_at=utc_timestamp(),
                summary="Testing command approval decision did not match the expected interrupt.",
                category="hitl",
                message="approval decision interrupt_id does not match testing command",
                artifact_ids=[],
                next_suggestion="Resume with a decision for the testing_command interrupt.",
            )
        self._record_decision(approval, action="approve_test_command")
        if approval.decision_type == "approve":
            hidden_error = _hidden_command_path_error(command)
            if hidden_error:
                return self._failed_result(
                    started_at=utc_timestamp(),
                    summary="Testing command targets hidden benchmark material.",
                    category="validation",
                    message=hidden_error,
                    artifact_ids=[],
                    next_suggestion="Use a visible pytest/unittest command that does not reference hidden benchmark paths.",
                )
            return command
        if approval.decision_type == "edit":
            edited = approval.edited_payload or {}
            edited_command = edited.get("command")
            if isinstance(edited_command, str) and edited_command.strip():
                hidden_error = _hidden_command_path_error(edited_command)
                if hidden_error:
                    return self._failed_result(
                        started_at=utc_timestamp(),
                        summary="Testing command targets hidden benchmark material.",
                        category="validation",
                        message=hidden_error,
                        artifact_ids=[],
                        next_suggestion="Use a visible pytest/unittest command that does not reference hidden benchmark paths.",
                    )
                return edited_command
            return self._failed_result(
                started_at=utc_timestamp(),
                summary="Testing command edit did not include a command.",
                category="hitl",
                message="edited_payload.command is required for command edit decisions",
                artifact_ids=[],
                next_suggestion="Resume with an edited command or approve the original command.",
            )
        return _result_from_non_approve_decision(
            approval,
            stage=TESTING_STAGE,
            node="approve_test_command",
            started_at=utc_timestamp(),
        )

    def _prepare_patch_candidates(
        self,
        candidates: list[TestingPlan],
    ) -> tuple[_PreparedTestPatch | None, list[dict[str, object]]]:
        attempts: list[dict[str, object]] = []
        for index, plan in enumerate(candidates, start=1):
            try:
                precheck_error = self._precheck_test_paths(plan)
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
                    self._file_changes_for_plan(plan)
                )
                candidate_path = self.stage_dir / f"test_patch_attempt_{index}.diff"
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
                summary = self.patch_service.summarize_patch(candidate_path)
                attempts.append(
                    {
                        "attempt": index,
                        "status": "valid",
                        "changed_files": validation.changed_files,
                        "warnings": validation.warnings,
                        "risk_level": validation.risk_report.level,
                    }
                )
                return _PreparedTestPatch(
                    plan=plan,
                    patch=patch,
                    validation=validation,
                    summary=summary,
                ), attempts
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

    def _precheck_test_paths(self, plan: TestingPlan) -> str:
        root = self.run_context.task_config.project_path.resolve()
        errors: list[str] = []
        sensitive_filter = SensitiveFilter(root)
        for change in plan.changes:
            normalized = _normalize_plan_path(change.path)
            if normalized is None:
                errors.append(f"patch path outside project root: {change.path}")
                continue
            target = (root / Path(normalized)).resolve()
            if not _is_relative_to(target, root):
                errors.append(f"patch path outside project root: {change.path}")
                continue
            if sensitive_filter.is_denied(target):
                errors.append(f"patch targets sensitive or generated path: {normalized}")
                continue
            if _is_hidden_benchmark_path(normalized):
                errors.append(f"test patch targets hidden benchmark path: {normalized}")
                continue
            if not _is_allowed_test_path(normalized):
                errors.append(f"test patch must target a test path: {normalized}")
        return "; ".join(errors)

    def _file_changes_for_plan(self, plan: TestingPlan) -> list[FileChange]:
        root = self.run_context.task_config.project_path
        changes: list[FileChange] = []
        for change in plan.changes:
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
        if not fs.is_file(target):
            return None
        return fs.read_text(target)

    def _run_command(self, command: str, request: TestingRequest) -> ShellResult:
        timeout = (
            request.command_timeout_seconds
            or self.run_context.task_config.test_command.timeout_seconds
        )
        approval = CommandApproval.approve(
            operation_id="testing_run_tests",
            approved_by="workflow",
            reason="Run approved testing command.",
        )
        try:
            return self.shell_runner.run(
                command,
                cwd=self.run_context.task_config.project_path,
                timeout_seconds=timeout,
                approval=approval,
            )
        except (CommandDeniedError, ValueError) as exc:
            raise RuntimeError(f"testing command failed before execution: {exc}") from exc

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
            )
        )

    def _write_plan(self, plan: TestingPlan) -> Path:
        path = self.stage_dir / "test_plan.md"
        fs.write_text(path, _render_plan(plan))
        return path

    def _write_plan_json(self, plan: TestingPlan) -> Path:
        path = self.stage_dir / "test_plan.json"
        fs.write_text(
            path,
            json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=False),
        )
        return path

    def _load_prepared_plan(self, fallback_plan: TestingPlan) -> TestingPlan:
        path = self.stage_dir / "test_plan.json"
        if not fs.exists(path):
            return fallback_plan
        try:
            data = json.loads(fs.read_text(path))
            return TestingPlan.model_validate(data)
        except (OSError, json.JSONDecodeError, ValidationError):
            return fallback_plan

    def _write_attempts(self, attempts: list[dict[str, object]]) -> Path:
        path = self.stage_dir / "test_patch_attempts.json"
        fs.write_text(
            path,
            json.dumps({"attempts": attempts}, indent=2, ensure_ascii=False),
        )
        return path

    def _write_changed_files(self, changed_files: list[str]) -> Path:
        path = self.stage_dir / "changed_files.json"
        fs.write_text(
            path,
            json.dumps(
                {"stage": TESTING_STAGE, "changed_files": changed_files},
                indent=2,
                ensure_ascii=False,
            ),
        )
        return path

    def _write_command_record(
        self,
        *,
        command: str,
        executed: bool,
        decision: str,
    ) -> Path:
        path = self.stage_dir / "test_command.json"
        fs.write_text(
            path,
            json.dumps(
                {
                    "command": command,
                    "executed": executed,
                    "decision": decision,
                },
                indent=2,
                ensure_ascii=False,
            ),
        )
        return path

    def _write_test_result(self, payload: dict[str, object]) -> Path:
        path = self.stage_dir / "test_result.json"
        fs.write_text(
            path,
            json.dumps(payload, indent=2, ensure_ascii=False),
        )
        return path

    def _write_test_report(
        self,
        *,
        plan: TestingPlan,
        test_result: dict[str, object],
        command: str,
    ) -> Path:
        path = self.stage_dir / "test_report.md"
        lines = [
            "# Testing Report",
            "",
            "## Target",
            "",
            plan.target_summary,
            "",
            "## Strategy",
            "",
            plan.strategy,
            "",
            "## Command",
            "",
            f"`{command}`",
            "",
            "## Result",
            "",
            f"- Success: {test_result.get('success')}",
            f"- Passed: {test_result.get('passed')}",
            f"- Failed: {test_result.get('failed')}",
            f"- Errors: {test_result.get('errors')}",
            f"- Skipped: {test_result.get('skipped')}",
        ]
        if test_result.get("error_summary"):
            lines.extend(["", "## Failure Summary", "", str(test_result["error_summary"])])
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
            stage=TESTING_STAGE,
            status="failed",
            started_at=started_at,
            ended_at=utc_timestamp(),
            summary=summary,
            artifact_ids=artifact_ids,
            error=ErrorRecord(
                error_id=f"testing_{category}",
                stage=TESTING_STAGE,
                node=TESTING_STAGE,
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
        plan: TestingPlan,
        artifact_ids: list[str],
        attempts: list[dict[str, object]],
        patch_summary: PatchSummary | None = None,
        changed_files: list[str] | None = None,
        test_result: dict[str, object] | None = None,
    ) -> StageResult:
        if fs.exists(self.stage_dir) and not fs.exists(self.stage_dir / "test_report.md"):
            report_payload = test_result or {
                "success": False,
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
                "error_summary": result.summary,
            }
            report_path = self._write_test_report(
                plan=plan,
                test_result=report_payload,
                command=plan.command,
            )
            artifact_ids = [
                *artifact_ids,
                self._record_artifact(
                    "testing_test_report",
                    ArtifactKind.REPORT,
                    report_path,
                    "Testing report",
                ),
            ]
        error = result.error
        if error is not None:
            error = error.model_copy(update={"artifact_ids": artifact_ids})
        finalized = result.model_copy(
            update={
                "artifact_ids": artifact_ids,
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
                stage=TESTING_STAGE,
                kind=kind,
                path=path,
                summary=summary,
            )
        )
        self.run_context.artifact_store.write()
        return artifact_id

    def _register_existing_artifacts(self, fallback_plan: TestingPlan) -> list[str]:
        plan_path = self.stage_dir / "test_plan.md"
        plan_json_path = self.stage_dir / "test_plan.json"
        if not fs.exists(plan_path):
            plan_path = self._write_plan(fallback_plan)
        if not fs.exists(plan_json_path):
            plan_json_path = self._write_plan_json(fallback_plan)
        artifacts = [
            self._record_artifact(
                "testing_test_plan",
                ArtifactKind.REPORT,
                plan_path,
                "Testing plan",
            ),
            self._record_artifact(
                "testing_test_plan_json",
                ArtifactKind.JSON,
                plan_json_path,
                "Structured testing plan",
            ),
        ]
        patch_path = self.stage_dir / "test.patch.diff"
        if fs.exists(patch_path):
            artifacts.append(
                self._record_artifact(
                    "testing_test_patch",
                    ArtifactKind.PATCH,
                    patch_path,
                    "Testing patch diff",
                )
            )
        attempts_path = self.stage_dir / "test_patch_attempts.json"
        if fs.exists(attempts_path):
            artifacts.append(
                self._record_artifact(
                    "testing_test_patch_attempts",
                    ArtifactKind.JSON,
                    attempts_path,
                    "Testing patch validation attempts",
                )
            )
        changed_files_path = self.stage_dir / "changed_files.json"
        if fs.exists(changed_files_path):
            artifacts.append(
                self._record_artifact(
                    "testing_changed_files",
                    ArtifactKind.JSON,
                    changed_files_path,
                    "Testing changed files",
                )
            )
        return artifacts


def _plan_from_payload(payload: dict[str, object] | None) -> TestingPlan | StageResult:
    raw_plan = (payload or {}).get("plan")
    if not isinstance(raw_plan, dict):
        return StageResult(
            stage=TESTING_STAGE,
            status="failed",
            started_at=utc_timestamp(),
            ended_at=utc_timestamp(),
            summary="Testing edit did not include an edited plan.",
            error=ErrorRecord(
                error_id="testing_hitl",
                stage=TESTING_STAGE,
                node="approval_edit",
                category="hitl",
                message="edited_payload.plan is required for edit decisions",
                retryable=True,
            ),
            next_suggestion="Resume with edited_payload.plan or regenerate the testing patch.",
        )
    try:
        return TestingPlan.model_validate(raw_plan)
    except ValidationError as exc:
        return StageResult(
            stage=TESTING_STAGE,
            status="failed",
            started_at=utc_timestamp(),
            ended_at=utc_timestamp(),
            summary="Testing edit payload failed schema validation.",
            error=ErrorRecord(
                error_id="testing_hitl",
                stage=TESTING_STAGE,
                node="approval_edit",
                category="hitl",
                message=str(exc),
                retryable=True,
            ),
            next_suggestion="Provide an edited testing plan that matches the TestingPlan schema.",
        )


def _result_from_non_approve_decision(
    approval: ApprovalDecision,
    *,
    stage: str,
    node: str,
    started_at: str,
) -> StageResult | None:
    if approval.decision_type == "approve":
        return None
    status: Literal["failed", "cancelled"] = (
        "cancelled" if approval.decision_type == "cancel" else "failed"
    )
    return StageResult(
        stage=stage,
        status=status,
        started_at=started_at,
        ended_at=utc_timestamp(),
        summary=f"Testing approval returned {approval.decision_type}.",
        error=ErrorRecord(
            error_id=f"testing_{approval.decision_type}",
            stage=stage,
            node=node,
            category="hitl",
            message=f"testing approval decision: {approval.decision_type}",
            retryable=status != "cancelled",
        ),
        next_suggestion=(
            "Run was cancelled by the approval decision."
            if status == "cancelled"
            else "Revise or approve the testing artifact before continuing."
        ),
    )


def _render_plan(plan: TestingPlan) -> str:
    lines = [
        "# Test Plan",
        "",
        "## Target Summary",
        "",
        plan.target_summary,
        "",
        "## Strategy",
        "",
        plan.strategy,
        "",
        "## Acceptance Criteria",
        "",
    ]
    lines.extend(f"- {item}" for item in plan.acceptance_criteria)
    lines.extend(["", "## Planned Test Changes", ""])
    for change in plan.changes:
        lines.extend(
            [
                f"- `{change.path.as_posix()}`",
                f"  - Rationale: {change.rationale}",
            ]
        )
    lines.extend(["", "## Command", "", f"`{plan.command}`"])
    return "\n".join(lines) + "\n"


def _last_attempt_error(attempts: list[dict[str, object]]) -> str:
    if not attempts:
        return "no test patch candidates were available"
    error = attempts[-1].get("error")
    return str(error or "test patch validation failed")


def _read_attempts(path: Path) -> list[dict[str, object]]:
    if not fs.exists(path):
        return []
    try:
        data = json.loads(fs.read_text(path))
    except (OSError, json.JSONDecodeError):
        return []
    attempts = data.get("attempts") if isinstance(data, dict) else None
    if not isinstance(attempts, list):
        return []
    return [attempt for attempt in attempts if isinstance(attempt, dict)]


def _read_changed_files(path: Path) -> list[str]:
    if not fs.exists(path):
        return []
    try:
        data = json.loads(fs.read_text(path))
    except (OSError, json.JSONDecodeError):
        return []
    changed = data.get("changed_files") if isinstance(data, dict) else None
    if not isinstance(changed, list):
        return []
    return [str(item) for item in changed]


def _normalize_plan_path(path: Path) -> str | None:
    raw = str(path).replace("\\", "/")
    posix_path = PurePosixPath(raw)
    parts = posix_path.parts
    if (
        not raw
        or posix_path.is_absolute()
        or any(part in {"", ".."} for part in parts)
        or (parts and ":" in parts[0])
    ):
        return None
    return posix_path.as_posix()


def _is_allowed_test_path(path: str) -> bool:
    posix = PurePosixPath(path)
    return "tests" in posix.parts or posix.name.startswith("test_")


def _is_hidden_benchmark_path(path: str) -> bool:
    parts = set(PurePosixPath(path).parts)
    return bool(parts & {"evaluation", "oracle_tests"}) or path.endswith(
        "expected_result.json"
    )


def _hidden_command_path_error(command: str) -> str:
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return f"invalid test command: {exc}"
    for arg in argv:
        normalized = arg.strip("'\"").replace("\\", "/")
        candidates = [normalized]
        if "=" in normalized:
            _option, value = normalized.split("=", 1)
            candidates.append(value)
        for candidate in candidates:
            if _is_hidden_benchmark_path(candidate):
                return f"test command references hidden benchmark path: {candidate}"
    return ""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
