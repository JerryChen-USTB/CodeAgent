"""Execute normalized TaskConfig objects from CLI commands."""

from __future__ import annotations

import ast
import json
import hashlib
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from codeagent import filesystem as fs
from codeagent.adapters.test_result import TestResult
from codeagent.agents.plan_generation import PlanGenerationError, PlanGenerationService
from codeagent.config.schema import Stage, TaskConfig
from codeagent.context.redaction import redact_sensitive_text
from codeagent.context.sensitive_filter import SensitiveFilter
from codeagent.errors.exceptions import ErrorRecord, utc_timestamp
from codeagent.reports import ArtifactKind, ArtifactRecord, ReportWriter, StageResult
from codeagent.reports.schemas import HumanDecision
from codeagent.runtime.commands import CommandApproval, ShellResult
from codeagent.runtime.run_context import RunContext, create_run_context
from codeagent.services.patch_service import FileChange, PatchApplyError, PatchValidationError
from codeagent.stages.debugging_service import (
    REPRODUCTION_COMMAND_INTERRUPT_ID,
    DebuggingRequest,
    DebuggingService,
)
from codeagent.stages.implementation_service import (
    PATCH_INTERRUPT_ID,
    PLAN_INTERRUPT_ID as IMPLEMENTATION_PLAN_INTERRUPT_ID,
    ImplementationPatchDraft,
    ImplementationPlan,
    ImplementationRequest,
    ImplementationService,
)
from codeagent.stages.repair_service import (
    REPAIR_PATCH_INTERRUPT_ID,
    REPAIR_PLAN_INTERRUPT_ID,
    RepairPatchDraft,
    RepairPlan,
    RepairRequest,
    RepairService,
)
from codeagent.stages.testing_service import (
    TEST_PATCH_INTERRUPT_ID,
    TestingPatchDraft,
    TestingPlan,
    TestingRequest,
    TestingService,
)
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
from codeagent.cli.approval_console import (
    PATCH_AUTO_APPROVE_REMAINING_KEY,
    ApprovalConsole,
)
from codeagent.tools.hitl import ApprovalRequest


@dataclass(frozen=True)
class CliRunResult:
    run_id: str
    run_dir: Path
    final_status: str
    stage_results: dict[str, StageResult]


@dataclass(frozen=True)
class DisplayPathRef:
    display: str
    absolute_path: Path


@dataclass(frozen=True)
class _IncrementalPatchResult:
    drafts: list[BaseModel]
    completed_files: list[str]
    failures: list[dict[str, object]]
    work_summary: str


@dataclass
class _StagePatchContext:
    stage: Literal["implementation", "testing", "repair"]
    workspace_tree: str
    initial_context: str
    applied_file_context: str
    context_path: Path
    metadata_path: Path
    applied_context_path: Path
    entries: list[dict[str, object]]
    budget_chars: int
    truncated: bool = False


@dataclass(frozen=True)
class _SingleFilePatchResult:
    draft: BaseModel
    changed_files: list[str]
    patch_path: Path
    patch_sha256: str
    attempts: list[dict[str, object]]
    auto_approve_remaining: bool = False


class ApprovalConsoleLike(Protocol):
    def prompt(self, request: ApprovalRequest) -> ApprovalDecision: ...


WORK_SUMMARY_MAX_CHARS = 6_000
STAGE_PATCH_CONTEXT_MAX_CHARS = 70_000
STAGE_PATCH_APPLIED_CONTEXT_MAX_CHARS = 30_000
STAGE_PATCH_APPLIED_FILE_MAX_CHARS = 18_000
STAGE_CONTEXT_TEXT_EXTENSIONS = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml"}
STAGE_CONTEXT_CONFIG_FILENAMES = {
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "requirements.txt",
    "requirements-dev.txt",
    "pytest.ini",
    "tox.ini",
    "package.json",
    "tsconfig.json",
}


def execute_task_config(
    task_config: TaskConfig,
    *,
    reporter: ProgressReporter | None = None,
    approval_console: ApprovalConsoleLike | None = None,
) -> CliRunResult:
    """Run a normalized task config through the LangGraph main workflow."""
    progress = reporter or ProgressReporter()
    approval_console = approval_console or ApprovalConsole()
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
            stage_handlers=_stage_handlers_for_cli(context, approval_console),
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
    key_file_refs = _key_file_summary_refs(context)
    key_files = [ref.display for ref in key_file_refs]
    context.workflow_trace.record("key_files_summary", files=key_files)
    progress.render_event(
        {
            "type": "key_files_summary",
            "files": [ref.display for ref in key_file_refs],
            "file_links": [_terminal_link_payload(ref) for ref in key_file_refs],
        }
    )
    progress.render_event({"type": "run_directory", "path": context.run_dir.as_posix()})
    return CliRunResult(
        run_id=context.run_id,
        run_dir=context.run_dir,
        final_status=final_status,
        stage_results=stage_results,
    )


def _stage_handlers_for_cli(
    context: RunContext,
    approval_console: ApprovalConsoleLike,
) -> dict[str, StageHandler]:
    return {
        "implementation": _implementation_handler(context, approval_console),
        "testing": _testing_handler(context, approval_console),
        "debugging": _debugging_handler(context, approval_console),
        "repair": _repair_handler(context, approval_console),
    }


def _planner_for_context(context: RunContext) -> PlanGenerationService:
    return PlanGenerationService()


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


def _prompt_approval(
    context: RunContext,
    payload: dict[str, object],
    approval_console: ApprovalConsoleLike,
) -> ApprovalDecision:
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
    _print_approval_context(context, request)
    decision = approval_console.prompt(request)
    return ApprovalDecision(
        interrupt_id=decision.interrupt_id,
        decision_type=decision.decision_type,
        edited_payload=decision.edited_payload,
        comment=decision.comment,
        decided_at=decision.decided_at,
        decided_by="user",
        auto=False,
        decision_source=decision.decision_source or "user",
        presented_to_user=True,
    )


def _print_approval_context(context: RunContext, request: ApprovalRequest) -> None:
    refs = _approval_context_refs(context, request)
    command_context = _approval_command_context(context, request)
    if not refs and command_context is None:
        return
    lines = [ref.display for ref in refs]
    print("")
    if refs:
        print("请先审查以下文件：")
        for ref in refs:
            print(f"- {_terminal_link(ref)}")
    command: str | None = None
    cwd_ref: DisplayPathRef | None = None
    if command_context is not None:
        command, cwd_ref = command_context
        if refs:
            print("")
        print("将执行命令：")
        print(f"- {command}")
        print("工作目录：")
        print(f"- {_terminal_link(cwd_ref)}")
    hint = _approval_hint(request.action)
    if hint:
        print(hint)
    print("")
    context.workflow_trace.record(
        "approval_context_presented",
        stage=_stage_from_approval_action(request.action),
        action=request.action,
        files=lines,
        hint=hint,
        command=command,
        cwd=cwd_ref.absolute_path.as_posix() if cwd_ref is not None else None,
    )


def _approval_context_lines(
    context: RunContext,
    request: ApprovalRequest,
) -> list[str]:
    return [ref.display for ref in _approval_context_refs(context, request)]


def _approval_context_refs(
    context: RunContext,
    request: ApprovalRequest,
) -> list[DisplayPathRef]:
    payload = request.payload
    refs: list[DisplayPathRef] = []
    artifact_keys = [
        ("plan_path", context.run_dir),
        ("plan_json_path", context.run_dir),
        ("patch_draft_json_path", context.run_dir),
        ("patch_path", context.run_dir),
    ]
    for key, base in artifact_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            ref = _existing_display_path_ref(value, base=base)
            if ref is not None:
                refs.append(ref)
    changed_files = payload.get("changed_files")
    include_changed_files = "plan" not in request.action
    if include_changed_files and isinstance(changed_files, list):
        for value in changed_files:
            if isinstance(value, str) and value.strip():
                ref = _existing_display_path_ref(
                    value,
                    base=context.task_config.project_path,
                )
                if ref is not None:
                    refs.append(ref)
    return _dedupe_refs(refs)


def _approval_command_context(
    context: RunContext,
    request: ApprovalRequest,
) -> tuple[str, DisplayPathRef] | None:
    if "command" not in request.action:
        return None
    command = request.payload.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    cwd_value = request.payload.get("cwd")
    if isinstance(cwd_value, str) and cwd_value.strip():
        cwd = Path(cwd_value)
        if not cwd.is_absolute():
            cwd = (context.task_config.project_path / cwd).resolve()
    else:
        cwd = context.task_config.project_path.resolve()
    return command.strip(), DisplayPathRef(display=cwd.as_posix(), absolute_path=cwd)


def _approval_hint(action: str) -> str:
    if "plan" in action:
        return "当前动作：补丁尚未生成；同意后开始生成补丁草案。"
    if "patch" in action:
        return "当前动作：只审查补丁；同意后才会修改项目文件。"
    if "command" in action:
        return "当前动作：同意后会在项目目录中执行命令。"
    return ""


def _display_path(path_text: str, *, base: Path) -> str:
    return _display_path_ref(path_text, base=base).display


def _display_path_ref(path_text: str, *, base: Path) -> DisplayPathRef:
    path = Path(path_text)
    relative = path
    base_resolved = base.resolve()
    if path.is_absolute():
        absolute = path.resolve()
        try:
            relative = absolute.relative_to(base_resolved)
        except ValueError:
            relative = Path(path.name)
    else:
        absolute = (base_resolved / path).resolve()
    normalized = relative.as_posix()
    name = Path(normalized).name or normalized
    return DisplayPathRef(display=f"{name} ({normalized})", absolute_path=absolute)


def _existing_display_path_ref(path_text: str, *, base: Path) -> DisplayPathRef | None:
    ref = _display_path_ref(path_text, base=base)
    if ref.absolute_path.exists():
        return ref
    return None


