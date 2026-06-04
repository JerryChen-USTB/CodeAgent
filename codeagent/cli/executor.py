"""Execute normalized TaskConfig objects from CLI commands."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from codeagent import filesystem as fs
from codeagent.adapters.test_result import TestResult
from codeagent.agents.plan_generation import PlanGenerationError, PlanGenerationService
from codeagent.config.schema import Stage, TaskConfig
from codeagent.context.redaction import redact_sensitive_text
from codeagent.errors.exceptions import ErrorRecord, utc_timestamp
from codeagent.reports import ArtifactKind, ArtifactRecord, ReportWriter, StageResult
from codeagent.reports.schemas import HumanDecision
from codeagent.runtime.commands import CommandApproval, ShellResult
from codeagent.runtime.run_context import RunContext, create_run_context
from codeagent.stages.debugging_service import (
    DEBUGGING_STAGE,
    REPRODUCTION_COMMAND_INTERRUPT_ID,
    DebuggingRequest,
    DebuggingService,
)
from codeagent.stages.implementation_service import (
    PLAN_INTERRUPT_ID as IMPLEMENTATION_PLAN_INTERRUPT_ID,
    ImplementationRequest,
    ImplementationService,
)
from codeagent.stages.repair_service import RepairRequest, RepairService
from codeagent.stages.testing_service import TestingRequest, TestingService
from codeagent.tools.hitl import ApprovalDecision
from codeagent.tools.pytest_tools import parse_shell_result
from codeagent.tools.shell_tools import CommandDeniedError, ShellRunner
from codeagent.workflow.checkpoint import CheckpointManager
from codeagent.workflow.events import stream_workflow_events
from codeagent.workflow.factory import WorkflowFactory
from codeagent.workflow.main_graph import StageHandler
from codeagent.workflow.progress_events import emit_progress
from codeagent.workflow.state import AgentState, create_initial_state

from codeagent.cli.progress import ProgressReporter
from codeagent.cli.approval_console import ApprovalConsole
from codeagent.tools.hitl import ApprovalRequest


@dataclass(frozen=True)
class CliRunResult:
    run_id: str
    run_dir: Path
    final_status: str
    stage_results: dict[str, StageResult]


def execute_task_config(
    task_config: TaskConfig,
    *,
    reporter: ProgressReporter | None = None,
) -> CliRunResult:
    """Run a normalized task config through the LangGraph main workflow."""
    progress = reporter or ProgressReporter()
    context = create_run_context(task_config, output_root=task_config.output_dir)
    manager = CheckpointManager(context.run_dir, run_id=context.run_id)
    initial = create_initial_state(
        run_id=context.run_id,
        mode=task_config.mode,
        selected_stages=[stage.value for stage in task_config.stages],
    )
    initial["max_repair_attempts"] = task_config.runtime.max_repair_attempts

    with manager.create_sqlite_saver() as saver:
        graph = WorkflowFactory(
            stage_handlers=_stage_handlers_for_cli(context),
        ).build(checkpointer=saver)
        thread_config = manager.get_thread_config()
        for event in stream_workflow_events(
            graph.stream(
                initial,
                config=thread_config,
                stream_mode=["updates", "custom", "messages"],
            )
        ):
            context.workflow_trace.record("workflow_event", **event)
            progress.render_event(event)
        final_state = dict(graph.get_state(config=thread_config).values)
        context.workflow_trace.record("workflow_final_state", final_state=final_state)

    stage_results = _stage_results_from_state(final_state)
    _write_final_report(context, list(stage_results.values()))
    final_status = str(final_state.get("final_status") or "failed")
    context.workflow_trace.record("run_completed", final_status=final_status)
    progress.render_event({"type": "run_directory", "path": context.run_dir.as_posix()})
    return CliRunResult(
        run_id=context.run_id,
        run_dir=context.run_dir,
        final_status=final_status,
        stage_results=stage_results,
    )


def _stage_handlers_for_cli(context: RunContext) -> dict[str, StageHandler]:
    return {
        "implementation": _implementation_handler(context),
        "testing": _testing_handler(context),
        "debugging": _debugging_handler(context),
        "repair": _repair_handler(context),
    }


def _should_prompt_for_approval(context: RunContext, approval: ApprovalDecision) -> bool:
    if approval.auto:
        return False
    if context.task_config.mode == "benchmark":
        return False
    return context.task_config.permissions.approval_mode == "manual"


def _approval_auto_source(context: RunContext) -> str | None:
    if (
        context.task_config.mode == "benchmark"
        or context.task_config.auto_approve_in_benchmark
        or context.task_config.runtime.auto_approve_in_benchmark
    ):
        return "benchmark_auto"
    if context.task_config.permissions.approval_mode == "auto":
        return "user_configured_auto"
    return None


def _effective_approval(context: RunContext, approval: ApprovalDecision) -> ApprovalDecision:
    source = _approval_auto_source(context)
    if source is None or approval.auto:
        return approval
    return ApprovalDecision(
        interrupt_id=approval.interrupt_id,
        decision_type=approval.decision_type,
        edited_payload=approval.edited_payload,
        comment=approval.comment,
        decided_at=approval.decided_at,
        decided_by="benchmark" if source == "benchmark_auto" else "config",
        auto=True,
        decision_source=source,
        presented_to_user=False,
    )


def _prompt_approval(context: RunContext, payload: dict[str, object]) -> ApprovalDecision:
    request = _approval_request_from_payload(payload)
    emit_progress(
        "approval_required",
        stage=_stage_from_approval_action(request.action),
        action=request.action,
    )
    context.workflow_trace.record(
        "approval_requested",
        stage=_stage_from_approval_action(request.action),
        interrupt_id=request.interrupt_id,
        action=request.action,
        title=request.title,
        risk_level=request.risk_level,
        payload=request.payload,
    )
    decision = ApprovalConsole().prompt(request)
    return ApprovalDecision(
        interrupt_id=decision.interrupt_id,
        decision_type=decision.decision_type,
        edited_payload=decision.edited_payload,
        comment=decision.comment,
        decided_at=decision.decided_at,
        decided_by="user",
        auto=False,
        decision_source="user",
        presented_to_user=True,
    )


def _approval_request_from_payload(payload: dict[str, object]) -> ApprovalRequest:
    risk = str(payload.get("risk_level") or "medium")
    if risk not in {"low", "medium", "high"}:
        risk = "medium"
    allowed_raw = payload.get("allowed_decisions")
    allowed = (
        tuple(str(item) for item in allowed_raw)
        if isinstance(allowed_raw, list)
        else ("approve", "edit", "reject", "cancel")
    )
    safe_allowed = tuple(
        item
        for item in allowed
        if item in {"approve", "edit", "reject", "respond", "cancel"}
    )
    return ApprovalRequest(
        interrupt_id=str(payload.get("interrupt_id") or payload.get("action") or "approval"),
        action=str(payload.get("action") or "approval"),
        title=str(payload.get("title") or payload.get("action") or "Approval required"),
        payload=payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
        risk_level=risk,  # type: ignore[arg-type]
        allowed_decisions=safe_allowed or ("approve", "reject", "cancel"),
        default_decision="reject",
    )


def _stage_from_approval_action(action: str) -> str:
    if "implementation" in action:
        return "implementation"
    if "test" in action:
        return "testing"
    if "repair" in action or "regression" in action:
        return "repair"
    if "reproduction" in action:
        return "debugging"
    return "workflow"


def _payload_patch_sha256(payload: dict[str, object] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    nested = payload.get("payload")
    if not isinstance(nested, dict):
        return None
    value = nested.get("patch_sha256")
    return str(value) if value else None


def _implementation_plan_review(
    context: RunContext,
    request: ImplementationRequest,
) -> ApprovalDecision:
    if request.plan_review is not None:
        return request.plan_review
    source = request.approval.decision_source or _approval_auto_source(context)
    auto = request.approval.auto or source is not None
    return ApprovalDecision(
        interrupt_id=IMPLEMENTATION_PLAN_INTERRUPT_ID,
        decision_type="approve",
        comment="Generated implementation plan review.",
        decided_by=(
            request.approval.decided_by
            or ("benchmark" if source == "benchmark_auto" else "config" if source else "workflow")
        ),
        auto=auto,
        decision_source=source or "system_default",
        presented_to_user=False,
    )


def _max_review_rounds(context: RunContext) -> int:
    return max(2, context.task_config.model.max_retries + 2)


def _feedback_from_decision(approval: ApprovalDecision) -> str:
    return approval.comment or "Reviewer requested regeneration without extra detail."


def _regenerate_implementation_request(
    context: RunContext,
    *,
    planner: PlanGenerationService,
    feedback: str,
    review_round: int,
) -> ImplementationRequest:
    emit_progress(
        "agent_status",
        stage="implementation",
        message=f"已收到修改意见，正在重新生成实现计划（第 {review_round} 轮反馈）",
    )
    context.workflow_trace.record(
        "approval_feedback_regeneration",
        stage="implementation",
        review_round=review_round,
        feedback=feedback,
    )
    return _planner_create_request(
        planner,
        "create_implementation_request",
        context,
        feedback=feedback,
    )


def _regenerate_testing_request(
    context: RunContext,
    *,
    planner: PlanGenerationService,
    feedback: str,
    review_round: int,
) -> TestingRequest:
    emit_progress(
        "agent_status",
        stage="testing",
        message=f"已收到测试修改意见，正在重新生成测试方案（第 {review_round} 轮反馈）",
    )
    context.workflow_trace.record(
        "approval_feedback_regeneration",
        stage="testing",
        review_round=review_round,
        feedback=feedback,
    )
    return _planner_create_request(
        planner,
        "create_testing_request",
        context,
        feedback=feedback,
    )


def _regenerate_repair_request(
    context: RunContext,
    *,
    planner: PlanGenerationService,
    feedback: str,
    review_round: int,
) -> RepairRequest:
    emit_progress(
        "agent_status",
        stage="repair",
        message=f"已收到修复修改意见，正在重新生成修复计划（第 {review_round} 轮反馈）",
    )
    context.workflow_trace.record(
        "approval_feedback_regeneration",
        stage="repair",
        review_round=review_round,
        feedback=feedback,
    )
    return _planner_create_request(
        planner,
        "create_repair_request",
        context,
        feedback=feedback,
    )


def _planner_create_request(
    planner: PlanGenerationService,
    method_name: str,
    context: RunContext,
    *,
    feedback: str,
):
    method = getattr(planner, method_name)
    try:
        return method(context, feedback=feedback)
    except TypeError as exc:
        if "feedback" not in str(exc):
            raise
        return method(context)


def _approval_feedback_limit_result(
    *,
    stage: str,
    started_at: str,
    summary: str,
) -> StageResult:
    return _failed_stage_result(
        stage=stage,
        started_at=started_at,
        summary=summary,
        category="hitl",
        message="Reviewer feedback requested regeneration more times than configured.",
        next_suggestion="Approve, edit, reject, or increase model.max_retries for more review rounds.",
    )


def _record_feedback_decision(
    context: RunContext,
    *,
    stage: str,
    action: str,
    approval: ApprovalDecision,
) -> None:
    _writer(context).record_human_decision(
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
    context.workflow_trace.record(
        "approval_decision",
        stage=stage,
        action=action,
        interrupt_id=approval.interrupt_id,
        decision_type=approval.decision_type,
        auto=approval.auto,
        decision_source=approval.decision_source,
        presented_to_user=approval.presented_to_user,
        decided_by=approval.decided_by,
        comment=approval.comment,
    )


def _implementation_handler(context: RunContext) -> StageHandler:
    service = ImplementationService(run_context=context)

    def run(state: AgentState) -> dict[str, Any]:
        started_at = utc_timestamp()
        try:
            emit_progress(
                "phase_started",
                stage="implementation",
                message="正在读取公开需求和可见源码，准备生成实现计划",
            )
            request = PlanGenerationService().create_implementation_request(context)
        except Exception as exc:
            result = _llm_generation_failed_result(
                stage="implementation",
                started_at=started_at,
                exc=exc,
                summary="Implementation plan generation failed.",
                next_suggestion=(
                    "Inspect model configuration, visible inputs, and schema validation "
                    "errors, then regenerate the implementation plan."
                ),
            )
            _writer(context).write_stage_report(result)
            return _state_update_from_result(state, result)
        try:
            emit_progress(
                "agent_status",
                stage="implementation",
                message="已获得实现计划，等待审批后再生成、校验并应用实现补丁",
            )
            result = _run_implementation_with_approval(context, service, request)
        except Exception as exc:
            result = _stage_execution_failed_result(
                stage="implementation",
                started_at=started_at,
                exc=exc,
                summary="Implementation stage execution failed.",
                next_suggestion=(
                    "Inspect implementation artifacts, command logs, and patch "
                    "application state, then retry the stage after fixing the runtime issue."
                ),
            )
            _writer(context).write_stage_report(result)
        return _state_update_from_result(state, result)

    return run


def _run_implementation_with_approval(
    context: RunContext,
    service: ImplementationService,
    request: ImplementationRequest,
) -> StageResult:
    planner = PlanGenerationService()
    started_at = utc_timestamp()
    for review_round in range(1, _max_review_rounds(context) + 1):
        plan_review = _implementation_plan_review(context, request)
        if _should_prompt_for_approval(context, plan_review):
            plan_preview = service.prepare_plan_review(request)
            if plan_preview.result is not None:
                return plan_preview.result
            if plan_preview.payload is None:
                raise RuntimeError("implementation plan approval payload missing")
            plan_review = _prompt_approval(context, plan_preview.payload)
        else:
            plan_review = _effective_approval(context, plan_review)
        if plan_review.decision_type == "respond":
            _record_feedback_decision(
                context,
                stage="implementation",
                action="review_implementation_plan",
                approval=plan_review,
            )
            request = _regenerate_implementation_request(
                context,
                planner=planner,
                feedback=_feedback_from_decision(plan_review),
                review_round=review_round,
            )
            continue
        if hasattr(service, "apply_plan_review_decision"):
            reviewed = service.apply_plan_review_decision(request, approval=plan_review)
            if isinstance(reviewed, StageResult):
                return reviewed
            request = reviewed
        else:
            request = replace(request, plan_review=plan_review)

        if not _should_prompt_for_approval(context, request.approval):
            return service.run(
                replace(request, approval=_effective_approval(context, request.approval))
            )

        preview = service.prepare_approval(request)
        if preview.result is not None:
            return preview.result
        if preview.payload is None:
            raise RuntimeError("implementation approval payload missing")
        approval = (
            _prompt_approval(context, preview.payload)
            if _should_prompt_for_approval(context, request.approval)
            else _effective_approval(context, request.approval)
        )
        if approval.decision_type == "respond":
            _record_feedback_decision(
                context,
                stage="implementation",
                action="approve_implementation_patch",
                approval=approval,
            )
            request = _regenerate_implementation_request(
                context,
                planner=planner,
                feedback=_feedback_from_decision(approval),
                review_round=review_round,
            )
            continue
        if approval.decision_type == "edit":
            return service.run(replace(request, approval=approval))
        return service.apply_prepared_patch(
            request,
            approval=approval,
            approved_patch_sha256=_payload_patch_sha256(preview.payload),
        )
    return _approval_feedback_limit_result(
        stage="implementation",
        started_at=started_at,
        summary="Implementation review feedback exceeded the retry limit.",
    )


def _testing_handler(context: RunContext) -> StageHandler:
    service = TestingService(run_context=context)

    def run(state: AgentState) -> dict[str, Any]:
        started_at = utc_timestamp()
        try:
            emit_progress(
                "phase_started",
                stage="testing",
                message="正在根据公开需求、实现产物和可见源码设计自测用例",
            )
            request = PlanGenerationService().create_testing_request(context)
        except Exception as exc:
            result = _llm_generation_failed_result(
                stage="testing",
                started_at=started_at,
                exc=exc,
                summary="Testing plan generation failed.",
                next_suggestion=(
                    "Inspect model configuration, visible inputs, and schema validation "
                    "errors, then regenerate the testing plan."
                ),
            )
            _writer(context).write_stage_report(result)
            return _state_update_from_result(state, result)
        try:
            emit_progress(
                "agent_status",
                stage="testing",
                message="测试方案已生成，正在写入测试补丁并执行 Agent 自测",
            )
            result = _run_testing_with_approval(context, service, request)
        except Exception as exc:
            result = _stage_execution_failed_result(
                stage="testing",
                started_at=started_at,
                exc=exc,
                summary="Testing stage execution failed.",
                next_suggestion=(
                    "Inspect testing artifacts, generated tests, command logs, and "
                    "test result parsing state, then retry the stage after fixing the "
                    "runtime issue."
                ),
            )
            _writer(context).write_stage_report(result)
        return _state_update_from_result(state, result)

    return run


def _run_testing_with_approval(
    context: RunContext,
    service: TestingService,
    request: TestingRequest,
) -> StageResult:
    planner = PlanGenerationService()
    started_at = utc_timestamp()
    for review_round in range(1, _max_review_rounds(context) + 1):
        plan_preview = service.prepare_plan_review(request)
        if plan_preview.result is not None:
            return plan_preview.result
        if plan_preview.payload is None:
            raise RuntimeError("testing plan approval payload missing")
        plan_review = (
            _prompt_approval(context, plan_preview.payload)
            if _should_prompt_for_approval(context, request.plan_review)
            else _effective_approval(context, request.plan_review)
        )
        if plan_review.decision_type == "respond":
            _record_feedback_decision(
                context,
                stage="testing",
                action="review_test_plan",
                approval=plan_review,
            )
            request = _regenerate_testing_request(
                context,
                planner=planner,
                feedback=_feedback_from_decision(plan_review),
                review_round=review_round,
            )
            continue
        request = replace(request, plan_review=plan_review)

        patch_preview = service.prepare_patch_approval(request, plan_review=plan_review)
        if patch_preview.result is not None:
            return patch_preview.result
        if patch_preview.payload is None:
            raise RuntimeError("testing patch approval payload missing")
        patch_approval = (
            _prompt_approval(context, patch_preview.payload)
            if _should_prompt_for_approval(context, request.patch_approval)
            else _effective_approval(context, request.patch_approval)
        )
        if patch_approval.decision_type == "respond":
            _record_feedback_decision(
                context,
                stage="testing",
                action="approve_test_patch",
                approval=patch_approval,
            )
            request = _regenerate_testing_request(
                context,
                planner=planner,
                feedback=_feedback_from_decision(patch_approval),
                review_round=review_round,
            )
            continue
        request = replace(request, patch_approval=patch_approval)

        command_preview = service.apply_patch_and_prepare_command(
            request,
            patch_approval=patch_approval,
            approved_patch_sha256=_payload_patch_sha256(patch_preview.payload),
        )
        if command_preview.result is not None:
            return command_preview.result
        if command_preview.payload is None:
            raise RuntimeError("testing command approval payload missing")
        command_approval = (
            _prompt_approval(context, command_preview.payload)
            if _should_prompt_for_approval(context, request.command_approval)
            else _effective_approval(context, request.command_approval)
        )
        request = replace(request, command_approval=command_approval)
        return service.run_prepared_command(request, command_approval=command_approval)
    return _approval_feedback_limit_result(
        stage="testing",
        started_at=started_at,
        summary="Testing review feedback exceeded the retry limit.",
    )


def _testing_command_handler(context: RunContext) -> StageHandler:
    def run(state: AgentState) -> dict[str, Any]:
        started_at = utc_timestamp()
        stage_dir = context.stage_dirs[Stage.TEST]
        fs.mkdir(stage_dir)
        writer = _writer(context)
        command = context.task_config.test_command.command
        approval = CommandApproval(
            operation_id="testing_cli_command",
            approved=True,
            decision_type="approve",
            decided_by="cli",
            reason="User supplied non-interactive test command.",
            auto=True,
        )
        writer.record_human_decision(
            HumanDecision(
                interrupt_id="testing_cli_command",
                action="approve_test_command",
                decision_type="approve",
                timestamp=utc_timestamp(),
                auto=True,
            )
        )
        try:
            shell = ShellRunner(
                logs_dir=stage_dir / "logs",
                max_output_chars=context.task_config.runtime.log_truncation_chars,
            ).run(
                command,
                cwd=context.task_config.project_path,
                timeout_seconds=context.task_config.test_command.timeout_seconds,
                approval=approval,
            )
            parsed = parse_shell_result(
                framework=context.task_config.test_framework,
                shell_result=shell,
            )
            result = _testing_result_from_parsed(
                context,
                parsed,
                shell=shell,
                started_at=started_at,
            )
        except (CommandDeniedError, ValueError, RuntimeError) as exc:
            result = _failed_stage_result(
                stage="testing",
                started_at=started_at,
                summary="Testing command failed before completion.",
                category="shell",
                message=str(exc),
                next_suggestion="Use an allowed pytest, unittest, or py_compile command.",
            )
        writer.write_stage_report(result)
        return _state_update_from_result(state, result)

    return run


def _debugging_handler(context: RunContext) -> StageHandler:
    service = DebuggingService(run_context=context)

    def run(state: AgentState) -> dict[str, Any]:
        emit_progress(
            "phase_started",
            stage="debugging",
            message="正在收集测试失败日志、测试报告和可见源码线索",
        )
        request = _debugging_request_from_config(context, state)
        emit_progress(
            "agent_status",
            stage="debugging",
            message="正在分析失败现象并生成根因定位报告",
        )
        result = _run_debugging_with_approval(context, service, request)
        return _state_update_from_result(state, result)

    return run


def _run_debugging_with_approval(
    context: RunContext,
    service: DebuggingService,
    request: DebuggingRequest,
) -> StageResult:
    if not _should_prompt_for_approval(context, request.command_approval):
        return service.run(
            replace(
                request,
                command_approval=_effective_approval(context, request.command_approval),
            )
        )
    preview = service.prepare_reproduction_approval(request)
    if preview.result is not None:
        return preview.result
    if preview.payload is None:
        raise RuntimeError("debugging reproduction approval payload missing")
    command_approval = _prompt_approval(context, preview.payload)
    return service.run_after_approval(request, command_approval=command_approval)


def _repair_handler(context: RunContext) -> StageHandler:
    service = RepairService(run_context=context)

    def run(state: AgentState) -> dict[str, Any]:
        started_at = utc_timestamp()
        try:
            emit_progress(
                "phase_started",
                stage="repair",
                message="正在读取调试证据和失败日志，准备生成修复计划",
            )
            request = PlanGenerationService().create_repair_request(context)
        except Exception as exc:
            result = _llm_generation_failed_result(
                stage="repair",
                started_at=started_at,
                exc=exc,
                summary="Repair plan generation failed.",
                next_suggestion=(
                    "Inspect model configuration, debug evidence, visible source, "
                    "and schema validation errors, then regenerate the repair plan."
                ),
            )
            _writer(context).write_stage_report(result)
            return _state_update_from_result(state, result)
        try:
            emit_progress(
                "agent_status",
                stage="repair",
                message="已获得修复计划，正在生成、校验并验证修复补丁",
            )
            result = _run_repair_with_approval(context, service, request)
        except Exception as exc:
            result = _stage_execution_failed_result(
                stage="repair",
                started_at=started_at,
                exc=exc,
                summary="Repair stage execution failed.",
                next_suggestion=(
                    "Inspect repair artifacts, command logs, and patch application state, "
                    "then retry the stage after fixing the runtime issue."
                ),
            )
            _writer(context).write_stage_report(result)
        return _state_update_from_result(state, result)

    return run


def _run_repair_with_approval(
    context: RunContext,
    service: RepairService,
    request: RepairRequest,
) -> StageResult:
    if (
        not _should_prompt_for_approval(context, request.patch_approval)
        and not _should_prompt_for_approval(context, request.command_approval)
    ):
        return service.run(
            replace(
                request,
                patch_approval=_effective_approval(context, request.patch_approval),
                command_approval=_effective_approval(context, request.command_approval),
            )
        )

    planner = PlanGenerationService()
    started_at = utc_timestamp()
    for review_round in range(1, _max_review_rounds(context) + 1):
        patch_preview = service.prepare_patch_approval(request)
        if patch_preview.result is not None:
            return patch_preview.result
        if patch_preview.payload is None:
            raise RuntimeError("repair patch approval payload missing")
        patch_approval = (
            _prompt_approval(context, patch_preview.payload)
            if _should_prompt_for_approval(context, request.patch_approval)
            else _effective_approval(context, request.patch_approval)
        )
        if patch_approval.decision_type == "respond":
            _record_feedback_decision(
                context,
                stage="repair",
                action="approve_repair_patch",
                approval=patch_approval,
            )
            request = _regenerate_repair_request(
                context,
                planner=planner,
                feedback=_feedback_from_decision(patch_approval),
                review_round=review_round,
            )
            continue
        request = replace(request, patch_approval=patch_approval)

        command_preview = service.apply_patch_and_prepare_command(
            request,
            patch_approval=patch_approval,
            approved_patch_sha256=_payload_patch_sha256(patch_preview.payload),
        )
        if command_preview.result is not None:
            return command_preview.result
        if command_preview.payload is None:
            raise RuntimeError("repair command approval payload missing")
        command_approval = (
            _prompt_approval(context, command_preview.payload)
            if _should_prompt_for_approval(context, request.command_approval)
            else _effective_approval(context, request.command_approval)
        )
        request = replace(request, command_approval=command_approval)
        return service.run_prepared_command(request, command_approval=command_approval)
    return _approval_feedback_limit_result(
        stage="repair",
        started_at=started_at,
        summary="Repair review feedback exceeded the retry limit.",
    )


def _debugging_request_from_config(
    context: RunContext,
    state: AgentState,
) -> DebuggingRequest:
    failure_logs = _failure_logs_from_config(context, state)
    test_report_path = _testing_report_path(context, state)
    has_static_evidence = bool(failure_logs or test_report_path)
    benchmark_auto = (
        context.task_config.mode == "benchmark"
        or context.task_config.auto_approve_in_benchmark
        or context.task_config.runtime.auto_approve_in_benchmark
    )
    user_auto = context.task_config.permissions.approval_mode == "auto"
    command_auto = has_static_evidence or benchmark_auto or user_auto
    decision_source = (
        "system_static_evidence"
        if has_static_evidence
        else "benchmark_auto"
        if benchmark_auto
        else "user_configured_auto"
        if user_auto
        else "system_default"
    )
    return DebuggingRequest(
        test_command=None if has_static_evidence else context.task_config.test_command.command,
        command_approval=ApprovalDecision(
            interrupt_id=REPRODUCTION_COMMAND_INTERRUPT_ID,
            decision_type="reject" if has_static_evidence else "approve",
            auto=command_auto,
            comment=(
                "Static failure evidence supplied by CLI."
                if has_static_evidence
                else "Non-interactive CLI reproduction command."
            ),
            decided_by="workflow" if has_static_evidence else "benchmark" if benchmark_auto else "config" if user_auto else "workflow",
            decision_source=decision_source,
            presented_to_user=False,
        ),
        failure_logs=failure_logs,
        test_report_path=test_report_path,
        framework=context.task_config.test_framework,
        command_timeout_seconds=context.task_config.test_command.timeout_seconds,
    )


def _failure_logs_from_config(context: RunContext, state: AgentState) -> list[Path]:
    testing_logs = _testing_log_paths(context)
    if "testing" in state.get("stage_results", {}):
        return testing_logs
    paths = [
        material.path
        for material in context.task_config.input_materials
        if "log" in material.material_type.lower()
        or "failure" in material.material_type.lower()
    ]
    if paths:
        return paths
    return []


def _testing_log_paths(context: RunContext) -> list[Path]:
    logs_dir = context.stage_dirs[Stage.TEST] / "logs"
    candidates = [
        logs_dir / "testing_cli_command.stdout.log",
        logs_dir / "testing_cli_command.stderr.log",
    ]
    if fs.exists(logs_dir):
        candidates.extend(sorted(logs_dir.glob("*.stdout.log")))
        candidates.extend(sorted(logs_dir.glob("*.stderr.log")))
    resolved: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if not fs.exists(path):
            continue
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


def _testing_report_path(context: RunContext, state: AgentState) -> Path | None:
    explicit = [
        material.path
        for material in context.task_config.input_materials
        if "test_report" in material.material_type.lower()
    ]
    if explicit:
        return explicit[0]
    if "testing" in state.get("stage_results", {}):
        for filename in ("test_result.json", "test_report.json", "test_report.md"):
            path = context.stage_dirs[Stage.TEST] / filename
            if fs.exists(path):
                return path
    return None


def _testing_result_from_parsed(
    context: RunContext,
    parsed: TestResult,
    *,
    shell: ShellResult | None = None,
    started_at: str,
) -> StageResult:
    stage_dir = context.stage_dirs[Stage.TEST]
    json_path = stage_dir / "test_report.json"
    md_path = stage_dir / "test_report.md"
    fs.write_text(
        json_path,
        json.dumps(parsed.to_json_dict(), indent=2, ensure_ascii=False),
    )
    fs.write_text(md_path, _render_test_report(parsed))
    artifacts = [
        _record_artifact(
            context,
            "testing_test_report_json",
            "testing",
            ArtifactKind.JSON,
            json_path,
            "Parsed test result",
        ),
        _record_artifact(
            context,
            "testing_test_report",
            "testing",
            ArtifactKind.REPORT,
            md_path,
            "Testing report",
        ),
    ]
    log_artifacts = (
        (
            "testing_stdout_log",
            shell.stdout_log if shell is not None else stage_dir / "logs" / "testing_cli_command.stdout.log",
            "Testing stdout log",
        ),
        (
            "testing_stderr_log",
            shell.stderr_log if shell is not None else stage_dir / "logs" / "testing_cli_command.stderr.log",
            "Testing stderr log",
        ),
        (
            "testing_command_record",
            shell.record_path if shell is not None else stage_dir / "logs" / "testing_cli_command.command.json",
            "Testing command record",
        ),
    )
    for artifact_id, path, summary in log_artifacts:
        if fs.exists(path):
            artifacts.append(
                _record_artifact(
                    context,
                    artifact_id,
                    "testing",
                    ArtifactKind.LOG if path.suffix == ".log" else ArtifactKind.JSON,
                    path,
                    summary,
                )
            )
    if _is_no_tests_result(parsed):
        return StageResult(
            stage="testing",
            status="failed",
            started_at=started_at,
            ended_at=utc_timestamp(),
            summary="Testing command completed but no tests were collected.",
            artifact_ids=artifacts,
            error=ErrorRecord(
                error_id="testing_no_tests_collected",
                stage="testing",
                node="testing",
                category="validation",
                message=(
                    "The testing stage must execute generated tests; a zero-test "
                    "result is not accepted as a successful verification."
                ),
                artifact_ids=artifacts,
                retryable=True,
            ),
            next_suggestion="Generate visible pytest/unittest tests and run them.",
        )
    if parsed.success:
        return StageResult(
            stage="testing",
            status="succeeded",
            started_at=started_at,
            ended_at=utc_timestamp(),
            summary=_test_summary(parsed),
            artifact_ids=artifacts,
            next_suggestion="Testing succeeded.",
        )
    return StageResult(
        stage="testing",
        status="failed",
        started_at=started_at,
        ended_at=utc_timestamp(),
        summary=_test_summary(parsed),
        artifact_ids=artifacts,
        error=ErrorRecord(
            error_id="testing_pytest_failure",
            stage="testing",
            node="testing",
            category="pytest_failure",
            message=parsed.error_summary or _test_summary(parsed),
            artifact_ids=artifacts,
            retryable=True,
        ),
        next_suggestion="Continue to debugging with saved test logs.",
    )


def _render_test_report(parsed: TestResult) -> str:
    lines = [
        "# Testing Report",
        "",
        f"- Framework: {parsed.framework}",
        f"- Success: {parsed.success}",
        f"- Command: {parsed.command}",
        f"- Exit code: {parsed.exit_code}",
        f"- Summary: {_test_summary(parsed)}",
        "",
        "## Failing Tests",
        "",
    ]
    if not parsed.failing_tests:
        lines.append("- <none>")
    for failure in parsed.failing_tests:
        lines.append(f"- {failure.nodeid}: {failure.outcome} {failure.message}")
    if parsed.error_summary:
        lines.extend(["", "## Error Summary", "", parsed.error_summary])
    return "\n".join(lines) + "\n"


def _test_summary(parsed: TestResult) -> str:
    return (
        f"{parsed.passed} passed, {parsed.failed} failed, "
        f"{parsed.errors} errors, {parsed.skipped} skipped"
    )


def _is_no_tests_result(parsed: TestResult) -> bool:
    if parsed.total > 0:
        return False
    text = f"{parsed.raw_summary}\n{parsed.error_summary}".lower()
    return (
        parsed.success
        or "ran 0 tests" in text
        or "no tests ran" in text
        or "collected 0 items" in text
    )


def _failed_stage_result(
    *,
    stage: str,
    started_at: str,
    summary: str,
    category: str,
    message: str,
    next_suggestion: str,
) -> StageResult:
    return StageResult(
        stage=stage,
        status="failed",
        started_at=started_at,
        ended_at=utc_timestamp(),
        summary=summary,
        error=ErrorRecord(
            error_id=f"{stage}_{category}",
            stage=stage,
            node=stage,
            category=category,  # type: ignore[arg-type]
            message=message,
            retryable=True,
        ),
        next_suggestion=next_suggestion,
    )


def _llm_generation_failed_result(
    *,
    stage: str,
    started_at: str,
    exc: Exception,
    summary: str,
    next_suggestion: str,
) -> StageResult:
    return _failed_stage_result(
        stage=stage,
        started_at=started_at,
        summary=summary,
        category="model" if isinstance(exc, PlanGenerationError) else "model",
        message=_redact_exception(exc),
        next_suggestion=next_suggestion,
    )


def _stage_execution_failed_result(
    *,
    stage: str,
    started_at: str,
    exc: Exception,
    summary: str,
    next_suggestion: str,
) -> StageResult:
    return _failed_stage_result(
        stage=stage,
        started_at=started_at,
        summary=summary,
        category="tool",
        message=_redact_exception(exc),
        next_suggestion=next_suggestion,
    )


def _redact_exception(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return redact_sensitive_text(text)


def _state_update_from_result(state: AgentState, result: StageResult) -> dict[str, Any]:
    stage_results = dict(state.get("stage_results", {}))
    stage_results[result.stage] = result.model_dump(mode="json", exclude_none=True)
    artifact_refs = list(state.get("artifact_refs", []))
    for artifact_id in result.artifact_ids:
        if artifact_id not in artifact_refs:
            artifact_refs.append(artifact_id)
    messages = list(state.get("messages", []))
    messages.append(
        {
            "type": "stage_completed",
            "stage": result.stage,
            "status": result.status,
            "summary": result.summary,
        }
    )
    return {
        "stage_results": stage_results,
        "artifact_refs": artifact_refs,
        "messages": messages,
    }


def _stage_results_from_state(state: dict[str, Any]) -> dict[str, StageResult]:
    results: dict[str, StageResult] = {}
    raw_results = state.get("stage_results") or {}
    if not isinstance(raw_results, dict):
        return results
    for stage, raw in raw_results.items():
        if isinstance(raw, dict):
            results[str(stage)] = StageResult.model_validate(raw)
    return results


def _write_final_report(context: RunContext, results: list[StageResult]) -> None:
    if results:
        _writer(context).write_final_report(results)


def _writer(context: RunContext) -> ReportWriter:
    return ReportWriter(
        run_dir=context.run_dir,
        artifact_store=context.artifact_store,
        transcript=context.transcript,
        decision_trace=context.decision_trace,
    )


def _record_artifact(
    context: RunContext,
    artifact_id: str,
    stage: str,
    kind: ArtifactKind,
    path: Path,
    summary: str,
) -> str:
    context.artifact_store.record(
        ArtifactRecord(
            artifact_id=artifact_id,
            stage=stage,
            kind=kind,
            path=path,
            summary=summary,
        )
    )
    context.artifact_store.write()
    return artifact_id
