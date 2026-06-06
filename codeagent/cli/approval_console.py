"""CLI helpers for human approval prompts."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass

from codeagent.tools.hitl import ApprovalDecision, ApprovalRequest, DecisionType


PATCH_AUTO_APPROVE_REMAINING_KEY = "auto_approve_remaining_stage"
_PATCH_AUTO_APPROVE_REMAINING_VALUE = "__patch_auto_approve_remaining_stage__"


class ApprovalInputError(ValueError):
    """Raised when a CLI approval input cannot be converted to a decision."""


_DECISION_ALIASES: dict[str, DecisionType] = {
    "1": "approve",
    "a": "approve",
    "approve": "approve",
    "approved": "approve",
    "批准": "approve",
    "同意": "approve",
    "继续": "approve",
    "2": "respond",
    "respond": "respond",
    "response": "respond",
    "反馈": "respond",
    "意见": "respond",
    "提出意见": "respond",
    "回复": "respond",
    "3": "edit",
    "e": "edit",
    "edit": "edit",
    "修改": "edit",
    "手动修改": "edit",
    "4": "reject",
    "r": "reject",
    "reject": "reject",
    "rejected": "reject",
    "拒绝": "reject",
    "5": "cancel",
    "c": "cancel",
    "cancel": "cancel",
    "cancelled": "cancel",
    "canceled": "cancel",
    "取消": "cancel",
}

_DECISION_TITLES: dict[DecisionType, str] = {
    "approve": "批准并继续",
    "respond": "提出修改意见",
    "edit": "手动编辑 JSON",
    "reject": "拒绝本项",
    "cancel": "取消整次运行",
}

_DECISION_DESCRIPTIONS: dict[DecisionType, str] = {
    "approve": "确认当前方案、补丁或命令可以进入下一步。",
    "respond": "输入中文意见，让 Agent 带着反馈重新生成当前产物。",
    "edit": "粘贴修改后的结构化 JSON，适合你已经知道精确改法时使用。",
    "reject": "停止当前阶段，并把原因记录到运行报告。",
    "cancel": "立即取消本次运行，不再继续后续阶段。",
}

_PLAN_ACTIONS = {
    "review_implementation_plan",
    "review_test_plan",
    "review_repair_plan",
}

_PATCH_ACTIONS = {
    "approve_implementation_patch",
    "approve_test_patch",
    "approve_repair_patch",
}


def parse_approval_decision(
    raw: str,
    *,
    request: ApprovalRequest,
    edited_payload_text: str | None = None,
    comment: str | None = None,
) -> ApprovalDecision:
    """Convert a user approval choice into the workflow decision schema."""
    decision_type = _normalize_decision(raw)
    if decision_type not in request.allowed_decisions:
        allowed = ", ".join(request.allowed_decisions)
        raise ApprovalInputError(
            f"决策 {decision_type!r} 不适用于 {request.action}，允许值：{allowed}"
        )

    edited_payload = None
    if decision_type == "edit":
        edited_payload = _parse_edited_payload(edited_payload_text)

    cleaned_comment = comment.strip() if comment and comment.strip() else None
    return ApprovalDecision(
        interrupt_id=request.interrupt_id,
        decision_type=decision_type,
        edited_payload=edited_payload,
        comment=cleaned_comment,
        decided_by="user",
        auto=False,
        decision_source="user",
        presented_to_user=True,
    )


@dataclass
class ApprovalConsole:
    """Prompt users for HITL decisions while keeping parsing testable."""

    input_func: Callable[[str], str] = input

    def prompt(self, request: ApprovalRequest) -> ApprovalDecision:
        if self._can_use_questionary_form():
            return self._prompt_questionary(request)
        return self._prompt_line(request)

    def _can_use_questionary_form(self) -> bool:
        return (
            self.input_func is input
            and sys.stdin.isatty()
            and sys.stdout.isatty()
        )

    def _prompt_questionary(self, request: ApprovalRequest) -> ApprovalDecision:
        import questionary

        choices = [
            questionary.Choice(
                title=label,
                value=value,
            )
            for value, label in _approval_choice_options(request)
        ]
        answer = questionary.select(
            request.title,
            choices=choices,
            default=(
                request.default_decision
                if request.default_decision in request.allowed_decisions
                else choices[0].value
            ),
            instruction="（上下键移动，回车选中）",
        ).ask()
        if answer is None:
            answer = (
                request.default_decision
                if request.default_decision in request.allowed_decisions
                else choices[0].value
            )
        decision_type = str(answer)
        comment = self._prompt_comment_if_needed(questionary, decision_type, request)
        edited_payload_text = None
        if decision_type == "edit":
            edited_payload_text = questionary.text(
                "请粘贴修改后的 JSON 对象",
            ).ask()
            if edited_payload_text is None:
                decision_type = "cancel"
        return _approval_decision_from_choice(
            decision_type,
            request=request,
            edited_payload_text=edited_payload_text,
            comment=comment,
        )

    def _prompt_comment_if_needed(
        self,
        questionary,
        decision_type: str,
        request: ApprovalRequest,
    ) -> str | None:
        if decision_type == "respond":
            answer = questionary.text(
                "请告诉 CodeAgent 如何调整",
                instruction="（输入中文意见，回车提交）",
            ).ask()
            if answer is None or not str(answer).strip():
                raise ApprovalInputError("提出修改意见时必须填写具体意见")
            return str(answer)
        if decision_type in {"reject", "cancel"}:
            answer = questionary.text("可选：请输入原因").ask()
            return str(answer) if answer is not None else None
        return None

    def _prompt_line(self, request: ApprovalRequest) -> ApprovalDecision:
        options = _approval_choice_options(request)
        print(_render_line_prompt(request, options))
        raw = self.input_func("> ")
        decision_type = _choice_value_from_line_input(raw, options)
        edited_payload_text = None
        comment = None
        if decision_type == "respond":
            comment = self.input_func("请输入希望 Agent 改进的具体意见：")
            if not comment.strip():
                raise ApprovalInputError("提出修改意见时必须填写具体意见")
        elif decision_type == "edit":
            edited_payload_text = self.input_func("请粘贴修改后的 JSON 对象：")
        elif decision_type in {"reject", "cancel"}:
            comment = self.input_func("可选：请输入原因（可直接回车跳过）：")
        return _approval_decision_from_choice(
            decision_type,
            request=request,
            edited_payload_text=edited_payload_text,
            comment=comment,
        )


def _ordered_allowed_decisions(request: ApprovalRequest) -> list[DecisionType]:
    priority: list[DecisionType] = ["approve", "respond", "edit", "reject", "cancel"]
    allowed = set(request.allowed_decisions)
    return [decision for decision in priority if decision in allowed]


def _approval_choice_options(request: ApprovalRequest) -> list[tuple[str, str]]:
    if _is_patch_feedback_request(request):
        return [
            ("approve", "是，应用此补丁"),
            (
                _PATCH_AUTO_APPROVE_REMAINING_VALUE,
                "是，应用此补丁，本阶段不再提示",
            ),
            ("respond", "否，告知 CodeAgent 如何调整"),
        ]
    return [
        (decision, _choice_label(request, decision))
        for decision in _ordered_allowed_decisions(request)
    ]


def _is_patch_feedback_request(request: ApprovalRequest) -> bool:
    allowed = set(request.allowed_decisions)
    return (
        request.action in _PATCH_ACTIONS
        and "approve" in allowed
        and "respond" in allowed
    )


def _decision_from_line_input(raw: str, allowed: list[DecisionType]) -> DecisionType:
    normalized = raw.strip()
    if normalized.isdigit():
        index = int(normalized) - 1
        if 0 <= index < len(allowed):
            return allowed[index]
    return _normalize_decision(raw)


def _choice_value_from_line_input(raw: str, options: list[tuple[str, str]]) -> str:
    normalized = raw.strip()
    if normalized.isdigit():
        index = int(normalized) - 1
        if 0 <= index < len(options):
            return options[index][0]
    if normalized.lower() in {
        "auto",
        "auto-approve",
        "approve-all",
        "approve-rest",
        "本阶段不再提示",
        "自动通过",
    }:
        return _PATCH_AUTO_APPROVE_REMAINING_VALUE
    return _normalize_decision(raw)


def _render_line_prompt(
    request: ApprovalRequest,
    options: list[tuple[str, str]],
) -> str:
    lines = [request.title]
    for index, (_value, label) in enumerate(options, start=1):
        lines.append(f"  {index}. {label}")
    return "\n".join(line for line in lines if line)


def _choice_label(request: ApprovalRequest, decision: DecisionType) -> str:
    if request.action in _PLAN_ACTIONS:
        if decision == "approve":
            return "是，实施此计划"
        if decision == "respond":
            return "否，告知 CodeAgent 如何调整"
    if request.action in _PATCH_ACTIONS:
        if decision == "approve":
            return "是，应用此补丁"
        if decision == "respond":
            return "否，告知 CodeAgent 如何调整"
    if "command" in request.action:
        if decision == "approve":
            return "是，运行命令"
        if decision == "edit":
            return "否，修改命令"
        if decision == "reject":
            return "否，不运行命令"
        if decision == "cancel":
            return "取消本次运行"
    return f"{_DECISION_TITLES[decision]}：{_DECISION_DESCRIPTIONS[decision]}"


def _normalize_decision(raw: str) -> DecisionType:
    normalized = raw.strip().lower()
    decision_type = _DECISION_ALIASES.get(normalized)
    if decision_type is None:
        raise ApprovalInputError(f"未知审批决策：{raw!r}")
    return decision_type


def _approval_decision_from_choice(
    choice: str,
    *,
    request: ApprovalRequest,
    edited_payload_text: str | None = None,
    comment: str | None = None,
) -> ApprovalDecision:
    if choice == _PATCH_AUTO_APPROVE_REMAINING_VALUE:
        if not _is_patch_feedback_request(request):
            raise ApprovalInputError("本阶段自动通过仅适用于补丁审批")
        return ApprovalDecision(
            interrupt_id=request.interrupt_id,
            decision_type="approve",
            edited_payload={PATCH_AUTO_APPROVE_REMAINING_KEY: True},
            comment="应用此补丁，并在本阶段本轮自动通过后续补丁。",
            decided_by="user",
            auto=False,
            decision_source="user_stage_patch_auto_approve",
            presented_to_user=True,
        )
    return parse_approval_decision(
        choice,
        request=request,
        edited_payload_text=edited_payload_text,
        comment=comment,
    )


def _parse_edited_payload(edited_payload_text: str | None) -> dict:
    if edited_payload_text is None or not edited_payload_text.strip():
        return {}
    try:
        payload = json.loads(edited_payload_text)
    except json.JSONDecodeError as exc:
        raise ApprovalInputError(f"修改内容必须是合法 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ApprovalInputError("修改内容必须是 JSON 对象。")
    return payload