def _terminal_link(ref: DisplayPathRef) -> str:
    uri = _terminal_uri(ref)
    if uri is None:
        return ref.display
    return f"\033]8;;{uri}\033\\{ref.display}\033]8;;\033\\"


def _terminal_link_payload(ref: DisplayPathRef) -> dict[str, str]:
    return {"label": ref.display, "uri": _terminal_uri(ref) or ""}


def _terminal_uri(ref: DisplayPathRef) -> str | None:
    if not _terminal_hyperlinks_enabled():
        return None
    try:
        return ref.absolute_path.resolve().as_uri()
    except ValueError:
        return None


def _terminal_hyperlinks_enabled() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.environ.get("CODEAGENT_DISABLE_TERMINAL_LINKS"):
        return False
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    if term_program in {"vscode", "wezterm", "iterm.app"}:
        return True
    return bool(os.environ.get("WT_SESSION") or os.environ.get("VTE_VERSION"))


def _interactive_input_available() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _key_file_summary(context: RunContext) -> list[str]:
    return [ref.display for ref in _key_file_summary_refs(context)]


def _key_file_summary_refs(context: RunContext) -> list[DisplayPathRef]:
    refs: list[DisplayPathRef] = []
    for stage in (Stage.IMPLEMENT, Stage.TEST, Stage.REPAIR):
        changed_files_path = context.stage_dirs[stage] / "changed_files.json"
        if not fs.exists(changed_files_path):
            continue
        try:
            data = json.loads(fs.read_text(changed_files_path))
        except (OSError, json.JSONDecodeError):
            continue
        changed_files = data.get("changed_files") if isinstance(data, dict) else None
        if not isinstance(changed_files, list):
            continue
        for value in changed_files:
            if isinstance(value, str) and value.strip():
                refs.append(_display_path_ref(value, base=context.task_config.project_path))
    for value in [
        "final_report.md",
        "workflow.log",
        "workflow_events.jsonl",
        "decision_trace.jsonl",
        "artifacts_index.json",
    ]:
        refs.append(_display_path_ref(value, base=context.run_dir))
    return _dedupe_refs(refs)


def _dedupe_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        deduped.append(line)
    return deduped


