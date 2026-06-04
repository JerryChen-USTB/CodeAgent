"""Implementation stage orchestration service."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from codeagent import filesystem as fs
from codeagent.config.schema import Stage
from codeagent.context.sensitive_filter import SensitiveFilter
from codeagent.errors import ErrorRecord
from codeagent.errors.exceptions import utc_timestamp
from codeagent.reports import ArtifactKind, ArtifactRecord
from codeagent.reports.schemas import HumanDecision, StageResult
from codeagent.reports.writer import ReportWriter
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


IMPLEMENTATION_STAGE = "implementation"
PLAN_INTERRUPT_ID = "implementation_plan"
PATCH_INTERRUPT_ID = "implementation_patch"


class ImplementationFileChange(BaseModel):
    """Pure implementation planning item without file contents."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    change_type: Literal["add", "modify", "delete"] = "modify"
    rationale: str = Field(min_length=1, max_length=4000)
    public_interfaces: list[str] = Field(default_factory=list)
    acceptance_notes: list[str] = Field(default_factory=list)


class ImplementationPatchFileChange(BaseModel):
    """Concrete file content generated only after implementation plan approval."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    old_content: str | None = None
    new_content: str | None = None
    rationale: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def require_real_change(self) -> "ImplementationPatchFileChange":
        if self.old_content is None and self.new_content is None:
            raise ValueError("patch file change must include old_content or new_content")
        return self


class ImplementationPlan(BaseModel):
    """Validated pure implementation plan that can be reviewed before code generation."""

    model_config = ConfigDict(extra="forbid")

    requirements_summary: str = Field(min_length=1, max_length=8000)
    implementation_strategy: str = Field(min_length=1, max_length=8000)
    changes: list[ImplementationFileChange] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    risk_notes: list[str] = Field(default_factory=list)

    @property
    def impact_summary(self) -> str:
        """Backward-compatible summary used by existing reporting code."""
        return self.implementation_strategy


class ImplementationPatchDraft(BaseModel):
    """Concrete implementation patch draft generated after plan approval."""

    model_config = ConfigDict(extra="forbid")

    plan_summary: str = Field(min_length=1, max_length=8000)
    changes: list[ImplementationPatchFileChange] = Field(min_length=1)
    syntax_check_targets: list[Path] = Field(default_factory=list)

    @field_validator("syntax_check_targets")
    @classmethod
    def dedupe_syntax_targets(cls, value: list[Path]) -> list[Path]:
        seen: set[str] = set()
        deduped: list[Path] = []
        for target in value:
            key = target.as_posix()
            if key not in seen:
                seen.add(key)
                deduped.append(target)
        return deduped


@dataclass(frozen=True)
class ImplementationRequest:
    """Input for the deterministic implementation stage service."""

    plan: ImplementationPlan
    approval: ApprovalDecision
    plan_review: ApprovalDecision | None = None
    patch_draft: ImplementationPatchDraft | None = None
    alternate_plans: list[ImplementationPlan] = field(default_factory=list)
    alternate_patch_drafts: list[ImplementationPatchDraft] = field(default_factory=list)
    max_patch_attempts: int = 3
    command_timeout_seconds: float | None = None


@dataclass(frozen=True)
class _PreparedPatch:
    plan: ImplementationPlan
    draft: ImplementationPatchDraft
    patch: PatchArtifact
    validation: PatchValidationResult
    summary: PatchSummary


@dataclass(frozen=True)
class _SyntaxCheckOutcome:
    status: Literal["succeeded", "failed", "skipped"]
    log_path: Path
    message: str = ""


@dataclass(frozen=True)
class ImplementationApprovalPreview:
    """Patch approval payload or terminal failure produced before project writes."""

    payload: dict[str, object] | None = None
    result: StageResult | None = None


class ImplementationService:
    """Run implementation-stage patch, approval, syntax, and reporting steps."""

    def __init__(
        self,
        *,
        run_context: RunContext,
        patch_service: PatchService | None = None,
    ) -> None:
        self.run_context = run_context
        self.patch_service = patch_service or PatchService()
        self.stage_dir = run_context.stage_dirs[Stage.IMPLEMENT]
        self.writer = ReportWriter(
            run_dir=run_context.run_dir,
            artifact_store=run_context.artifact_store,
            transcript=run_context.transcript,
            decision_trace=run_context.decision_trace,
            stage_dirs=run_context.stage_dirs,
        )

    def apply_prepared_patch(
        self,
        request: ImplementationRequest,
        *,
        approval: ApprovalDecision,
        approved_patch_sha256: str | None = None,
    ) -> StageResult:
        """Apply the patch that was already generated for an approval interrupt."""
        started_at = utc_timestamp()
        _mkdir(self.stage_dir)
        plan_path = self.stage_dir / "implementation_plan.md"
        patch_path = self.stage_dir / "implementation.patch.diff"
        attempts_path = self.stage_dir / "patch_attempts.json"
        approved_plan = self._load_prepared_plan(request.plan)
        approved_draft = self._load_prepared_patch_draft(request.patch_draft)
        artifacts = self._register_prepared_artifacts(
            plan_path=plan_path,
            patch_path=patch_path,
            attempts_path=attempts_path,
            fallback_plan=approved_plan,
            fallback_draft=approved_draft,
        )

        decision_result = self._handle_patch_decision(approval)
        if decision_result is not None:
            decision_result = decision_result.model_copy(update={"artifact_ids": artifacts})
            return self._finalize_result(
                decision_result,
                plan=approved_plan,
                patch_draft=approved_draft,
                artifact_ids=artifacts,
                attempts=_read_attempts(attempts_path),
            )

        if not fs.exists(patch_path):
            result = self._build_failed_result(
                started_at=started_at,
                summary="Approved implementation patch is missing.",
                category="patch",
                message="implementation.patch.diff was not found at resume time",
                artifact_ids=artifacts,
                next_suggestion="Regenerate the implementation patch and request approval again.",
            )
            return self._finalize_result(
                result,
                plan=approved_plan,
                patch_draft=approved_draft,
                artifact_ids=artifacts,
                attempts=_read_attempts(attempts_path),
            )
        patch_text = fs.read_text(patch_path)
        if approved_patch_sha256 and _sha256_text(patch_text) != approved_patch_sha256:
            result = self._build_failed_result(
                started_at=started_at,
                summary="Approved implementation patch changed before application.",
                category="patch",
                message="approved patch hash mismatch",
                artifact_ids=artifacts,
                next_suggestion="Review and approve the current implementation patch before applying it.",
            )
            return self._finalize_result(
                result,
                plan=approved_plan,
                patch_draft=approved_draft,
                artifact_ids=artifacts,
                attempts=_read_attempts(attempts_path),
            )

        validation = self.patch_service.validate_patch(
            patch_path,
            self.run_context.task_config.project_path,
        )
        if not validation.valid:
            result = self._build_failed_result(
                started_at=started_at,
                summary="Approved implementation patch failed validation at resume time.",
                category="patch",
                message="; ".join(validation.errors),
                artifact_ids=artifacts,
                next_suggestion="Regenerate the implementation patch against the current project files.",
            )
            return self._finalize_result(
                result,
                plan=approved_plan,
                patch_draft=approved_draft,
                artifact_ids=artifacts,
                attempts=_read_attempts(attempts_path),
            )
        summary = self.patch_service.summarize_patch(patch_path)
        try:
            applied = self.patch_service.apply_patch(
                patch_path,
                self.run_context.task_config.project_path,
                operation_id="implementation_apply_patch",
            )
        except (PatchApplyError, PatchValidationError) as exc:
            result = self._build_failed_result(
                started_at=started_at,
                summary="Approved implementation patch could not be applied.",
                category="patch",
                message=str(exc),
                artifact_ids=artifacts,
                next_suggestion="Regenerate the implementation patch against the current project files.",
            )
            return self._finalize_result(
                result,
                plan=approved_plan,
                patch_draft=approved_draft,
                artifact_ids=artifacts,
                attempts=_read_attempts(attempts_path),
                patch_summary=summary,
            )

        changed_files_path = self._write_changed_files(applied.changed_files)
        artifacts.append(
            self._record_artifact(
                "implementation_changed_files",
                ArtifactKind.JSON,
                changed_files_path,
                "Implementation changed files",
            )
        )
        syntax = self._run_syntax_check(approved_draft, applied.changed_files, request)
        artifacts.append(
            self._record_artifact(
                "implementation_syntax_log",
                ArtifactKind.LOG,
                syntax.log_path,
                "Implementation syntax check log",
            )
        )
        if syntax.status == "failed":
            result = self._build_failed_result(
                started_at=started_at,
                summary="Approved implementation patch applied but syntax check failed.",
                category="shell",
                message=syntax.message or "syntax check command failed",
                artifact_ids=artifacts,
                next_suggestion="Enter debugging or regenerate the implementation patch to fix syntax errors.",
            )
        else:
            result = StageResult(
                stage=IMPLEMENTATION_STAGE,
                status="succeeded",
                started_at=started_at,
                ended_at=utc_timestamp(),
                summary="Approved implementation patch applied and syntax check completed.",
                artifact_ids=artifacts,
            )
        return self._finalize_result(
            result,
            plan=approved_plan,
            patch_draft=approved_draft,
            artifact_ids=artifacts,
            attempts=_read_attempts(attempts_path),
            patch_summary=summary,
            changed_files=applied.changed_files,
            syntax=syntax,
        )

    def prepare_plan_review(self, request: ImplementationRequest) -> ImplementationApprovalPreview:
        """Write the implementation plan and return a review payload before patch generation."""
        _mkdir(self.stage_dir)
        plan_path = self._write_plan(request.plan)
        plan_json_path = self._write_plan_json(request.plan)
        artifacts = [
            self._record_artifact(
                "implementation_plan",
                ArtifactKind.REPORT,
                plan_path,
                "Implementation plan",
            ),
            self._record_artifact(
                "implementation_plan_json",
                ArtifactKind.JSON,
                plan_json_path,
                "Structured implementation plan",
            ),
        ]
        return ImplementationApprovalPreview(
            payload={
                "interrupt_id": PLAN_INTERRUPT_ID,
                "action": "review_implementation_plan",
                "title": "实施此实现计划？",
                "summary": request.plan.impact_summary,
                "risk_level": "medium",
                "allowed_decisions": ["approve", "respond"],
                "default_decision": "approve",
                "payload": {
                    "plan_path": "implementation/implementation_plan.md",
                    "plan_json_path": "implementation/implementation_plan.json",
                    "requirements_summary": request.plan.requirements_summary,
                    "impact_summary": request.plan.impact_summary,
                    "changed_files": [
                        change.path.as_posix() for change in request.plan.changes
                    ],
                    "artifact_ids": artifacts,
                },
            }
        )

    def apply_plan_review_decision(
        self,
        request: ImplementationRequest,
        *,
        approval: ApprovalDecision,
    ) -> ImplementationRequest | StageResult:
        """Record an implementation-plan decision and return the approved request."""
        started_at = utc_timestamp()
        if approval.interrupt_id != PLAN_INTERRUPT_ID:
            return self._build_failed_result(
                started_at=started_at,
                summary="Implementation plan review did not match the expected interrupt.",
                category="hitl",
                message="approval decision interrupt_id does not match implementation plan",
                artifact_ids=[],
                next_suggestion="Resume with a decision for the implementation_plan interrupt.",
            )
        self._record_plan_decision(approval)
        if approval.decision_type == "approve":
            return replace(request, plan_review=approval)
        if approval.decision_type == "edit":
            raw_plan = (approval.edited_payload or {}).get("plan")
            if not isinstance(raw_plan, dict):
                return self._build_failed_result(
                    started_at=started_at,
                    summary="Implementation plan edit did not include an edited plan.",
                    category="hitl",
                    message="edited_payload.plan is required for edit decisions",
                    artifact_ids=[],
                    next_suggestion="Resume with edited_payload.plan or ask the Agent to regenerate.",
                )
            try:
                edited_plan = ImplementationPlan.model_validate(raw_plan)
            except ValidationError as exc:
                return self._build_failed_result(
                    started_at=started_at,
                    summary="Implementation plan edit payload failed schema validation.",
                    category="hitl",
                    message=str(exc),
                    artifact_ids=[],
                    next_suggestion="Provide an edited implementation plan that matches the schema.",
                )
            return replace(request, plan=edited_plan, plan_review=approval)
        status: Literal["failed", "cancelled"] = (
            "cancelled" if approval.decision_type == "cancel" else "failed"
        )
        return StageResult(
            stage=IMPLEMENTATION_STAGE,
            status=status,
            started_at=started_at,
            ended_at=utc_timestamp(),
            summary=f"Implementation plan review returned {approval.decision_type}.",
            error=ErrorRecord(
                error_id=f"implementation_plan_{approval.decision_type}",
                stage=IMPLEMENTATION_STAGE,
                node="review_implementation_plan",
                category="hitl",
                message=f"implementation plan decision: {approval.decision_type}",
                retryable=status != "cancelled",
            ),
            next_suggestion=(
                "Run was cancelled by the approval decision."
                if status == "cancelled"
                else "Ask the Agent to regenerate or approve the implementation plan."
            ),
        )

    def prepare_approval(self, request: ImplementationRequest) -> ImplementationApprovalPreview:
        """Prepare implementation artifacts and return a LangGraph interrupt payload."""
        started_at = utc_timestamp()
        _mkdir(self.stage_dir)
        if request.patch_draft is None:
            plan_path = self._write_plan(request.plan)
            artifacts = [
                self._record_artifact(
                    "implementation_plan",
                    ArtifactKind.REPORT,
                    plan_path,
                    "Implementation plan",
                )
            ]
            result = self._build_failed_result(
                started_at=started_at,
                summary="Implementation patch draft is missing.",
                category="model",
                message="implementation patch generation must run after plan approval",
                artifact_ids=artifacts,
                next_suggestion="Generate an ImplementationPatchDraft from the approved plan.",
            )
            finalized = self._finalize_result(
                result,
                plan=request.plan,
                patch_draft=None,
                artifact_ids=artifacts,
                attempts=[],
            )
            return ImplementationApprovalPreview(result=finalized)

        candidates = [request.patch_draft, *request.alternate_patch_drafts][
            : max(1, request.max_patch_attempts)
        ]
        prepared, attempts = self._prepare_patch_candidates(request.plan, candidates)
        artifacts: list[str] = []
        if prepared is None:
            plan_path = self._write_plan(request.plan)
            draft_json_path = self._write_patch_draft_json(candidates[0])
            attempts_path = self._write_attempts(attempts)
            artifacts.extend(
                [
                    self._record_artifact(
                        "implementation_plan",
                        ArtifactKind.REPORT,
                        plan_path,
                        "Implementation plan for the first patch candidate",
                    ),
                    self._record_artifact(
                        "implementation_patch_draft_json",
                        ArtifactKind.JSON,
                        draft_json_path,
                        "Structured implementation patch draft",
                    ),
                    self._record_artifact(
                        "implementation_patch_attempts",
                        ArtifactKind.JSON,
                        attempts_path,
                        "Implementation patch validation attempts",
                    ),
                ]
            )
            result = self._build_failed_result(
                started_at=started_at,
                summary="Implementation patch validation failed before approval.",
                category="patch",
                message=_last_attempt_error(attempts),
                artifact_ids=artifacts,
                next_suggestion="Revise the implementation plan or patch candidate and retry.",
            )
            finalized = self._finalize_result(
                result,
                plan=request.plan,
                patch_draft=candidates[0],
                artifact_ids=artifacts,
                attempts=attempts,
            )
            return ImplementationApprovalPreview(result=finalized)

        plan_path = self._write_plan(prepared.plan)
        plan_json_path = self._write_plan_json(prepared.plan)
        draft_json_path = self._write_patch_draft_json(prepared.draft)
        patch_path = self.stage_dir / "implementation.patch.diff"
        _write_text(patch_path, prepared.patch.text)
        attempts_path = self._write_attempts(attempts)
        artifacts.extend(
            [
                self._record_artifact(
                    "implementation_plan",
                    ArtifactKind.REPORT,
                    plan_path,
                    "Implementation plan",
                ),
                self._record_artifact(
                    "implementation_plan_json",
                    ArtifactKind.JSON,
                    plan_json_path,
                    "Structured implementation plan",
                ),
                self._record_artifact(
                    "implementation_patch_draft_json",
                    ArtifactKind.JSON,
                    draft_json_path,
                    "Structured implementation patch draft",
                ),
                self._record_artifact(
                    "implementation_patch",
                    ArtifactKind.PATCH,
                    patch_path,
                    "Implementation patch diff",
                ),
                self._record_artifact(
                    "implementation_patch_attempts",
                    ArtifactKind.JSON,
                    attempts_path,
                    "Implementation patch validation attempts",
                ),
            ]
        )
        payload: dict[str, object] = {
            "interrupt_id": PATCH_INTERRUPT_ID,
            "action": "approve_implementation_patch",
            "title": "应用此实现补丁？",
            "summary": "在修改项目文件前审查生成的实现补丁。",
            "risk_level": prepared.validation.risk_report.level,
            "allowed_decisions": ["approve", "respond"],
            "default_decision": "approve",
            "payload": {
                "plan_path": "implementation/implementation_plan.md",
                "plan_json_path": "implementation/implementation_plan.json",
                "patch_path": "implementation/implementation.patch.diff",
                "patch_draft_json_path": "implementation/implementation_patch_draft.json",
                "changed_files": prepared.validation.changed_files,
                "added_lines": prepared.summary.added_lines,
                "removed_lines": prepared.summary.removed_lines,
                "risk_level": prepared.summary.risk_level,
                "patch_sha256": _sha256_text(prepared.patch.text),
                "patch_attempts_path": "implementation/patch_attempts.json",
                "artifact_ids": artifacts,
            },
        }
        return ImplementationApprovalPreview(payload=payload)

    def run(self, request: ImplementationRequest) -> StageResult:
        started_at = utc_timestamp()
        edited = self._request_from_edit_decision(request, started_at=started_at)
        if isinstance(edited, StageResult):
            return self._finalize_result(
                edited,
                plan=request.plan,
                artifact_ids=[],
                attempts=[],
            )
        if edited is not None:
            return self.run(edited)

        _mkdir(self.stage_dir)
        if request.patch_draft is None:
            result = self._build_failed_result(
                started_at=started_at,
                summary="Implementation patch draft is missing.",
                category="model",
                message="implementation patch generation must run after plan approval",
                artifact_ids=[],
                next_suggestion="Generate an ImplementationPatchDraft from the approved plan.",
            )
            return self._finalize_result(
                result,
                plan=request.plan,
                patch_draft=None,
                artifact_ids=[],
                attempts=[],
            )
        candidates = [request.patch_draft, *request.alternate_patch_drafts][
            : max(1, request.max_patch_attempts)
        ]
        prepared, attempts = self._prepare_patch_candidates(request.plan, candidates)
        artifacts: list[str] = []

        if prepared is None:
            plan_path = self._write_plan(request.plan)
            draft_json_path = self._write_patch_draft_json(candidates[0])
            attempts_path = self._write_attempts(attempts)
            artifacts.extend(
                [
                    self._record_artifact(
                        "implementation_plan",
                        ArtifactKind.REPORT,
                        plan_path,
                        "Implementation plan for the first patch candidate",
                    ),
                    self._record_artifact(
                        "implementation_patch_draft_json",
                        ArtifactKind.JSON,
                        draft_json_path,
                        "Structured implementation patch draft",
                    ),
                    self._record_artifact(
                        "implementation_patch_attempts",
                        ArtifactKind.JSON,
                        attempts_path,
                        "Implementation patch validation attempts",
                    ),
                ]
            )
            result = self._build_failed_result(
                started_at=started_at,
                summary="Implementation patch validation failed before approval.",
                category="patch",
                message=_last_attempt_error(attempts),
                artifact_ids=artifacts,
                next_suggestion="Revise the implementation plan or patch candidate and retry.",
            )
            return self._finalize_result(
                result,
                plan=request.plan,
                patch_draft=candidates[0],
                artifact_ids=artifacts,
                attempts=attempts,
            )

        plan_path = self._write_plan(prepared.plan)
        draft_json_path = self._write_patch_draft_json(prepared.draft)
        patch_path = self.stage_dir / "implementation.patch.diff"
        _write_text(patch_path, prepared.patch.text)
        attempts_path = self._write_attempts(attempts)
        artifacts.extend(
            [
                self._record_artifact(
                    "implementation_plan",
                    ArtifactKind.REPORT,
                    plan_path,
                    "Implementation plan",
                ),
                self._record_artifact(
                    "implementation_patch",
                    ArtifactKind.PATCH,
                    patch_path,
                    "Implementation patch diff",
                ),
                self._record_artifact(
                    "implementation_patch_draft_json",
                    ArtifactKind.JSON,
                    draft_json_path,
                    "Structured implementation patch draft",
                ),
                self._record_artifact(
                    "implementation_patch_attempts",
                    ArtifactKind.JSON,
                    attempts_path,
                    "Implementation patch validation attempts",
                ),
            ]
        )

        decision_result = self._handle_patch_decision(request.approval)
        if decision_result is not None:
            decision_result = decision_result.model_copy(update={"artifact_ids": artifacts})
            return self._finalize_result(
                decision_result,
                plan=prepared.plan,
                patch_draft=prepared.draft,
                artifact_ids=artifacts,
                attempts=attempts,
                patch_summary=prepared.summary,
            )

        try:
            applied = self.patch_service.apply_patch(
                patch_path,
                self.run_context.task_config.project_path,
                operation_id="implementation_apply_patch",
            )
        except (PatchApplyError, PatchValidationError) as exc:
            result = self._build_failed_result(
                started_at=started_at,
                summary="Implementation patch could not be applied.",
                category="patch",
                message=str(exc),
                artifact_ids=artifacts,
                next_suggestion="Regenerate the implementation patch against the current project files.",
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
                "implementation_changed_files",
                ArtifactKind.JSON,
                changed_files_path,
                "Implementation changed files",
            )
        )

        syntax = self._run_syntax_check(prepared.draft, applied.changed_files, request)
        artifacts.append(
            self._record_artifact(
                "implementation_syntax_log",
                ArtifactKind.LOG,
                syntax.log_path,
                "Implementation syntax check log",
            )
        )

        if syntax.status == "failed":
            result = self._build_failed_result(
                started_at=started_at,
                summary="Implementation patch applied but syntax check failed.",
                category="shell",
                message=syntax.message or "syntax check command failed",
                artifact_ids=artifacts,
                next_suggestion="Enter debugging or regenerate the implementation patch to fix syntax errors.",
            )
        else:
            result = StageResult(
                stage=IMPLEMENTATION_STAGE,
                status="succeeded",
                started_at=started_at,
                ended_at=utc_timestamp(),
                summary="Implementation patch applied and syntax check completed.",
                artifact_ids=artifacts,
            )
        return self._finalize_result(
            result,
            plan=prepared.plan,
            patch_draft=prepared.draft,
            artifact_ids=artifacts,
            attempts=attempts,
            patch_summary=prepared.summary,
            changed_files=applied.changed_files,
            syntax=syntax,
        )

    def _request_from_edit_decision(
        self,
        request: ImplementationRequest,
        *,
        started_at: str,
    ) -> ImplementationRequest | StageResult | None:
        approval = request.approval
        if approval.decision_type != "edit":
            return None
        self._record_approval_decision(approval)
        edited_payload = approval.edited_payload or {}
        raw_draft = edited_payload.get("patch_draft") or edited_payload.get("plan")
        if not isinstance(raw_draft, dict):
            return self._build_failed_result(
                started_at=started_at,
                summary="Implementation patch edit did not include an edited patch draft.",
                category="hitl",
                message="edited_payload.patch_draft is required for edit decisions",
                artifact_ids=[],
                next_suggestion="Resume with edited_payload.patch_draft or regenerate the implementation patch.",
            )
        try:
            edited_draft = ImplementationPatchDraft.model_validate(raw_draft)
        except ValidationError as exc:
            return self._build_failed_result(
                started_at=started_at,
                summary="Implementation patch draft edit payload failed schema validation.",
                category="hitl",
                message=str(exc),
                artifact_ids=[],
                next_suggestion="Provide an edited implementation patch draft that matches the schema.",
            )
        return ImplementationRequest(
            plan=request.plan,
            approval=ApprovalDecision(
                interrupt_id=approval.interrupt_id,
                decision_type="approve",
                comment=approval.comment or "Apply edited implementation patch draft.",
                decided_by=approval.decided_by,
                auto=approval.auto,
            ),
            plan_review=request.plan_review,
            patch_draft=edited_draft,
            alternate_plans=[],
            alternate_patch_drafts=[],
            max_patch_attempts=request.max_patch_attempts,
            command_timeout_seconds=request.command_timeout_seconds,
        )

    def _prepare_patch_candidates(
        self,
        plan: ImplementationPlan,
        candidates: list[ImplementationPatchDraft],
    ) -> tuple[_PreparedPatch | None, list[dict[str, object]]]:
        attempts: list[dict[str, object]] = []
        for index, draft in enumerate(candidates, start=1):
            try:
                precheck_error = self._precheck_patch_targets(draft)
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
                candidate_patch_path = self.stage_dir / f"implementation_attempt_{index}.patch.diff"
                _write_text(candidate_patch_path, patch.text)
                validation = self.patch_service.validate_patch(
                    candidate_patch_path,
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
                summary = self.patch_service.summarize_patch(candidate_patch_path)
                attempts.append(
                    {
                        "attempt": index,
                        "status": "valid",
                        "changed_files": validation.changed_files,
                        "warnings": validation.warnings,
                        "risk_level": validation.risk_report.level,
                    }
                )
                return _PreparedPatch(
                    plan=plan,
                    draft=draft,
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

    def _precheck_patch_targets(self, draft: ImplementationPatchDraft) -> str:
        root = self.run_context.task_config.project_path.resolve()
        errors: list[str] = []
        sensitive_filter = SensitiveFilter(root)
        for change in draft.changes:
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
        return "; ".join(errors)

    def _file_changes_for_patch_draft(self, draft: ImplementationPatchDraft) -> list[FileChange]:
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
        if not fs.is_file(target):
            return None
        return fs.read_text(target)

    def _handle_patch_decision(self, approval: ApprovalDecision) -> StageResult | None:
        if approval.interrupt_id != PATCH_INTERRUPT_ID:
            return self._build_failed_result(
                started_at=utc_timestamp(),
                summary="Implementation patch approval did not match the expected interrupt.",
                category="hitl",
                message="approval decision interrupt_id does not match implementation patch",
                artifact_ids=[],
                next_suggestion="Resume with a decision for the implementation_patch interrupt.",
            )
        self._record_approval_decision(approval)
        if approval.decision_type == "approve":
            return None
        status: Literal["failed", "cancelled"] = (
            "cancelled" if approval.decision_type == "cancel" else "failed"
        )
        next_suggestion = (
            "Run was cancelled by the approval decision."
            if status == "cancelled"
            else "Regenerate or revise the implementation patch before applying changes."
        )
        return StageResult(
            stage=IMPLEMENTATION_STAGE,
            status=status,
            started_at=utc_timestamp(),
            ended_at=utc_timestamp(),
            summary=f"Implementation patch approval returned {approval.decision_type}.",
            error=ErrorRecord(
                error_id=f"implementation_{approval.decision_type}",
                stage=IMPLEMENTATION_STAGE,
                node="approve_patch",
                category="hitl",
                message=f"implementation approval decision: {approval.decision_type}",
                retryable=status != "cancelled",
            ),
            next_suggestion=next_suggestion,
        )

    def _record_approval_decision(self, approval: ApprovalDecision) -> None:
        self.writer.record_human_decision(
            HumanDecision(
                interrupt_id=approval.interrupt_id,
                action="approve_implementation_patch",
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
            stage=IMPLEMENTATION_STAGE,
            action="approve_implementation_patch",
            interrupt_id=approval.interrupt_id,
            decision_type=approval.decision_type,
            auto=approval.auto,
            decision_source=approval.decision_source,
            presented_to_user=approval.presented_to_user,
            decided_by=approval.decided_by,
            comment=approval.comment,
        )

    def _record_plan_decision(self, approval: ApprovalDecision) -> None:
        self.writer.record_human_decision(
            HumanDecision(
                interrupt_id=approval.interrupt_id,
                action="review_implementation_plan",
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
            stage=IMPLEMENTATION_STAGE,
            action="review_implementation_plan",
            interrupt_id=approval.interrupt_id,
            decision_type=approval.decision_type,
            auto=approval.auto,
            decision_source=approval.decision_source,
            presented_to_user=approval.presented_to_user,
            decided_by=approval.decided_by,
            comment=approval.comment,
        )

    def _run_syntax_check(
        self,
        patch_draft: ImplementationPatchDraft | None,
        changed_files: list[str],
        request: ImplementationRequest,
    ) -> _SyntaxCheckOutcome:
        log_path = self.stage_dir / "syntax_check.log"
        targets = self._syntax_targets(patch_draft, changed_files)
        if not targets:
            _write_text(
                log_path,
                "command: <none>\nexit_code: skipped\nNo Python syntax targets.\n",
            )
            return _SyntaxCheckOutcome(status="skipped", log_path=log_path)

        _ = request
        command = "internal compile() syntax check " + " ".join(targets)
        started = time.perf_counter()
        errors: list[str] = []
        for target in targets:
            target_path = self.run_context.task_config.project_path / Path(target)
            try:
                source = fs.read_text(target_path)
                compile(source, target, "exec", dont_inherit=True)
            except (OSError, SyntaxError, ValueError) as exc:
                errors.append(f"{target}: {type(exc).__name__}: {exc}")
        duration = time.perf_counter() - started
        exit_code = 1 if errors else 0
        stdout = f"Checked {len(targets)} Python file(s)." if not errors else ""
        stderr = "\n".join(errors)

        _write_text(
            log_path,
            _render_syntax_log(
                command=command,
                exit_code=exit_code,
                duration_seconds=duration,
                stdout=stdout,
                stderr=stderr,
            ),
        )
        if exit_code != 0:
            return _SyntaxCheckOutcome(
                status="failed",
                log_path=log_path,
                message=f"syntax check exit_code={exit_code}",
            )
        return _SyntaxCheckOutcome(status="succeeded", log_path=log_path)

    def _syntax_targets(
        self,
        patch_draft: ImplementationPatchDraft | None,
        changed_files: list[str],
    ) -> list[str]:
        raw_targets = (
            patch_draft.syntax_check_targets
            if patch_draft is not None and patch_draft.syntax_check_targets
            else [Path(path) for path in changed_files]
        )
        targets: list[str] = []
        changed_set = {Path(path).as_posix() for path in changed_files}
        for target in raw_targets:
            target_posix = Path(target).as_posix()
            if not target_posix.endswith(".py"):
                continue
            if changed_set and target_posix not in changed_set:
                continue
            targets.append(target_posix)
        return sorted(set(targets))

    def _write_plan(self, plan: ImplementationPlan) -> Path:
        path = self.stage_dir / "implementation_plan.md"
        _write_text(path, _render_plan(plan))
        return path

    def _write_plan_json(self, plan: ImplementationPlan) -> Path:
        path = self.stage_dir / "implementation_plan.json"
        _write_text(
            path,
            json.dumps(
                plan.model_dump(mode="json"),
                indent=2,
                ensure_ascii=False,
            ),
        )
        return path

    def _write_patch_draft_json(self, draft: ImplementationPatchDraft) -> Path:
        path = self.stage_dir / "implementation_patch_draft.json"
        _write_text(
            path,
            json.dumps(draft.model_dump(mode="json"), indent=2, ensure_ascii=False),
        )
        return path

    def _load_prepared_plan(self, fallback_plan: ImplementationPlan) -> ImplementationPlan:
        path = self.stage_dir / "implementation_plan.json"
        if not fs.exists(path):
            return fallback_plan
        try:
            data = json.loads(fs.read_text(path))
            return ImplementationPlan.model_validate(data)
        except (OSError, json.JSONDecodeError, ValidationError):
            return fallback_plan

    def _load_prepared_patch_draft(
        self,
        fallback_draft: ImplementationPatchDraft | None,
    ) -> ImplementationPatchDraft | None:
        path = self.stage_dir / "implementation_patch_draft.json"
        if not fs.exists(path):
            return fallback_draft
        try:
            data = json.loads(fs.read_text(path))
            return ImplementationPatchDraft.model_validate(data)
        except (OSError, json.JSONDecodeError, ValidationError):
            return fallback_draft

    def _write_attempts(self, attempts: list[dict[str, object]]) -> Path:
        path = self.stage_dir / "patch_attempts.json"
        _write_text(path, json.dumps({"attempts": attempts}, indent=2, ensure_ascii=False))
        return path

    def _write_changed_files(self, changed_files: list[str]) -> Path:
        path = self.stage_dir / "changed_files.json"
        _write_text(
            path,
            json.dumps(
                {
                    "stage": IMPLEMENTATION_STAGE,
                    "changed_files": changed_files,
                },
                indent=2,
                ensure_ascii=False,
            ),
        )
        return path

    def _write_implementation_report(
        self,
        result: StageResult,
        *,
        plan: ImplementationPlan,
        patch_draft: ImplementationPatchDraft | None = None,
        attempts: list[dict[str, object]],
        patch_summary: PatchSummary | None = None,
        changed_files: list[str] | None = None,
        syntax: _SyntaxCheckOutcome | None = None,
    ) -> Path:
        path = self.stage_dir / "implementation_report.md"
        lines = [
            "# Implementation Report",
            "",
            "## Status",
            "",
            f"- Status: {result.status}",
            f"- Summary: {result.summary}",
            "",
            "## Requirements Summary",
            "",
            plan.requirements_summary,
            "",
            "## Project Impact",
            "",
            plan.impact_summary,
            "",
            "## Acceptance Criteria",
            "",
            *(f"- {item}" for item in plan.acceptance_criteria),
            "",
            "## Patch Attempts",
            "",
        ]
        if patch_draft is not None:
            lines.extend(
                [
                    "## Patch Draft",
                    "",
                    patch_draft.plan_summary,
                    "",
                    "### Draft Files",
                    "",
                    *(f"- `{change.path.as_posix()}`: {change.rationale}" for change in patch_draft.changes),
                    "",
                ]
            )
        for attempt in attempts:
            lines.append(
                f"- Attempt {attempt.get('attempt')}: {attempt.get('status')}"
                + (f" - {attempt.get('error')}" if attempt.get("error") else "")
            )
        if patch_summary is not None:
            lines.extend(
                [
                    "",
                    "## Patch Summary",
                    "",
                    f"- Files: {', '.join(patch_summary.changed_files) or '-'}",
                    f"- Added lines: {patch_summary.added_lines}",
                    f"- Removed lines: {patch_summary.removed_lines}",
                    f"- Risk level: {patch_summary.risk_level}",
                ]
            )
        if changed_files is not None:
            lines.extend(
                [
                    "",
                    "## Changed Files",
                    "",
                    *(f"- {path}" for path in changed_files),
                ]
            )
        if syntax is not None:
            lines.extend(
                [
                    "",
                    "## Syntax Check",
                    "",
                    f"- Status: {syntax.status}",
                    f"- Log: {syntax.log_path.name}",
                ]
            )
        if result.next_suggestion:
            lines.extend(["", "## Next Suggestion", "", result.next_suggestion])
        _write_text(path, "\n".join(lines) + "\n")
        return path

    def _build_failed_result(
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
            stage=IMPLEMENTATION_STAGE,
            status="failed",
            started_at=started_at,
            ended_at=utc_timestamp(),
            summary=summary,
            artifact_ids=artifact_ids,
            error=ErrorRecord(
                error_id=f"implementation_{category}",
                stage=IMPLEMENTATION_STAGE,
                node=IMPLEMENTATION_STAGE,
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
        plan: ImplementationPlan,
        patch_draft: ImplementationPatchDraft | None,
        artifact_ids: list[str],
        attempts: list[dict[str, object]],
        patch_summary: PatchSummary | None = None,
        changed_files: list[str] | None = None,
        syntax: _SyntaxCheckOutcome | None = None,
    ) -> StageResult:
        report_path = self._write_implementation_report(
            result,
            plan=plan,
            patch_draft=patch_draft,
            attempts=attempts,
            patch_summary=patch_summary,
            changed_files=changed_files,
            syntax=syntax,
        )
        report_id = self._record_artifact(
            "implementation_report",
            ArtifactKind.REPORT,
            report_path,
            "Implementation narrative report",
        )
        artifact_ids = [*artifact_ids, report_id]
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
        self.run_context.workflow_trace.record(
            "stage_finalized",
            stage=IMPLEMENTATION_STAGE,
            status=finalized.status,
            summary=finalized.summary,
            artifact_ids=artifact_ids,
            attempts=attempts,
            changed_files=changed_files or [],
            syntax_status=syntax.status if syntax is not None else None,
            patch_draft_present=patch_draft is not None,
            patch_summary=patch_summary.__dict__ if patch_summary is not None else None,
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
                stage=IMPLEMENTATION_STAGE,
                kind=kind,
                path=path,
                summary=summary,
            )
        )
        self.run_context.artifact_store.write()
        return artifact_id

    def _register_prepared_artifacts(
        self,
        *,
        plan_path: Path,
        patch_path: Path,
        attempts_path: Path,
        fallback_plan: ImplementationPlan,
        fallback_draft: ImplementationPatchDraft | None = None,
    ) -> list[str]:
        if not fs.exists(plan_path):
            plan_path = self._write_plan(fallback_plan)
        plan_json_path = self.stage_dir / "implementation_plan.json"
        if not fs.exists(plan_json_path):
            plan_json_path = self._write_plan_json(fallback_plan)
        artifacts = [
            self._record_artifact(
                "implementation_plan",
                ArtifactKind.REPORT,
                plan_path,
                "Implementation plan",
            )
        ]
        artifacts.append(
            self._record_artifact(
                "implementation_plan_json",
                ArtifactKind.JSON,
                plan_json_path,
                "Structured implementation plan",
            )
        )
        draft_json_path = self.stage_dir / "implementation_patch_draft.json"
        if fallback_draft is not None and not fs.exists(draft_json_path):
            draft_json_path = self._write_patch_draft_json(fallback_draft)
        if fs.exists(draft_json_path):
            artifacts.append(
                self._record_artifact(
                    "implementation_patch_draft_json",
                    ArtifactKind.JSON,
                    draft_json_path,
                    "Structured implementation patch draft",
                )
            )
        
        if fs.exists(patch_path):
            artifacts.append(
                self._record_artifact(
                    "implementation_patch",
                    ArtifactKind.PATCH,
                    patch_path,
                    "Implementation patch diff",
                )
            )
        if fs.exists(attempts_path):
            artifacts.append(
                self._record_artifact(
                    "implementation_patch_attempts",
                    ArtifactKind.JSON,
                    attempts_path,
                    "Implementation patch validation attempts",
                )
            )
        return artifacts


def _render_plan(plan: ImplementationPlan) -> str:
    lines = [
        "# Implementation Plan",
        "",
        "## Requirements Summary",
        "",
        plan.requirements_summary,
        "",
        "## Implementation Strategy",
        "",
        plan.implementation_strategy,
        "",
        "## Planned Changes",
        "",
    ]
    for change in plan.changes:
        lines.extend(
            [
                f"- `{change.path.as_posix()}`",
                f"  - Change type: {change.change_type}",
                f"  - Rationale: {change.rationale}",
            ]
        )
        if change.public_interfaces:
            lines.extend(f"  - Interface: {item}" for item in change.public_interfaces)
        if change.acceptance_notes:
            lines.extend(f"  - Acceptance note: {item}" for item in change.acceptance_notes)
    lines.extend(["", "## Acceptance Criteria", ""])
    lines.extend(f"- {item}" for item in plan.acceptance_criteria)
    if plan.risk_notes:
        lines.extend(["", "## Risks", ""])
        lines.extend(f"- {item}" for item in plan.risk_notes)
    return "\n".join(lines) + "\n"


def _render_syntax_log(
    *,
    command: str,
    exit_code: int,
    duration_seconds: float,
    stdout: str,
    stderr: str,
) -> str:
    return "\n".join(
        [
            f"command: {command}",
            f"exit_code: {exit_code}",
            "timed_out: False",
            f"duration_seconds: {duration_seconds:.3f}",
            "stdout_log: <internal>",
            "stderr_log: <internal>",
            "",
            "stdout:",
            stdout.rstrip(),
            "",
            "stderr:",
            stderr.rstrip(),
            "",
        ]
    )


def _last_attempt_error(attempts: list[dict[str, object]]) -> str:
    if not attempts:
        return "no patch candidates were available"
    error = attempts[-1].get("error")
    return str(error or "patch validation failed")


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


def _mkdir(path: Path) -> None:
    fs.mkdir(path)


def _write_text(path: Path, text: str) -> None:
    fs.write_text(path, text)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


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
