"""CLI helpers for human approval prompts."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from codeagent.tools.hitl import ApprovalDecision, ApprovalRequest, DecisionType


class ApprovalInputError(ValueError):
    """Raised when a CLI approval input cannot be converted to a decision."""


_DECISION_ALIASES: dict[str, DecisionType] = {
    "a": "approve",
    "approve": "approve",
    "approved": "approve",
    "e": "edit",
    "edit": "edit",
    "r": "reject",
    "reject": "reject",
    "rejected": "reject",
    "respond": "respond",
    "response": "respond",
    "c": "cancel",
    "cancel": "cancel",
    "cancelled": "cancel",
    "canceled": "cancel",
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
            f"Decision {decision_type!r} is not allowed for {request.action}; "
            f"allowed: {allowed}"
        )

    edited_payload = None
    if decision_type == "edit":
        edited_payload = _parse_edited_payload(edited_payload_text)

    return ApprovalDecision(
        interrupt_id=request.interrupt_id,
        decision_type=decision_type,
        edited_payload=edited_payload,
        comment=comment.strip() if comment and comment.strip() else None,
    )


@dataclass
class ApprovalConsole:
    """Prompt users for HITL decisions while keeping parsing testable."""

    input_func: Callable[[str], str] = input

    def prompt(self, request: ApprovalRequest) -> ApprovalDecision:
        allowed = "/".join(request.allowed_decisions)
        raw = self.input_func(f"{request.title} [{allowed}]: ")
        edited_payload_text = None
        if _normalize_decision(raw) == "edit":
            edited_payload_text = self.input_func("Edited payload JSON: ")
        return parse_approval_decision(
            raw,
            request=request,
            edited_payload_text=edited_payload_text,
        )


def _normalize_decision(raw: str) -> DecisionType:
    normalized = raw.strip().lower()
    decision_type = _DECISION_ALIASES.get(normalized)
    if decision_type is None:
        raise ApprovalInputError(f"Unknown approval decision: {raw!r}")
    return decision_type


def _parse_edited_payload(edited_payload_text: str | None) -> dict:
    if edited_payload_text is None or not edited_payload_text.strip():
        return {}
    try:
        payload = json.loads(edited_payload_text)
    except json.JSONDecodeError as exc:
        raise ApprovalInputError(f"Edited payload must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ApprovalInputError("Edited payload must be a JSON object.")
    return payload