def _dedupe_refs(refs: list[DisplayPathRef]) -> list[DisplayPathRef]:
    seen: set[tuple[str, str]] = set()
    deduped: list[DisplayPathRef] = []
    for ref in refs:
        key = (ref.display, str(ref.absolute_path).lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


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
        default_decision=(
            "approve"
            if payload.get("default_decision") == "approve"
            else "reject"
        ),
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


def _generate_implementation_patch_request(
    context: RunContext,
    request: ImplementationRequest,
    *,
    planner: PlanGenerationService,
    feedback: str | None = None,
    review_round: int = 1,
) -> ImplementationRequest:
    emit_progress(
        "agent_status",
        stage="implementation",
        message="实现方案已通过，正在调用 LLM 生成实现补丁草案。",
    )
    context.workflow_trace.record(
        "patch_generation_requested",
        stage="implementation",
        review_round=review_round,
        feedback=feedback,
    )
    draft = planner.create_implementation_patch_draft(
        context,
        request.plan,
        feedback=feedback,
    )
    return replace(request, patch_draft=draft, alternate_patch_drafts=[])


def _generate_testing_patch_request(
    context: RunContext,
    request: TestingRequest,
    *,
    planner: PlanGenerationService,
    feedback: str | None = None,
    review_round: int = 1,
) -> TestingRequest:
    emit_progress(
        "agent_status",
        stage="testing",
        message="测试方案已通过，正在调用 LLM 生成完整测试补丁草案。",
    )
    context.workflow_trace.record(
        "patch_generation_requested",
        stage="testing",
        review_round=review_round,
        feedback=feedback,
    )
    draft = planner.create_testing_patch_draft(
        context,
        request.plan,
        feedback=feedback,
    )
    return replace(request, patch_draft=draft, alternate_patch_drafts=[])


def _generate_repair_patch_request(
    context: RunContext,
    request: RepairRequest,
    *,
    planner: PlanGenerationService,
    feedback: str | None = None,
    review_round: int = 1,
) -> RepairRequest:
    emit_progress(
        "agent_status",
        stage="repair",
        message="修复方案已通过，正在调用 LLM 生成修复补丁草案。",
    )
    context.workflow_trace.record(
        "patch_generation_requested",
        stage="repair",
        review_round=review_round,
        feedback=feedback,
    )
    draft = planner.create_repair_patch_draft(
        context,
        request.plan,
        feedback=feedback,
    )
    return replace(request, patch_draft=draft, alternate_patch_drafts=[])


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


def _supports_incremental_patch_generation(
    planner: PlanGenerationService,
    stage: Literal["implementation", "testing", "repair"],
) -> bool:
    method_name = {
        "implementation": "create_implementation_file_patch_draft",
        "testing": "create_testing_file_patch_draft",
        "repair": "create_repair_file_patch_draft",
    }[stage]
    return hasattr(planner, method_name)


def _generate_and_apply_incremental_patches(
    context: RunContext,
    *,
    stage: Literal["implementation", "testing", "repair"],
    service: ImplementationService | TestingService | RepairService,
    planner: PlanGenerationService,
    plan: ImplementationPlan | TestingPlan | RepairPlan,
    approval_template: ApprovalDecision,
    approval_console: ApprovalConsoleLike,
    feedback: str | None = None,
) -> _IncrementalPatchResult | StageResult:
    started_at = utc_timestamp()
    stage_dir = context.stage_dirs[_stage_enum_for_incremental(stage)]
    fs.mkdir(stage_dir)
    file_patch_dir = stage_dir / "file_patches"
    fs.mkdir(file_patch_dir)
    planned_paths = _planned_paths(plan)
    drafts: list[BaseModel] = []
    completed_files: list[str] = []
    failures: list[dict[str, object]] = []
    work_summary = ""
    auto_approve_file_patches = False

    context.workflow_trace.record(
        "incremental_patch_loop_started",
        stage=stage,
        planned_files=planned_paths,
        scheduled_by="approved_plan_order",
        feedback=feedback,
    )
    emit_progress(
        "agent_status",
        stage=stage,
        message="计划已通过，正在按单文件循环生成、检查、审批并应用补丁。",
    )
    stage_patch_context = _build_stage_patch_context(
        context,
        stage=stage,
        plan=plan,
        stage_dir=stage_dir,
        planned_paths=planned_paths,
    )

    return _run_scheduled_incremental_patch_files(
        context,
        stage=stage,
        service=service,
        planner=planner,
        plan=plan,
        approval_template=approval_template,
        approval_console=approval_console,
        feedback=feedback,
        started_at=started_at,
        stage_dir=stage_dir,
        file_patch_dir=file_patch_dir,
        planned_paths=planned_paths,
        drafts=drafts,
        completed_files=completed_files,
        failures=failures,
        work_summary=work_summary,
        auto_approve_file_patches=auto_approve_file_patches,
        stage_patch_context=stage_patch_context,
    )


def _run_scheduled_incremental_patch_files(
    context: RunContext,
    *,
    stage: Literal["implementation", "testing", "repair"],
    service: ImplementationService | TestingService | RepairService,
    planner: PlanGenerationService,
    plan: ImplementationPlan | TestingPlan | RepairPlan,
    approval_template: ApprovalDecision,
    approval_console: ApprovalConsoleLike,
    feedback: str | None,
    started_at: str,
    stage_dir: Path,
    file_patch_dir: Path,
    planned_paths: list[str],
    drafts: list[BaseModel],
    completed_files: list[str],
    failures: list[dict[str, object]],
    work_summary: str,
    auto_approve_file_patches: bool,
    stage_patch_context: _StagePatchContext,
) -> _IncrementalPatchResult | StageResult:
    if not planned_paths:
        return _failed_stage_result(
            stage=stage,
            started_at=started_at,
            summary="Incremental patch loop has no planned files.",
            category="model",
            message="Approved plan did not contain any file changes.",
            next_suggestion="Regenerate the plan with explicit file changes.",
        )

    for scheduled_index, target_key in enumerate(planned_paths, start=1):
        if target_key in completed_files:
            context.workflow_trace.record(
                "incremental_scheduled_file_skipped",
                stage=stage,
                target_path=target_key,
                reason="already completed",
            )
            continue
        target = Path(target_key)
        context.workflow_trace.record(
            "incremental_stage_patch_context_reused",
            stage=stage,
            target_path=target_key,
            context_path=_run_relative_path(
                stage_patch_context.context_path,
                run_dir=context.run_dir,
            ),
            applied_context_path=_run_relative_path(
                stage_patch_context.applied_context_path,
                run_dir=context.run_dir,
            ),
            applied_context_chars=len(stage_patch_context.applied_file_context),
        )
        single = _generate_approve_apply_single_file_patch(
            context,
            stage=stage,
            service=service,
            planner=planner,
            plan=plan,
            target_path=target,
            read_context=_stage_patch_context_for_prompt(stage_patch_context),
            work_summary=work_summary,
            completed_files=completed_files,
            failures=failures,
            approval_template=approval_template,
            approval_console=approval_console,
            file_patch_dir=file_patch_dir,
            file_index=len(drafts) + 1,
            auto_approve=auto_approve_file_patches,
        )
        if isinstance(single, StageResult):
            return single
        if single.auto_approve_remaining:
            auto_approve_file_patches = True
        drafts.append(single.draft)
        _write_incremental_aggregate_artifacts(
            context,
            stage=stage,
            service=service,
            plan=plan,
            drafts=drafts,
        )
        _update_stage_patch_context_after_apply(
            context,
            stage_patch_context=stage_patch_context,
            target_path=target,
            draft=single.draft,
            changed_files=single.changed_files,
        )
        for changed_file in single.changed_files:
            if changed_file not in completed_files:
                completed_files.append(changed_file)
        if target_key not in completed_files:
            completed_files.append(target_key)
        work_summary = _append_work_summary(
            work_summary,
            _single_file_summary(
                index=len(drafts),
                target_path=target_key,
                draft=single.draft,
                changed_files=single.changed_files,
            ),
        )
        _write_incremental_work_summary(stage_dir, work_summary)
        context.workflow_trace.record(
            "incremental_file_patch_applied",
            stage=stage,
            target_path=target_key,
            changed_files=single.changed_files,
            patch_path=_run_relative_path(single.patch_path, run_dir=context.run_dir),
        )

    if drafts:
        context.workflow_trace.record(
            "incremental_patch_loop_completed",
            stage=stage,
            completed_files=completed_files,
            failures=failures,
        )
        return _IncrementalPatchResult(
            drafts=drafts,
            completed_files=completed_files,
            failures=failures,
            work_summary=work_summary,
        )
    return _failed_stage_result(
        stage=stage,
        started_at=started_at,
        summary="Incremental patch loop completed without generating any file patch.",
        category="model",
        message="All scheduled files were skipped before producing a patch draft.",
        next_suggestion="Regenerate the plan with explicit uncompleted file changes.",
    )


def _build_stage_patch_context(
    context: RunContext,
    *,
    stage: Literal["implementation", "testing", "repair"],
    plan: ImplementationPlan | TestingPlan | RepairPlan,
    stage_dir: Path,
    planned_paths: list[str],
) -> _StagePatchContext:
    workspace_tree = _visible_workspace_tree(context)
    sections: list[str] = []
    entries: list[dict[str, object]] = []
    budget = STAGE_PATCH_CONTEXT_MAX_CHARS
    truncated = False

    budget, section_truncated = _append_stage_context_text_section(
        sections,
        entries,
        label="project_tree",
        text=workspace_tree,
        budget=budget,
    )
    truncated = truncated or section_truncated

    for path in _stage_input_material_paths(context):
        budget, section_truncated = _append_stage_context_file_section(
            sections,
            entries,
            label=f"input/{path.name}",
            path=path,
            budget=budget,
        )
        truncated = truncated or section_truncated
        if budget <= 0:
            break

    if stage == "repair" and budget > 0:
        for path in _stage_patch_evidence_paths(context):
            budget, section_truncated = _append_stage_context_file_section(
                sections,
                entries,
                label=f"stage_artifact/{path.name}",
                path=path,
                budget=budget,
            )
            truncated = truncated or section_truncated
            if budget <= 0:
                break

    for target in planned_paths:
        if budget <= 0:
            break
        budget, section_truncated = _append_planned_target_context_section(
            context,
            sections,
            entries,
            target_path=Path(target),
            budget=budget,
        )
        truncated = truncated or section_truncated

    for path in _stage_project_context_paths(
        context,
        stage=stage,
        planned_paths=planned_paths,
    ):
        if budget <= 0:
            break
        try:
            label = path.resolve().relative_to(
                context.task_config.project_path.resolve()
            ).as_posix()
        except ValueError:
            label = path.name
        budget, section_truncated = _append_stage_context_file_section(
            sections,
            entries,
            label=f"project/{label}",
            path=path,
            budget=budget,
        )
        truncated = truncated or section_truncated

    initial_context = "\n\n".join(sections) if sections else "(no stage context selected)"
    stage_context = _StagePatchContext(
        stage=stage,
        workspace_tree=workspace_tree,
        initial_context=initial_context,
        applied_file_context="",
        context_path=stage_dir / "stage_patch_context.md",
        metadata_path=stage_dir / "stage_patch_context.json",
        applied_context_path=stage_dir / "applied_file_context.md",
        entries=entries,
        budget_chars=STAGE_PATCH_CONTEXT_MAX_CHARS,
        truncated=truncated,
    )
    _write_stage_patch_context_artifacts(context, stage_context, plan=plan)
    context.workflow_trace.record(
        "incremental_stage_patch_context_built",
        stage=stage,
        planned_files=planned_paths,
        context_path=_run_relative_path(
            stage_context.context_path,
            run_dir=context.run_dir,
        ),
        metadata_path=_run_relative_path(
            stage_context.metadata_path,
            run_dir=context.run_dir,
        ),
        applied_context_path=_run_relative_path(
            stage_context.applied_context_path,
            run_dir=context.run_dir,
        ),
        entry_count=len(entries),
        context_chars=len(initial_context),
        budget_chars=STAGE_PATCH_CONTEXT_MAX_CHARS,
        truncated=truncated,
    )
    emit_progress(
        "agent_status",
        stage=stage,
        message=(
            "已在阶段开始读取并缓存补丁上下文；后续单文件补丁将复用该上下文。"
        ),
    )
    return stage_context


def _stage_patch_context_for_prompt(stage_context: _StagePatchContext) -> str:
    applied = stage_context.applied_file_context.strip()
    sections = [
        (
            "## Stage context snapshot\n"
            "The workflow read this visible context once at the start of the current "
            "stage. Later file patch calls reuse this snapshot; do not assume another "
            "workspace read will happen during this stage.\n\n"
            f"{stage_context.initial_context}"
        )
    ]
    if applied:
        sections.append(
            "## Approved file patches applied after the stage snapshot\n" + applied
        )
    else:
        sections.append(
            "## Approved file patches applied after the stage snapshot\n"
            "(no file patches have been applied in this stage yet)"
        )
    return "\n\n".join(sections)


def _write_stage_patch_context_artifacts(
    context: RunContext,
    stage_context: _StagePatchContext,
    *,
    plan: ImplementationPlan | TestingPlan | RepairPlan,
) -> None:
    fs.write_text(stage_context.context_path, stage_context.initial_context)
    fs.write_text(
        stage_context.applied_context_path,
        "(no approved file patches have been applied in this stage yet)\n",
    )
    fs.write_text(
        stage_context.metadata_path,
        json.dumps(
            {
                "stage": stage_context.stage,
                "budget_chars": stage_context.budget_chars,
                "context_chars": len(stage_context.initial_context),
                "truncated": stage_context.truncated,
                "planned_files": _planned_paths(plan),
                "entries": stage_context.entries,
            },
            indent=2,
            ensure_ascii=False,
        ),
    )


def _update_stage_patch_context_after_apply(
    context: RunContext,
    *,
    stage_patch_context: _StagePatchContext,
    target_path: Path,
    draft: BaseModel,
    changed_files: list[str],
) -> None:
    addition = _applied_file_context_from_draft(draft)
    if not addition:
        return
    combined = "\n\n".join(
        item
        for item in [stage_patch_context.applied_file_context.strip(), addition.strip()]
        if item
    )
    if len(combined) > STAGE_PATCH_APPLIED_CONTEXT_MAX_CHARS:
        marker = "[older applied file context truncated]\n"
        keep = max(0, STAGE_PATCH_APPLIED_CONTEXT_MAX_CHARS - len(marker))
        combined = marker + combined[-keep:]
    stage_patch_context.applied_file_context = combined
    fs.write_text(stage_patch_context.applied_context_path, combined + "\n")
    context.workflow_trace.record(
        "incremental_stage_patch_context_updated",
        stage=stage_patch_context.stage,
        target_path=target_path.as_posix(),
        changed_files=changed_files,
        applied_context_path=_run_relative_path(
            stage_patch_context.applied_context_path,
            run_dir=context.run_dir,
        ),
        applied_context_chars=len(combined),
    )


def _applied_file_context_from_draft(draft: BaseModel) -> str:
    sections: list[str] = []
    for change in getattr(draft, "changes", []):
        path = Path(getattr(change, "path")).as_posix()
        new_content = getattr(change, "new_content", None)
        if new_content is None:
            sections.append(f"### applied/{path}\n[file deleted by approved patch]\n")
            continue
        text = redact_sensitive_text(str(new_content))
        if len(text) > STAGE_PATCH_APPLIED_FILE_MAX_CHARS:
            text = text[:STAGE_PATCH_APPLIED_FILE_MAX_CHARS] + "\n[truncated]\n"
        sections.append(f"### applied/{path}\n{text}")
    return "\n\n".join(sections)


def _append_stage_context_text_section(
    sections: list[str],
    entries: list[dict[str, object]],
    *,
    label: str,
    text: str,
    budget: int,
) -> tuple[int, bool]:
    if budget <= 0:
        return budget, False
    original_chars = len(text)
    truncated = original_chars > budget
    if truncated:
        text = text[:budget] + "\n[truncated]\n"
    sections.append(f"### {label}\n{text}")
    entries.append(
        {
            "label": label,
            "chars": len(text),
            "original_chars": original_chars,
            "truncated": truncated,
        }
    )
    return budget - len(text), truncated


def _append_stage_context_file_section(
    sections: list[str],
    entries: list[dict[str, object]],
    *,
    label: str,
    path: Path,
    budget: int,
) -> tuple[int, bool]:
    text = _read_context_text_file(path)
    if text is None:
        return budget, False
    remaining, truncated = _append_stage_context_text_section(
        sections,
        entries,
        label=label,
        text=text,
        budget=budget,
    )
    if entries:
        entries[-1]["path"] = str(path)
    return remaining, truncated


def _append_planned_target_context_section(
    context: RunContext,
    sections: list[str],
    entries: list[dict[str, object]],
    *,
    target_path: Path,
    budget: int,
) -> tuple[int, bool]:
    root = context.task_config.project_path.resolve()
    target = (root / target_path).resolve()
    label = f"planned_target/{target_path.as_posix()}"
    if not _is_visible_project_context_file(context, target):
        text = "[file does not exist at stage context build time]\n"
        if fs.exists(target):
            text = "[read denied by visibility policy]\n"
        return _append_stage_context_text_section(
            sections,
            entries,
            label=label,
            text=text,
            budget=budget,
        )
    return _append_stage_context_file_section(
        sections,
        entries,
        label=label,
        path=target,
        budget=budget,
    )


def _stage_input_material_paths(context: RunContext) -> list[Path]:
    paths: list[Path] = []
    for material in context.task_config.input_materials:
        paths.extend(_iter_stage_context_text_files(material.path))
    return _dedupe_path_list(paths)


def _stage_project_context_paths(
    context: RunContext,
    *,
    stage: Literal["implementation", "testing", "repair"],
    planned_paths: list[str],
) -> list[Path]:
    root = context.task_config.project_path.resolve()
    planned = {(root / Path(path)).resolve() for path in planned_paths}
    paths: list[Path] = []
    for filename in sorted(STAGE_CONTEXT_CONFIG_FILENAMES):
        candidate = root / filename
        if _is_visible_project_context_file(context, candidate):
            paths.append(candidate)
    for path in _iter_stage_context_text_files(root):
        resolved = path.resolve()
        if resolved in planned or not _is_visible_project_context_file(context, resolved):
            continue
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            continue
        if stage in {"implementation", "testing"} and _is_test_artifact_path(relative):
            continue
        paths.append(resolved)
    return _dedupe_path_list(paths)


def _stage_patch_evidence_paths(context: RunContext) -> list[Path]:
    candidates = [
        context.stage_dirs[Stage.REPAIR] / "repair_test_result.json",
        context.stage_dirs[Stage.REPAIR] / "after_test.log",
        context.stage_dirs[Stage.REPAIR] / "repair_report.md",
        context.stage_dirs[Stage.REPAIR] / "stage_result.json",
        context.stage_dirs[Stage.REPAIR] / "changed_files.json",
        context.stage_dirs[Stage.DEBUG] / "failure_summary.md",
        context.stage_dirs[Stage.DEBUG] / "llm_debug_analysis.json",
        context.stage_dirs[Stage.DEBUG] / "llm_debug_analysis.md",
        context.stage_dirs[Stage.DEBUG] / "fault_localization.json",
        context.stage_dirs[Stage.DEBUG] / "root_cause.md",
        context.stage_dirs[Stage.DEBUG] / "repair_plan.md",
        context.stage_dirs[Stage.DEBUG] / "debug_report.md",
        context.stage_dirs[Stage.TEST] / "test_result.json",
        context.stage_dirs[Stage.TEST] / "test_command.json",
        context.stage_dirs[Stage.TEST] / "test_report.md",
        context.stage_dirs[Stage.TEST] / "test_report.json",
        context.stage_dirs[Stage.TEST] / "stage_result.json",
    ]
    logs_dir = context.stage_dirs[Stage.REPAIR] / "logs"
    if fs.exists(logs_dir):
        candidates.extend(sorted(logs_dir.glob("*.stdout.log")))
        candidates.extend(sorted(logs_dir.glob("*.stderr.log")))
    logs_dir = context.stage_dirs[Stage.TEST] / "logs"
    if fs.exists(logs_dir):
        candidates.extend(sorted(logs_dir.glob("*.stdout.log")))
        candidates.extend(sorted(logs_dir.glob("*.stderr.log")))
    return _dedupe_path_list(
        [path for path in candidates if fs.exists(path) and fs.is_file(path)]
    )


def _is_visible_project_context_file(context: RunContext, path: Path) -> bool:
    if not fs.exists(path) or not fs.is_file(path):
        return False
    if path.suffix.lower() not in STAGE_CONTEXT_TEXT_EXTENSIONS:
        return False
    root = context.task_config.project_path.resolve()
    hidden_roots = [
        hidden.resolve() for hidden in context.task_config.agent_visibility.hidden_paths
    ]
    try:
        SensitiveFilter(
            root,
            visible_roots=[root],
            hidden_roots=hidden_roots,
        ).ensure_allowed(path.resolve())
    except (PermissionError, ValueError, OSError):
        return False
    return True


def _iter_stage_context_text_files(path: Path) -> list[Path]:
    path = path.resolve()
    if fs.is_file(path):
        return [path] if path.suffix.lower() in STAGE_CONTEXT_TEXT_EXTENSIONS else []
    if not fs.is_dir(path):
        return []
    return [
        candidate
        for candidate in sorted(path.rglob("*"))
        if fs.is_file(candidate)
        and candidate.suffix.lower() in STAGE_CONTEXT_TEXT_EXTENSIONS
    ]


def _read_context_text_file(path: Path) -> str | None:
    try:
        text = fs.read_text(path)
    except UnicodeDecodeError:
        text = fs.portable_path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return redact_sensitive_text(text)


def _dedupe_path_list(paths: list[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(path)
    return deduped


def _is_test_artifact_path(path: str) -> bool:
    normalized = PurePosixPath(path.replace("\\", "/"))
    return (
        "tests" in normalized.parts
        or normalized.name.startswith("test_")
        or normalized.name.endswith("_test.py")
        or normalized.name == "conftest.py"
        or normalized.name in {"pytest.ini", "tox.ini"}
    )


def _generate_approve_apply_single_file_patch(
    context: RunContext,
    *,
    stage: Literal["implementation", "testing", "repair"],
    service: ImplementationService | TestingService | RepairService,
    planner: PlanGenerationService,
    plan: ImplementationPlan | TestingPlan | RepairPlan,
    target_path: Path,
    read_context: str,
    work_summary: str,
    completed_files: list[str],
    failures: list[dict[str, object]],
    approval_template: ApprovalDecision,
    approval_console: ApprovalConsoleLike,
    file_patch_dir: Path,
    file_index: int,
    auto_approve: bool = False,
) -> _SingleFilePatchResult | StageResult:
    started_at = utc_timestamp()
    target_key = target_path.as_posix()
    local_feedback: str | None = None
    attempts_for_file: list[dict[str, object]] = []
    max_attempts = max(1, context.task_config.model.max_retries + 1)
    for attempt in range(1, max_attempts + 1):
        emit_progress(
            "agent_status",
            stage=stage,
            message=f"正在生成单文件补丁 {file_index}.{attempt}: {target_key}",
        )
        draft = _create_single_file_patch_draft(
            context,
            planner=planner,
            stage=stage,
            plan=plan,
            target_path=target_path,
            read_context=read_context,
            work_summary=work_summary,
            completed_files=completed_files,
            failures=failures[-8:],
            feedback=local_feedback,
        )
        prepared, validation_attempts = _prepare_single_file_candidate(
            service,
            plan=plan,
            draft=draft,
        )
        attempts_for_file.extend(validation_attempts)
        if prepared is None:
            error = _incremental_last_attempt_error(validation_attempts)
            failure = {
                "target_path": target_key,
                "attempt": attempt,
                "status": "validation_failed",
                "error": error,
                "attempts": validation_attempts,
            }
            failures.append(failure)
            _write_incremental_failures(
                context.stage_dirs[_stage_enum_for_incremental(stage)],
                failures,
            )
            local_feedback = (
                "The previous single-file patch for "
                f"{target_key} failed validation: {error}. Regenerate only this file "
                "and avoid repeating the same mistake."
            )
            _emit_single_file_retry_message(
                context,
                stage=stage,
                target_path=target_key,
                attempt=attempt,
                reason="补丁校验失败",
                detail=error,
            )
            continue
        risk_error = _prepared_risk_error(stage, prepared)
        if risk_error:
            failure = {
                "target_path": target_key,
                "attempt": attempt,
                "status": "risk_failed",
                "error": risk_error,
            }
            failures.append(failure)
            _write_incremental_failures(
                context.stage_dirs[_stage_enum_for_incremental(stage)],
                failures,
            )
            local_feedback = (
                f"The previous single-file patch for {target_key} failed risk checks: "
                f"{risk_error}. Regenerate only this file and avoid the risky change."
            )
            _emit_single_file_retry_message(
                context,
                stage=stage,
                target_path=target_key,
                attempt=attempt,
                reason="风险检查未通过",
                detail=risk_error,
            )
            continue

        import_error = _single_file_local_import_error(context, draft, plan=plan)
        if import_error:
            failure = {
                "target_path": target_key,
                "attempt": attempt,
                "status": "local_import_failed",
                "error": import_error,
            }
            failures.append(failure)
            _write_incremental_failures(
                context.stage_dirs[_stage_enum_for_incremental(stage)],
                failures,
            )
            local_feedback = (
                f"The previous single-file patch for {target_key} introduced an "
                f"invalid local import: {import_error}. Regenerate only this file; "
                "use existing workspace modules/APIs and do not invent local modules."
            )
            _emit_single_file_retry_message(
                context,
                stage=stage,
                target_path=target_key,
                attempt=attempt,
                reason="本地导入检查未通过",
                detail=import_error,
            )
            continue

        patch_text = _prepared_patch_text(prepared)
        patch_sha256 = _sha256_text(patch_text)
        patch_path, draft_json_path = _write_single_file_patch_artifacts(
            file_patch_dir,
            file_index=file_index,
            attempt=attempt,
            target_path=target_path,
            draft=draft,
            patch_text=patch_text,
        )
        approval = _approve_single_file_patch(
            context,
            stage=stage,
            approval_template=approval_template,
            approval_console=approval_console,
            target_path=target_key,
            patch_path=patch_path,
            draft_json_path=draft_json_path,
            prepared=prepared,
            patch_sha256=patch_sha256,
            auto_approve=auto_approve,
        )
        _record_approval_decision(
            context,
            stage=stage,
            action=_patch_approval_action(stage),
            approval=approval,
        )
        if approval.decision_type == "respond":
            feedback = _feedback_from_decision(approval)
            failures.append(
                {
                    "target_path": target_key,
                    "attempt": attempt,
                    "status": "reviewer_feedback",
                    "error": feedback,
                }
            )
            _write_incremental_failures(
                context.stage_dirs[_stage_enum_for_incremental(stage)],
                failures,
            )
            local_feedback = (
                f"Reviewer feedback for {target_key}: {feedback}. Regenerate only this file."
            )
            _emit_single_file_retry_message(
                context,
                stage=stage,
                target_path=target_key,
                attempt=attempt,
                reason="收到人工调整意见",
                detail=feedback,
            )
            continue
        if approval.decision_type in {"reject", "cancel"}:
            return _incremental_patch_decision_result(
                stage=stage,
                started_at=started_at,
                target_path=target_key,
                approval=approval,
            )
        if approval.decision_type != "approve":
            return _failed_stage_result(
                stage=stage,
                started_at=started_at,
                summary="Single-file patch approval returned an unsupported decision.",
                category="hitl",
                message=f"Unsupported decision for single-file patch: {approval.decision_type}",
                next_suggestion="Apply the patch or respond with concrete feedback.",
            )
        try:
            applied = service.patch_service.apply_patch(
                patch_path,
                context.task_config.project_path,
                operation_id=f"{stage}_single_file_patch_{file_index}_{attempt}",
            )
        except (PatchApplyError, PatchValidationError, OSError, ValueError) as exc:
            error = str(exc)
            failures.append(
                {
                    "target_path": target_key,
                    "attempt": attempt,
                    "status": "apply_failed",
                    "error": error,
                }
            )
            _write_incremental_failures(
                context.stage_dirs[_stage_enum_for_incremental(stage)],
                failures,
            )
            local_feedback = (
                "The previous approved single-file patch for "
                f"{target_key} could not be applied: {error}. Regenerate only this "
                "file against the current workspace content."
            )
            _emit_single_file_retry_message(
                context,
                stage=stage,
                target_path=target_key,
                attempt=attempt,
                reason="补丁应用失败",
                detail=error,
            )
            continue
        return _SingleFilePatchResult(
            draft=draft,
            changed_files=applied.changed_files,
            patch_path=patch_path,
            patch_sha256=patch_sha256,
            attempts=attempts_for_file,
            auto_approve_remaining=_approval_enables_stage_auto_approve(approval),
        )

    return _failed_stage_result(
        stage=stage,
        started_at=started_at,
        summary=f"Single-file patch generation failed for {target_key}.",
        category="patch",
        message=_incremental_last_attempt_error(attempts_for_file),
        next_suggestion="Inspect the single-file patch attempts and regenerate this target file.",
    )


def _create_single_file_patch_draft(
    context: RunContext,
    *,
    planner: PlanGenerationService,
    stage: Literal["implementation", "testing", "repair"],
    plan: ImplementationPlan | TestingPlan | RepairPlan,
    target_path: Path,
    read_context: str,
    work_summary: str,
    completed_files: list[str],
    failures: list[dict[str, object]],
    feedback: str | None,
) -> BaseModel:
    common = {
        "target_path": target_path,
        "workspace_context": read_context,
        "work_summary": work_summary,
        "completed_files": completed_files,
        "failed_attempts": failures,
        "feedback": feedback,
    }
    if stage == "implementation":
        return planner.create_implementation_file_patch_draft(
            context,
            plan,  # type: ignore[arg-type]
            **common,
        )
    if stage == "testing":
        return planner.create_testing_file_patch_draft(
            context,
            plan,  # type: ignore[arg-type]
            **common,
        )
    return planner.create_repair_file_patch_draft(
        context,
        plan,  # type: ignore[arg-type]
        **common,
    )


def _prepare_single_file_candidate(
    service: ImplementationService | TestingService | RepairService,
    *,
    plan: ImplementationPlan | TestingPlan | RepairPlan,
    draft: BaseModel,
) -> tuple[Any | None, list[dict[str, object]]]:
    return service._prepare_patch_candidates(plan, [draft])  # type: ignore[attr-defined,arg-type]


def _single_file_local_import_error(
    context: RunContext,
    draft: BaseModel,
    *,
    plan: BaseModel | None = None,
) -> str:
    root = context.task_config.project_path.resolve()
    changes = getattr(draft, "changes", [])
    new_paths = {
        Path(getattr(change, "path")).as_posix()
        for change in changes
        if getattr(change, "new_content", None) is not None
    }
    planned_paths = _plan_python_paths(plan)
    local_roots = _local_python_roots(root, new_paths | planned_paths)
    if not local_roots:
        return ""
    for change in changes:
        path = Path(getattr(change, "path"))
        if path.suffix != ".py":
            continue
        new_content = getattr(change, "new_content", None)
        if not isinstance(new_content, str):
            continue
        try:
            tree = ast.parse(new_content)
        except SyntaxError:
            continue
        for module in _imported_modules(tree, target_path=path):
            if not module:
                continue
            top_level = module.split(".", 1)[0]
            if top_level not in local_roots:
                continue
            if not _local_module_exists(root, module, new_paths | planned_paths):
                return (
                    f"{path.as_posix()} imports local module {module!r}, "
                    "but that module is not present in the current workspace, this patch, "
                    "or the approved plan."
                )
    return ""


def _plan_python_paths(plan: BaseModel | None) -> set[str]:
    if plan is None:
        return set()
    paths: set[str] = set()
    for change in getattr(plan, "changes", []):
        raw_path = getattr(change, "path", None)
        if raw_path is None:
            continue
        path = Path(raw_path)
        if path.suffix == ".py":
            paths.add(path.as_posix())
    return paths


def _local_python_roots(root: Path, new_paths: set[str]) -> set[str]:
    roots: set[str] = set()
    try:
        children = list(root.iterdir())
    except OSError:
        children = []
    for child in children:
        name = child.name
        if fs.is_file(child) and child.suffix == ".py":
            roots.add(child.stem)
        elif fs.is_dir(child) and fs.exists(child / "__init__.py"):
            roots.add(name)
    for raw_path in new_paths:
        parts = PurePosixPath(raw_path).parts
        if not parts:
            continue
        if len(parts) == 1 and parts[0].endswith(".py"):
            roots.add(PurePosixPath(parts[0]).stem)
        elif len(parts) >= 2 and parts[1] == "__init__.py":
            roots.add(parts[0])
    return roots


def _imported_modules(tree: ast.AST, *, target_path: Path) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_import_from_module(node, target_path=target_path)
            if module:
                modules.append(module)
    return modules


def _resolved_import_from_module(node: ast.ImportFrom, *, target_path: Path) -> str:
    module = node.module or ""
    if node.level <= 0:
        return module
    package_parts = list(target_path.parent.parts)
    if len(package_parts) < node.level:
        return module
    base = package_parts[: len(package_parts) - node.level + 1]
    parts = [*base]
    if module:
        parts.extend(module.split("."))
    return ".".join(part for part in parts if part)


def _local_module_exists(root: Path, module: str, new_paths: set[str]) -> bool:
    module_path = PurePosixPath(*module.split("."))
    file_path = f"{module_path.as_posix()}.py"
    package_path = f"{module_path.as_posix()}/__init__.py"
    if file_path in new_paths or package_path in new_paths:
        return True
    return fs.exists(root / Path(file_path)) or fs.exists(root / Path(package_path))


def _approve_single_file_patch(
    context: RunContext,
    *,
    stage: Literal["implementation", "testing", "repair"],
    approval_template: ApprovalDecision,
    approval_console: ApprovalConsoleLike,
    target_path: str,
    patch_path: Path,
    draft_json_path: Path,
    prepared: Any,
    patch_sha256: str,
    auto_approve: bool = False,
) -> ApprovalDecision:
    default = ApprovalDecision(
        interrupt_id=_patch_interrupt_id(stage),
        decision_type="approve",
        comment=f"Generated single-file patch for {target_path}.",
        auto=approval_template.auto,
        decided_by=approval_template.decided_by,
        decision_source=approval_template.decision_source,
        presented_to_user=False,
    )
    if auto_approve:
        approval = _auto_approve_single_file_patch(
            stage=stage,
            target_path=target_path,
        )
        _emit_auto_approved_patch_message(
            context,
            stage=stage,
            target_path=target_path,
        )
        return approval
    if not _should_prompt_for_approval(context, default):
        return _effective_approval(context, default)
    payload = {
        "interrupt_id": _patch_interrupt_id(stage),
        "action": _patch_approval_action(stage),
        "title": _single_file_patch_title(stage),
        "summary": f"目标文件：{target_path}。审批通过后将立即写入该文件。",
        "risk_level": _prepared_risk_level(stage, prepared),
        "allowed_decisions": ["approve", "respond"],
        "default_decision": "approve",
        "payload": {
            "patch_path": _run_relative_path(patch_path, run_dir=context.run_dir),
            "patch_draft_json_path": _run_relative_path(draft_json_path, run_dir=context.run_dir),
            "changed_files": _prepared_changed_files(prepared),
            "added_lines": getattr(prepared.summary, "added_lines", 0),
            "removed_lines": getattr(prepared.summary, "removed_lines", 0),
            "risk_level": _prepared_risk_level(stage, prepared),
            "patch_sha256": patch_sha256,
        },
    }
    return _prompt_approval(context, payload, approval_console)


def _visible_workspace_tree(context: RunContext, *, max_entries: int = 500) -> str:
    root = context.task_config.project_path.resolve()
    hidden_roots = [
        path.resolve() for path in context.task_config.agent_visibility.hidden_paths
    ]
    sensitive_filter = SensitiveFilter(
        root,
        visible_roots=[root],
        hidden_roots=hidden_roots,
    )
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        if len(lines) >= max_entries:
            lines.append(f"... truncated after {max_entries} visible entries")
            break
        try:
            if sensitive_filter.is_denied(path):
                continue
            relative = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if not relative:
            continue
        if fs.is_dir(path):
            lines.append(f"[dir]  {relative}/")
        elif fs.is_file(path):
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            lines.append(f"[file] {relative} ({size} bytes)")
    return "\n".join(lines) if lines else "(no visible files)"


def _write_single_file_patch_artifacts(
    file_patch_dir: Path,
    *,
    file_index: int,
    attempt: int,
    target_path: Path,
    draft: BaseModel,
    patch_text: str,
) -> tuple[Path, Path]:
    slug = _path_slug(target_path)
    draft_json_path = file_patch_dir / f"{file_index:03d}_{attempt:02d}_{slug}.json"
    patch_path = file_patch_dir / f"{file_index:03d}_{attempt:02d}_{slug}.patch.diff"
    fs.write_text(
        draft_json_path,
        json.dumps(draft.model_dump(mode="json"), indent=2, ensure_ascii=False),
    )
    fs.write_text(patch_path, patch_text)
    return patch_path, draft_json_path


def _write_incremental_work_summary(stage_dir: Path, work_summary: str) -> None:
    fs.write_text(stage_dir / "incremental_work_summary.md", work_summary)


def _write_incremental_failures(
    stage_dir: Path,
    failures: list[dict[str, object]],
) -> None:
    fs.write_text(
        stage_dir / "incremental_patch_failures.json",
        json.dumps({"failures": failures}, indent=2, ensure_ascii=False),
    )


def _append_work_summary(current: str, addition: str) -> str:
    addition = addition.strip()
    if not addition:
        return current
    if not current.strip():
        combined = f"- {addition}\n"
    else:
        combined = current.rstrip() + f"\n- {addition}\n"
    if len(combined) <= WORK_SUMMARY_MAX_CHARS:
        return combined
    marker = "[older incremental work summary truncated]\n"
    keep = max(0, WORK_SUMMARY_MAX_CHARS - len(marker))
    return marker + combined[-keep:]


def _single_file_summary(
    *,
    index: int,
    target_path: str,
    draft: BaseModel,
    changed_files: list[str],
) -> str:
    summary = str(getattr(draft, "plan_summary", "")).strip()
    changed = ", ".join(changed_files) or target_path
    return f"文件 {index} `{target_path}` 已应用；changed_files={changed}；summary={summary}"


def _planned_paths(plan: ImplementationPlan | TestingPlan | RepairPlan) -> list[str]:
    paths: list[str] = []
    for change in plan.changes:
        path = change.path.as_posix()
        if path not in paths:
            paths.append(path)
    return paths


def _combine_implementation_patch_drafts(
    plan: ImplementationPlan,
    drafts: list[BaseModel],
) -> ImplementationPatchDraft:
    changes = []
    syntax_targets: list[Path] = []
    summaries: list[str] = []
    for draft in drafts:
        typed = ImplementationPatchDraft.model_validate(draft.model_dump(mode="json"))
        changes.extend(typed.changes)
        syntax_targets.extend(typed.syntax_check_targets)
        summaries.append(typed.plan_summary)
    return ImplementationPatchDraft(
        plan_summary="\n".join(_dedupe_lines([item for item in summaries if item]))
        or plan.implementation_strategy,
        changes=changes,
        syntax_check_targets=_dedupe_paths(syntax_targets),
    )


def _combine_testing_patch_drafts(
    plan: TestingPlan,
    drafts: list[BaseModel],
) -> TestingPatchDraft:
    changes = []
    summaries: list[str] = []
    framework = plan.framework
    for draft in drafts:
        typed = TestingPatchDraft.model_validate(draft.model_dump(mode="json"))
        changes.extend(typed.changes)
        summaries.append(typed.plan_summary)
        framework = typed.framework or framework
    return TestingPatchDraft(
        plan_summary="\n".join(_dedupe_lines([item for item in summaries if item]))
        or plan.strategy,
        changes=changes,
        command=plan.command,
        framework=framework,
    )


def _combine_repair_patch_drafts(
    plan: RepairPlan,
    drafts: list[BaseModel],
) -> RepairPatchDraft:
    changes = []
    summaries: list[str] = []
    command = plan.verification_command
    framework = plan.framework
    for draft in drafts:
        typed = RepairPatchDraft.model_validate(draft.model_dump(mode="json"))
        changes.extend(typed.changes)
        summaries.append(typed.plan_summary)
        command = typed.verification_command or command
        framework = typed.framework or framework
    return RepairPatchDraft(
        plan_summary="\n".join(_dedupe_lines([item for item in summaries if item]))
        or plan.strategy,
        changes=changes,
        verification_command=command,
        framework=framework,
    )


def _write_incremental_aggregate_artifacts(
    context: RunContext,
    *,
    stage: Literal["implementation", "testing", "repair"],
    service: ImplementationService | TestingService | RepairService,
    plan: ImplementationPlan | TestingPlan | RepairPlan,
    drafts: list[BaseModel],
) -> None:
    """Refresh stage-level patch artifacts after each approved single-file patch."""
    if not drafts:
        return
    stage_dir = context.stage_dirs[_stage_enum_for_incremental(stage)]
    fs.mkdir(stage_dir)
    if stage == "implementation":
        typed_draft = _combine_implementation_patch_drafts(plan, drafts)  # type: ignore[arg-type]
        service._write_plan(plan)  # type: ignore[attr-defined,arg-type]
        service._write_plan_json(plan)  # type: ignore[attr-defined,arg-type]
        service._write_patch_draft_json(typed_draft)  # type: ignore[attr-defined]
        patch_path = stage_dir / "implementation.patch.diff"
    elif stage == "testing":
        typed_draft = _combine_testing_patch_drafts(plan, drafts)  # type: ignore[arg-type]
        service._write_plan(plan)  # type: ignore[attr-defined,arg-type]
        service._write_plan_json(plan)  # type: ignore[attr-defined,arg-type]
        service._write_patch_draft_json(typed_draft)  # type: ignore[attr-defined]
        patch_path = stage_dir / "test.patch.diff"
    else:
        typed_draft = _combine_repair_patch_drafts(plan, drafts)  # type: ignore[arg-type]
        service._write_plan_artifacts(plan)  # type: ignore[attr-defined,arg-type]
        service._write_patch_draft_json(typed_draft)  # type: ignore[attr-defined]
        patch_path = stage_dir / "repair.patch.diff"

    patch = service.patch_service.create_unified_diff(
        _file_changes_from_draft_preserving_none(typed_draft)
    )
    fs.write_text(patch_path, patch.text)
    context.workflow_trace.record(
        "incremental_aggregate_patch_artifacts_written",
        stage=stage,
        patch_path=_run_relative_path(patch_path, run_dir=context.run_dir),
        changed_files=patch.changed_files,
        completed_patch_count=len(drafts),
    )


def _write_final_incremental_patch_artifacts(
    context: RunContext,
    *,
    stage: Literal["implementation", "testing", "repair"],
    service: ImplementationService | TestingService | RepairService,
    plan: ImplementationPlan | TestingPlan | RepairPlan,
    draft: BaseModel,
    started_at: str,
) -> str | StageResult:
    stage_dir = context.stage_dirs[_stage_enum_for_incremental(stage)]
    fs.mkdir(stage_dir)
    if stage == "implementation":
        typed_draft = ImplementationPatchDraft.model_validate(draft.model_dump(mode="json"))
        service._write_plan(plan)  # type: ignore[attr-defined,arg-type]
        service._write_plan_json(plan)  # type: ignore[attr-defined,arg-type]
        service._write_patch_draft_json(typed_draft)  # type: ignore[attr-defined]
        patch_path = stage_dir / "implementation.patch.diff"
    elif stage == "testing":
        typed_draft = TestingPatchDraft.model_validate(draft.model_dump(mode="json"))
        service._write_plan(plan)  # type: ignore[attr-defined,arg-type]
        service._write_plan_json(plan)  # type: ignore[attr-defined,arg-type]
        service._write_patch_draft_json(typed_draft)  # type: ignore[attr-defined]
        patch_path = stage_dir / "test.patch.diff"
    else:
        typed_draft = RepairPatchDraft.model_validate(draft.model_dump(mode="json"))
        service._write_plan_artifacts(plan)  # type: ignore[attr-defined,arg-type]
        service._write_patch_draft_json(typed_draft)  # type: ignore[attr-defined]
        patch_path = stage_dir / "repair.patch.diff"

    patch = service.patch_service.create_unified_diff(
        _file_changes_from_draft_preserving_none(typed_draft)
    )
    fs.write_text(patch_path, patch.text)
    validation = service.patch_service.validate_patch(
        patch_path,
        context.task_config.project_path,
    )
    if not validation.valid:
        service._write_attempts(  # type: ignore[attr-defined]
            [
                {
                    "attempt": 1,
                    "status": "validation_failed",
                    "error": "; ".join(validation.errors),
                    "warnings": validation.warnings,
                }
            ]
        )
        return _failed_stage_result(
            stage=stage,
            started_at=started_at,
            summary="Final incremental patch validation failed.",
            category="patch",
            message="; ".join(validation.errors),
            next_suggestion="Inspect the per-file patches and regenerate the final patch draft.",
        )
    attempt: dict[str, object] = {
        "attempt": 1,
        "status": "valid",
        "changed_files": validation.changed_files,
        "warnings": validation.warnings,
    }
    if stage == "repair":
        risk = service.risk_checker.assess(  # type: ignore[attr-defined]
            validation,
            allow_test_modification=_repair_plan_allows_test_modification(plan),
        )
        service._write_risk_report(risk)  # type: ignore[attr-defined]
        attempt["risk"] = risk.to_json_dict()
        if not risk.allowed:
            service._write_attempts([attempt])  # type: ignore[attr-defined]
            return _failed_stage_result(
                stage=stage,
                started_at=started_at,
                summary="Final incremental repair patch failed risk checks.",
                category="patch",
                message=_repair_risk_message(risk),
                next_suggestion="Regenerate the risky single-file repair patch.",
            )
    else:
        attempt["risk_level"] = validation.risk_report.level
    service._write_attempts([attempt])  # type: ignore[attr-defined]
    return _sha256_text(patch.text)


def _file_changes_from_draft_preserving_none(draft: BaseModel) -> list[FileChange]:
    return [
        FileChange(
            path=change.path,
            old_content=change.old_content,
            new_content=change.new_content,
        )
        for change in getattr(draft, "changes")
    ]


def _repair_risk_message(risk: Any) -> str:
    findings = [
        f"{finding.kind}:{finding.path}:{finding.message}"
        for finding in risk.findings
    ]
    return "; ".join(findings) or "repair risk level is high"


def _repair_plan_allows_test_modification(
    plan: ImplementationPlan | TestingPlan | RepairPlan,
) -> bool:
    return isinstance(plan, RepairPlan) and bool(
        plan.test_repair_allowed
        and plan.failure_origin in {"generated_test_code", "mixed", "test_harness"}
        and (plan.test_repair_rationale or "").strip()
    )


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    deduped: list[Path] = []
    for path in paths:
        key = path.as_posix()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _stage_enum_for_incremental(stage: Literal["implementation", "testing", "repair"]) -> Stage:
    if stage == "testing":
        return Stage.TEST
    if stage == "repair":
        return Stage.REPAIR
    return Stage.IMPLEMENT


def _patch_interrupt_id(stage: Literal["implementation", "testing", "repair"]) -> str:
    if stage == "testing":
        return TEST_PATCH_INTERRUPT_ID
    if stage == "repair":
        return REPAIR_PATCH_INTERRUPT_ID
    return PATCH_INTERRUPT_ID


def _patch_approval_action(stage: Literal["implementation", "testing", "repair"]) -> str:
    if stage == "testing":
        return "approve_test_patch"
    if stage == "repair":
        return "approve_repair_patch"
    return "approve_implementation_patch"


def _single_file_patch_title(stage: Literal["implementation", "testing", "repair"]) -> str:
    if stage == "testing":
        return "应用这个单文件测试补丁？"
    if stage == "repair":
        return "应用这个单文件修复补丁？"
    return "应用这个单文件实现补丁？"


def _prepared_patch_text(prepared: Any) -> str:
    if hasattr(prepared, "patch_text"):
        return str(prepared.patch_text)
    return str(prepared.patch.text)


def _prepared_changed_files(prepared: Any) -> list[str]:
    return list(prepared.validation.changed_files)


def _prepared_risk_level(
    stage: Literal["implementation", "testing", "repair"],
    prepared: Any,
) -> str:
    if stage == "repair" and hasattr(prepared, "risk"):
        return str(prepared.risk.level)
    return str(prepared.validation.risk_report.level)


def _prepared_risk_error(
    stage: Literal["implementation", "testing", "repair"],
    prepared: Any,
) -> str | None:
    if stage == "repair" and hasattr(prepared, "risk") and not prepared.risk.allowed:
        findings = [
            f"{finding.kind}:{finding.path}:{finding.message}"
            for finding in prepared.risk.findings
        ]
        return "; ".join(findings) or "repair risk level is high"
    return None


def _approval_enables_stage_auto_approve(approval: ApprovalDecision) -> bool:
    payload = approval.edited_payload
    return bool(
        isinstance(payload, dict)
        and payload.get(PATCH_AUTO_APPROVE_REMAINING_KEY) is True
    )


def _auto_approve_single_file_patch(
    *,
    stage: Literal["implementation", "testing", "repair"],
    target_path: str,
) -> ApprovalDecision:
    return ApprovalDecision(
        interrupt_id=_patch_interrupt_id(stage),
        decision_type="approve",
        comment=f"Auto-approved single-file patch for {target_path}.",
        decided_by="workflow",
        auto=True,
        decision_source="stage_patch_auto_approve",
        presented_to_user=False,
    )


def _emit_single_file_retry_message(
    context: RunContext,
    *,
    stage: Literal["implementation", "testing", "repair"],
    target_path: str,
    attempt: int,
    reason: str,
    detail: str,
) -> None:
    compact_detail = _compact_retry_detail(detail)
    message = (
        f"单文件补丁 {target_path} 第 {attempt} 次未通过："
        f"{reason}（{compact_detail}）；正在重新生成当前文件。"
    )
    emit_progress("agent_status", stage=stage, message=message)
    context.workflow_trace.record(
        "incremental_file_patch_retry",
        stage=stage,
        target_path=target_path,
        attempt=attempt,
        reason=reason,
        detail=compact_detail,
    )


def _compact_retry_detail(detail: str, *, limit: int = 180) -> str:
    text = " ".join(str(detail or "").split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _emit_auto_approved_patch_message(
    context: RunContext,
    *,
    stage: Literal["implementation", "testing", "repair"],
    target_path: str,
) -> None:
    target_ref = _display_path_ref(target_path, base=context.task_config.project_path)
    emit_progress(
        "agent_status",
        stage=stage,
        message="auto-approved 自动通过补丁，目标文件：",
        message_link=_terminal_link_payload(target_ref),
    )


def _incremental_patch_decision_result(
    *,
    stage: str,
    started_at: str,
    target_path: str,
    approval: ApprovalDecision,
) -> StageResult:
    status: Literal["failed", "cancelled"] = (
        "cancelled" if approval.decision_type == "cancel" else "failed"
    )
    return StageResult(
        stage=stage,
        status=status,
        started_at=started_at,
        ended_at=utc_timestamp(),
        summary=f"Single-file patch approval returned {approval.decision_type}: {target_path}.",
        error=ErrorRecord(
            error_id=f"{stage}_single_file_patch_{approval.decision_type}",
            stage=stage,
            node=_patch_approval_action(stage),  # type: ignore[arg-type]
            category="hitl",
            message=f"single-file patch decision: {approval.decision_type}",
            retryable=status != "cancelled",
        ),
        next_suggestion=(
            "Run was cancelled by the approval decision."
            if status == "cancelled"
            else "Ask the Agent to regenerate the single-file patch or approve it."
        ),
    )


def _incremental_final_patch_approval(
    *,
    stage: Literal["implementation", "testing", "repair"],
) -> ApprovalDecision:
    return ApprovalDecision(
        interrupt_id=_patch_interrupt_id(stage),
        decision_type="approve",
        comment="All single-file patches were approved and applied incrementally.",
        decided_by="workflow",
        auto=True,
        decision_source="incremental_file_approvals",
        presented_to_user=False,
    )


def _incremental_last_attempt_error(attempts: list[dict[str, object]]) -> str:
    for attempt in reversed(attempts):
        error = attempt.get("error")
        if error:
            return str(error)
    return "single-file patch validation failed"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_relative_path(path: Path, *, run_dir: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _path_slug(path: Path) -> str:
    raw = path.as_posix().replace("/", "_").replace("\\", "_")
    return "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in raw)[:120] or "target"


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
    _record_approval_decision(
        context,
        stage=stage,
        action=action,
        approval=approval,
    )


def _record_approval_decision(
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


def _implementation_handler(
    context: RunContext,
    approval_console: ApprovalConsoleLike,
) -> StageHandler:
    service = ImplementationService(run_context=context)

    def run(state: AgentState) -> dict[str, Any]:
        started_at = utc_timestamp()
        try:
            emit_progress(
                "phase_started",
                stage="implementation",
                message="正在读取公开需求和可见源码，准备生成实现计划",
            )
            request = _planner_for_context(context).create_implementation_request(context)
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
                message="已获得实现计划，等待方案审批；补丁尚未生成",
            )
            result = _run_implementation_with_approval(
                context,
                service,
                request,
                approval_console,
            )
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
    approval_console: ApprovalConsoleLike,
) -> StageResult:
    planner = _planner_for_context(context)
    started_at = utc_timestamp()
    for review_round in range(1, _max_review_rounds(context) + 1):
        plan_preview = service.prepare_plan_review(request)
        if plan_preview.result is not None:
            return plan_preview.result
        if plan_preview.payload is None:
            raise RuntimeError("implementation plan approval payload missing")
        plan_review = _implementation_plan_review(context, request)
        if _should_prompt_for_approval(context, plan_review):
            plan_review = _prompt_approval(
                context,
                plan_preview.payload,
                approval_console,
            )
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

        if _supports_incremental_patch_generation(planner, "implementation"):
            incremental = _generate_and_apply_incremental_patches(
                context,
                stage="implementation",
                service=service,
                planner=planner,
                plan=request.plan,
                approval_template=request.approval,
                approval_console=approval_console,
            )
            if isinstance(incremental, StageResult):
                return incremental
            request = replace(
                request,
                patch_draft=_combine_implementation_patch_drafts(
                    request.plan,
                    incremental.drafts,
                ),
                alternate_patch_drafts=[],
            )
            approved_hash = _write_final_incremental_patch_artifacts(
                context,
                stage="implementation",
                service=service,
                plan=request.plan,
                draft=request.patch_draft,
                started_at=started_at,
            )
            if isinstance(approved_hash, StageResult):
                return approved_hash
            return service.apply_prepared_patch(
                request,
                approval=_incremental_final_patch_approval(stage="implementation"),
                approved_patch_sha256=approved_hash,
            )

        request = _generate_implementation_patch_request(
            context,
            request,
            planner=planner,
            review_round=review_round,
        )
        for patch_round in range(1, _max_review_rounds(context) + 1):
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
                _prompt_approval(context, preview.payload, approval_console)
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
                request = _generate_implementation_patch_request(
                    context,
                    request,
                    planner=planner,
                    feedback=_feedback_from_decision(approval),
                    review_round=patch_round,
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


def _testing_handler(
    context: RunContext,
    approval_console: ApprovalConsoleLike,
) -> StageHandler:
    service = TestingService(run_context=context)

    def run(state: AgentState) -> dict[str, Any]:
        started_at = utc_timestamp()
        try:
            emit_progress(
                "phase_started",
                stage="testing",
                message="正在根据公开需求、实现产物和可见源码设计自测用例",
            )
            request = _planner_for_context(context).create_testing_request(context)
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
                message="测试方案已生成，等待方案审批；测试补丁尚未生成",
            )
            result = _run_testing_with_approval(
                context,
                service,
                request,
                approval_console,
            )
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
    approval_console: ApprovalConsoleLike,
) -> StageResult:
    planner = _planner_for_context(context)
    started_at = utc_timestamp()
    for review_round in range(1, _max_review_rounds(context) + 1):
        plan_preview = service.prepare_plan_review(request)
        if plan_preview.result is not None:
            return plan_preview.result
        if plan_preview.payload is None:
            raise RuntimeError("testing plan approval payload missing")
        plan_review = (
            _prompt_approval(context, plan_preview.payload, approval_console)
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
        _record_approval_decision(
            context,
            stage="testing",
            action="review_test_plan",
            approval=plan_review,
        )
        if _supports_incremental_patch_generation(planner, "testing"):
            incremental = _generate_and_apply_incremental_patches(
                context,
                stage="testing",
                service=service,
                planner=planner,
                plan=request.plan,
                approval_template=request.patch_approval,
                approval_console=approval_console,
            )
            if isinstance(incremental, StageResult):
                return incremental
            request = replace(
                request,
                patch_draft=_combine_testing_patch_drafts(
                    request.plan,
                    incremental.drafts,
                ),
                alternate_patch_drafts=[],
                patch_approval=_incremental_final_patch_approval(stage="testing"),
            )
            approved_hash = _write_final_incremental_patch_artifacts(
                context,
                stage="testing",
                service=service,
                plan=request.plan,
                draft=request.patch_draft,
                started_at=started_at,
            )
            if isinstance(approved_hash, StageResult):
                return approved_hash
            patch_approval = _incremental_final_patch_approval(stage="testing")
            request = replace(request, patch_approval=patch_approval)
            command_preview = service.apply_patch_and_prepare_command(
                request,
                patch_approval=patch_approval,
                approved_patch_sha256=approved_hash,
            )
            if command_preview.result is not None:
                return command_preview.result
            if command_preview.payload is None:
                raise RuntimeError("testing command approval payload missing")
            command_approval = (
                _prompt_approval(context, command_preview.payload, approval_console)
                if _should_prompt_for_approval(context, request.command_approval)
                else _effective_approval(context, request.command_approval)
            )
            request = replace(request, command_approval=command_approval)
            return service.run_prepared_command(request, command_approval=command_approval)

        request = _generate_testing_patch_request(
            context,
            request,
            planner=planner,
            review_round=review_round,
        )

        for patch_round in range(1, _max_review_rounds(context) + 1):
            patch_preview = service.prepare_patch_approval(
                request,
                plan_review=plan_review,
                record_plan_review=False,
            )
            if patch_preview.result is not None:
                return patch_preview.result
            if patch_preview.payload is None:
                raise RuntimeError("testing patch approval payload missing")
            patch_approval = (
                _prompt_approval(context, patch_preview.payload, approval_console)
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
                request = _generate_testing_patch_request(
                    context,
                    request,
                    planner=planner,
                    feedback=_feedback_from_decision(patch_approval),
                    review_round=patch_round,
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
                _prompt_approval(context, command_preview.payload, approval_console)
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


def _debugging_handler(
    context: RunContext,
    approval_console: ApprovalConsoleLike,
) -> StageHandler:
    service = DebuggingService(
        run_context=context,
        analysis_provider=_planner_for_context(context),
    )

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
        result = _run_debugging_with_approval(
            context,
            service,
            request,
            approval_console,
        )
        return _state_update_from_result(state, result)

    return run


def _run_debugging_with_approval(
    context: RunContext,
    service: DebuggingService,
    request: DebuggingRequest,
    approval_console: ApprovalConsoleLike,
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
    command_approval = _prompt_approval(context, preview.payload, approval_console)
    return service.run_after_approval(request, command_approval=command_approval)


def _repair_handler(
    context: RunContext,
    approval_console: ApprovalConsoleLike,
) -> StageHandler:
    service = RepairService(run_context=context)

    def run(state: AgentState) -> dict[str, Any]:
        started_at = utc_timestamp()
        try:
            emit_progress(
                "phase_started",
                stage="repair",
                message="正在读取调试证据和失败日志，准备生成修复计划",
            )
            request = _planner_for_context(context).create_repair_request(context)
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
                message="已获得修复计划，等待方案审批；修复补丁尚未生成",
            )
            result = _run_repair_with_approval(
                context,
                service,
                request,
                approval_console,
            )
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
    approval_console: ApprovalConsoleLike,
) -> StageResult:
    planner = _planner_for_context(context)
    started_at = utc_timestamp()
    for review_round in range(1, _max_review_rounds(context) + 1):
        plan_preview = service.prepare_plan_review(request)
        if plan_preview.result is not None:
            return plan_preview.result
        if plan_preview.payload is None:
            raise RuntimeError("repair plan approval payload missing")
        default_plan_review = request.plan_review or ApprovalDecision(
            interrupt_id=REPAIR_PLAN_INTERRUPT_ID,
            decision_type="approve",
            comment="Generated repair plan review.",
            auto=False,
            decision_source="system_default",
            presented_to_user=False,
        )
        plan_review = (
            _prompt_approval(context, plan_preview.payload, approval_console)
            if _should_prompt_for_approval(context, default_plan_review)
            else _effective_approval(context, default_plan_review)
        )
        if plan_review.decision_type == "respond":
            _record_feedback_decision(
                context,
                stage="repair",
                action="review_repair_plan",
                approval=plan_review,
            )
            request = _regenerate_repair_request(
                context,
                planner=planner,
                feedback=_feedback_from_decision(plan_review),
                review_round=review_round,
            )
            continue
        request = replace(request, plan_review=plan_review)
        _record_approval_decision(
            context,
            stage="repair",
            action="review_repair_plan",
            approval=plan_review,
        )
        if _supports_incremental_patch_generation(planner, "repair"):
            incremental = _generate_and_apply_incremental_patches(
                context,
                stage="repair",
                service=service,
                planner=planner,
                plan=request.plan,
                approval_template=request.patch_approval,
                approval_console=approval_console,
            )
            if isinstance(incremental, StageResult):
                return incremental
            request = replace(
                request,
                patch_draft=_combine_repair_patch_drafts(
                    request.plan,
                    incremental.drafts,
                ),
                alternate_patch_drafts=[],
                patch_approval=_incremental_final_patch_approval(stage="repair"),
            )
            approved_hash = _write_final_incremental_patch_artifacts(
                context,
                stage="repair",
                service=service,
                plan=request.plan,
                draft=request.patch_draft,
                started_at=started_at,
            )
            if isinstance(approved_hash, StageResult):
                return approved_hash
            patch_approval = _incremental_final_patch_approval(stage="repair")
            request = replace(request, patch_approval=patch_approval)
            command_preview = service.apply_patch_and_prepare_command(
                request,
                patch_approval=patch_approval,
                approved_patch_sha256=approved_hash,
            )
            if command_preview.result is not None:
                return command_preview.result
            if command_preview.payload is None:
                raise RuntimeError("repair command approval payload missing")
            command_approval = (
                _prompt_approval(context, command_preview.payload, approval_console)
                if _should_prompt_for_approval(context, request.command_approval)
                else _effective_approval(context, request.command_approval)
            )
            request = replace(request, command_approval=command_approval)
            return service.run_prepared_command(request, command_approval=command_approval)

        request = _generate_repair_patch_request(
            context,
            request,
            planner=planner,
            review_round=review_round,
        )

        for patch_round in range(1, _max_review_rounds(context) + 1):
            patch_preview = service.prepare_patch_approval(
                request,
                plan_review=plan_review,
                record_plan_review=False,
            )
            if patch_preview.result is not None:
                return patch_preview.result
            if patch_preview.payload is None:
                raise RuntimeError("repair patch approval payload missing")
            patch_approval = (
                _prompt_approval(context, patch_preview.payload, approval_console)
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
                request = _generate_repair_patch_request(
                    context,
                    request,
                    planner=planner,
                    feedback=_feedback_from_decision(patch_approval),
                    review_round=patch_round,
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
                _prompt_approval(context, command_preview.payload, approval_console)
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
    manual_static_reproduction = (
        has_static_evidence
        and not benchmark_auto
        and not user_auto
        and context.task_config.permissions.approval_mode == "manual"
        and _interactive_input_available()
    )
    command_auto = (
        False
        if manual_static_reproduction
        else has_static_evidence or benchmark_auto or user_auto
    )
    decision_source = (
        "user"
        if manual_static_reproduction
        else "system_static_evidence"
        if has_static_evidence
        else "benchmark_auto"
        if benchmark_auto
        else "user_configured_auto"
        if user_auto
        else "system_default"
    )
    test_command = (
        context.task_config.test_command.command
        if manual_static_reproduction or not has_static_evidence
        else None
    )
    decision_type = (
        "approve"
        if manual_static_reproduction or not has_static_evidence
        else "reject"
    )
    return DebuggingRequest(
        test_command=test_command,
        command_approval=ApprovalDecision(
            interrupt_id=REPRODUCTION_COMMAND_INTERRUPT_ID,
            decision_type=decision_type,
            auto=command_auto,
            comment=(
                "Use the visible testing failure evidence; ask before rerunning reproduction."
                if manual_static_reproduction
                else "Static failure evidence supplied by CLI."
                if has_static_evidence
                else "Non-interactive CLI reproduction command."
            ),
            decided_by=(
                "user"
                if manual_static_reproduction
                else "workflow"
                if has_static_evidence
                else "benchmark"
                if benchmark_auto
                else "config"
                if user_auto
                else "workflow"
            ),
            decision_source=decision_source,
            presented_to_user=False if command_auto else True,
        ),
        failure_logs=failure_logs,
        test_report_path=test_report_path,
        framework=context.task_config.test_framework,
        command_timeout_seconds=context.task_config.test_command.timeout_seconds,
        attempt_index=int(state.get("repair_attempt", 0)) + 1,
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
    retryable: bool = True,
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
            retryable=retryable,
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
    retryable = getattr(exc, "retryable", True)
    if retryable is False:
        next_suggestion = (
            "当前选择的 LLM 模型在此环境或账号下不可用。请在 wizard 中选择其他模型，"
            "或修改运行配置里的 model_name 后重新运行任务。"
        )
    return _failed_stage_result(
        stage=stage,
        started_at=started_at,
        summary=summary,
        category="model" if isinstance(exc, PlanGenerationError) else "model",
        message=_redact_exception(exc),
        next_suggestion=next_suggestion,
        retryable=retryable,
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
