"""Tool-level human-in-the-loop interception."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from codeagent.reports.transcript import JsonlRecorder
from codeagent.tools.permissions import (
    PermissionDecision,
    ToolCallContext,
    ToolPermissionPolicy,
)


DecisionType = Literal["approve", "edit", "reject", "respond", "cancel"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    operation_id: str


@dataclass(frozen=True)
class ApprovalRequest:
    interrupt_id: str
    action: str
    title: str
    payload: dict[str, Any]
    risk_level: RiskLevel
    allowed_decisions: tuple[DecisionType, ...]
    default_decision: Literal["reject", "approve"] = "reject"


@dataclass(frozen=True)
class ApprovalDecision:
    interrupt_id: str
    decision_type: DecisionType
    edited_payload: dict[str, Any] | None = None
    comment: str | None = None
    decided_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    decided_by: str = "user"
    auto: bool = False


@dataclass(frozen=True)
class ToolInterceptionResult:
    tool_name: str
    args: dict[str, Any]
    permission: PermissionDecision
    execute: bool
    request: ApprovalRequest | None = None
    message: str = ""


class ToolHITLInterceptor:
    def __init__(self, *, policy: ToolPermissionPolicy) -> None:
        self.policy = policy

    def intercept(
        self,
        tool_call: ToolCall,
        context: ToolCallContext,
        *,
        decision: ApprovalDecision | None = None,
        decision_recorder: JsonlRecorder | None = None,
    ) -> ToolInterceptionResult:
        permission = self.policy.classify(tool_call.name, tool_call.args, context)
        if permission.action == "deny":
            return ToolInterceptionResult(
                tool_name=tool_call.name,
                args=tool_call.args,
                permission=permission,
                execute=False,
                message=permission.reason,
            )
        if permission.action == "allow":
            if permission.auto_approved:
                self._record_decision(
                    tool_call,
                    decision_recorder,
                    decision_type="approve",
                    auto=True,
                    reason=permission.reason,
                )
            return ToolInterceptionResult(
                tool_name=tool_call.name,
                args=tool_call.args,
                permission=permission,
                execute=True,
            )

        if decision is None:
            return ToolInterceptionResult(
                tool_name=tool_call.name,
                args=tool_call.args,
                permission=permission,
                execute=False,
                request=_approval_request(tool_call),
                message=permission.reason,
            )
        if decision.interrupt_id != tool_call.operation_id:
            raise ValueError(
                "approval decision interrupt_id does not match tool operation_id"
            )
        self._record_decision(
            tool_call,
            decision_recorder,
            decision_type=decision.decision_type,
            edited_payload=decision.edited_payload,
            comment=decision.comment,
            auto=decision.auto,
            decided_by=decision.decided_by,
            decided_at=decision.decided_at,
        )
        return _result_from_decision(tool_call, permission, decision)

    def _record_decision(
        self,
        tool_call: ToolCall,
        recorder: JsonlRecorder | None,
        *,
        decision_type: DecisionType,
        edited_payload: dict[str, Any] | None = None,
        comment: str | None = None,
        auto: bool = False,
        reason: str | None = None,
        decided_by: str = "user",
        decided_at: str | None = None,
    ) -> None:
        if recorder is None:
            return
        recorder.append(
            {
                "type": "human_decision",
                "interrupt_id": tool_call.operation_id,
                "action": "review_tool_call",
                "tool_name": tool_call.name,
                "decision_type": decision_type,
                "payload_summary": tool_call.name,
                "edited_payload": edited_payload,
                "comment": comment,
                "auto": auto,
                "reason": reason or comment or "",
                "decided_by": decided_by,
                "decided_at": decided_at
                or datetime.now(timezone.utc).isoformat(),
            }
        )


def _approval_request(tool_call: ToolCall) -> ApprovalRequest:
    return ApprovalRequest(
        interrupt_id=tool_call.operation_id,
        action="review_tool_call",
        title=f"Review tool call: {tool_call.name}",
        payload={"tool_name": tool_call.name, "args": tool_call.args},
        risk_level="high",
        allowed_decisions=("approve", "edit", "reject", "respond", "cancel"),
        default_decision="reject",
    )


def _result_from_decision(
    tool_call: ToolCall,
    permission: PermissionDecision,
    decision: ApprovalDecision,
) -> ToolInterceptionResult:
    if decision.decision_type == "approve":
        return ToolInterceptionResult(
            tool_name=tool_call.name,
            args=tool_call.args,
            permission=permission,
            execute=True,
        )
    if decision.decision_type == "edit":
        edited_args = (
            decision.edited_payload
            if decision.edited_payload is not None
            else tool_call.args
        )
        return ToolInterceptionResult(
            tool_name=tool_call.name,
            args=edited_args,
            permission=permission,
            execute=True,
        )
    if decision.decision_type == "respond":
        return ToolInterceptionResult(
            tool_name=tool_call.name,
            args=tool_call.args,
            permission=permission,
            execute=False,
            message=decision.comment or "human response returned to agent",
        )
    message = (
        "tool call cancelled"
        if decision.decision_type == "cancel"
        else "tool call rejected"
    )
    return ToolInterceptionResult(
        tool_name=tool_call.name,
        args=tool_call.args,
        permission=permission,
        execute=False,
        message=message,
    )
