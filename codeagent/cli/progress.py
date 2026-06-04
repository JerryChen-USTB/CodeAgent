"""Small helpers for consistent CLI status output."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.panel import Panel


@dataclass(frozen=True)
class ProgressEventFormatter:
    """Format normalized workflow events as concise CLI progress lines."""

    def format_event(self, event: dict[str, Any]) -> str:
        event_type = str(event.get("type") or "event")
        if event_type == "phase_started":
            stage = _stage_label(event.get("stage"))
            message = str(event.get("message") or "阶段开始")
            return f"[{stage}] {message}"
        if event_type == "agent_status":
            stage = event.get("stage")
            prefix = f"[{_stage_label(stage)}] " if stage else "[Agent] "
            message = str(event.get("message") or "正在工作")
            return f"{prefix}{message}"
        if event_type == "tool_started":
            tool_name = event.get("tool_name") or event.get("name") or "<unknown>"
            message = str(event.get("message") or "正在执行工具")
            return f"[工具] {tool_name}：{message}"
        if event_type == "tool_finished":
            tool_name = event.get("tool_name") or event.get("name") or "<unknown>"
            status = _status_label(event.get("status") or "succeeded")
            message = str(event.get("message") or "")
            suffix = f"：{message}" if message else ""
            return f"[工具] {tool_name} {status}{suffix}"
        if event_type == "artifact_written":
            stage = _stage_label(event.get("stage"))
            artifact = event.get("artifact")
            message = str(event.get("message") or "产物已写入")
            suffix = f"（{artifact}）" if artifact else ""
            return f"[{stage}] {message}{suffix}"
        if event_type == "test_result":
            passed = event.get("passed", 0)
            failed = event.get("failed", 0)
            errors = event.get("errors", 0)
            skipped = event.get("skipped", 0)
            total = event.get("total", 0)
            return (
                "[测试结果] "
                f"{passed} passed, {failed} failed, {errors} errors, "
                f"{skipped} skipped（total={total}）"
            )
        if event_type == "approval_required":
            action = _action_label(event.get("action", "approval"))
            return f"[需要确认] {action}"
        if event_type == "key_files_summary":
            files = event.get("files")
            if not isinstance(files, list) or not files:
                return "[关键文件] <无>"
            rendered = "\n".join(f"- {item}" for item in files)
            return f"[关键文件]\n{rendered}"
        if event_type == "run_directory":
            return f"运行目录：{event.get('path', '<unknown>')}"
        if event_type == "human_decision":
            decision = event.get("decision_type", "<unknown>")
            action = event.get("action", "approval")
            return f"[审批] {action} {_status_label(decision)}"
        if event_type == "approval_required":
            action = _action_label(event.get("action", "approval"))
            return f"[需要确认] {action}"
        if event_type == "node_completed":
            return f"[节点] {_node_label(event.get('node'))} 已完成"
        if event_type == "route_decision":
            source = event.get("from_node") or event.get("from_stage") or "<unknown>"
            target = event.get("to_node") or event.get("to_stage") or "<unknown>"
            reason = _translate_reason(str(event.get("reason") or ""))
            suffix = f": {reason}" if reason else ""
            return f"[路由] {_node_label(source)} -> {_node_label(target)}{suffix}"
        if event_type == "stage_result":
            stage = _stage_label(event.get("stage", "<unknown>"))
            status = _status_label(event.get("status", "<unknown>"))
            summary = str(event.get("summary") or "").strip()
            suffix = f": {summary}" if summary else ""
            line = f"[结果] {stage} {status}{suffix}"
            error_message = str(event.get("error_message") or "").strip()
            if error_message:
                line += f"\n[错误原因] {_compact_error_message(error_message)}"
            retryable = event.get("retryable")
            next_suggestion = str(event.get("next_suggestion") or "").strip()
            if retryable is False:
                line += "\n[处理建议] 当前错误通常无法通过重试解决，请更换模型或修正配置后重新运行。"
            elif next_suggestion:
                line += f"\n[处理建议] {next_suggestion}"
            return line
        if event_type == "tool_call":
            tool_name = event.get("tool_name") or event.get("name") or "<unknown>"
            status = _status_label(event.get("status") or event.get("result") or "started")
            return f"[工具] {tool_name} {status}"
        if event_type == "final_status":
            return f"[最终结果] {_status_label(event.get('status', '<unknown>'))}"
        if event_type == "run_directory":
            return f"运行目录：{event.get('path', '<unknown>')}"
        if event_type == "human_decision":
            decision = event.get("decision_type", "<unknown>")
            action = event.get("action", "approval")
            return f"[审批] {action} {_status_label(decision)}"
        return f"[事件] {event_type}"


class ProgressReporter:
    """Render concise status panels for command skeletons."""

    def __init__(
        self,
        console: Console | None = None,
        formatter: ProgressEventFormatter | None = None,
    ) -> None:
        self._console = console or Console()
        self._formatter = formatter or ProgressEventFormatter()

    def planned(self, command: str, detail: str) -> None:
        self._console.print(
            Panel(
                detail,
                title=f"{command} not implemented yet",
                border_style="yellow",
            )
        )

    def render_event(self, event: dict[str, Any]) -> str:
        line = self._formatter.format_event(event)
        self._console.print(line, markup=False)
        try:
            self._console.file.flush()
        except Exception:
            pass
        return line

    def render_events(self, events: Iterable[dict[str, Any]]) -> list[str]:
        return [self.render_event(event) for event in events]


_STAGE_LABELS = {
    "implementation": "实现阶段",
    "implement": "实现阶段",
    "testing": "测试阶段",
    "test": "测试阶段",
    "debugging": "调试阶段",
    "debug": "调试阶段",
    "repair": "修复阶段",
    "wizard": "向导",
}

_NODE_LABELS = {
    **_STAGE_LABELS,
    "route_entry": "入口路由",
    "entry": "入口",
    "route_after_implementation": "实现后路由",
    "route_after_testing": "测试后路由",
    "route_after_debugging": "调试后路由",
    "route_after_repair": "修复后路由",
    "final_success": "成功结束",
    "final_failed": "失败结束",
    "final_cancelled": "取消结束",
}

_STATUS_LABELS = {
    "started": "开始",
    "running": "运行中",
    "succeeded": "成功",
    "success": "成功",
    "failed": "失败",
    "blocked": "阻塞",
    "skipped": "跳过",
    "cancelled": "已取消",
    "canceled": "已取消",
    "approve": "已批准",
    "edit": "已修改",
    "reject": "已拒绝",
    "respond": "已回复",
    "cancel": "已取消",
}

_ACTION_LABELS = {
    "review_implementation_plan": "审查实现计划",
    "approve_implementation_patch": "审批实现补丁",
    "review_test_plan": "审查测试方案",
    "approve_test_patch": "审批测试补丁",
    "approve_test_command": "审批测试命令",
    "approve_reproduction_command": "审批复现命令",
    "approve_repair_patch": "审批修复补丁",
    "approve_regression_command": "审批回归验证命令",
}


def _stage_label(value: Any) -> str:
    text = str(value or "<unknown>")
    return _STAGE_LABELS.get(text, text)


def _node_label(value: Any) -> str:
    text = str(value or "<unknown>")
    return _NODE_LABELS.get(text, text)


def _status_label(value: Any) -> str:
    text = str(value or "<unknown>")
    return _STATUS_LABELS.get(text, text)


def _compact_error_message(message: str) -> str:
    text = " ".join(message.split())
    replacements = {
        "PlanGenerationError: Failed to generate valid ImplementationPlan: ": "",
        "PlanGenerationError: Failed to generate valid TestingPlan: ": "",
        "PlanGenerationError: Failed to generate valid RepairPlan: ": "",
        "Error code: 403 - {'error': {'message': 'This model is not available in your region.', 'code': 403}}": (
            "模型在当前区域不可用（OpenRouter 403）。"
        ),
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text[:800]


def _action_label(value: Any) -> str:
    text = str(value or "<unknown>")
    clean_labels = {
        "review_implementation_plan": "审查实现方案",
        "approve_implementation_patch": "审批实现补丁",
        "review_test_plan": "审查测试方案",
        "approve_test_patch": "审批测试补丁",
        "approve_test_command": "审批测试命令",
        "approve_reproduction_command": "审批复现命令",
        "review_repair_plan": "审查修复方案",
        "approve_repair_patch": "审批修复补丁",
        "approve_regression_command": "审批回归验证命令",
    }
    if text in clean_labels:
        return clean_labels[text]
    return _ACTION_LABELS.get(text, text)


def _translate_reason(reason: str) -> str:
    replacements = {
        "start selected stage": "从所选阶段开始",
        "implementation succeeded; run testing": "实现成功，进入测试阶段",
        "implementation succeeded; skip testing": "实现成功，跳过测试阶段",
        "implementation failed": "实现失败",
        "testing succeeded; skip later debug/repair": "测试成功，跳过调试和修复",
        "testing failed and debug is selected": "测试失败，进入调试阶段",
        "testing failed and debug not selected": "测试失败且未选择调试阶段",
        "debugging succeeded; run repair": "调试成功，进入修复阶段",
        "repair succeeded": "修复成功",
    }
    translated = reason
    for source, target in replacements.items():
        translated = translated.replace(source, target)
    return translated
