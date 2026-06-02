"""Implementation stage LangGraph adapter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from codeagent.stages.implementation_service import (
    IMPLEMENTATION_STAGE,
    PATCH_INTERRUPT_ID,
    ImplementationRequest,
    ImplementationService,
)
from codeagent.tools.hitl import ApprovalDecision
from codeagent.workflow.main_graph import StageHandler
from codeagent.workflow.state import AgentState


ImplementationRequestBuilder = Callable[[AgentState], ImplementationRequest]


def create_implementation_stage_handler(
    *,
    service: ImplementationService,
    request_builder: ImplementationRequestBuilder,
) -> StageHandler:
    """Create a main-graph compatible handler for the implementation service."""

    def run(state: AgentState) -> dict[str, Any]:
        request = request_builder(state)
        result = service.run(request)
        stage_results = dict(state.get("stage_results", {}))
        stage_results[IMPLEMENTATION_STAGE] = result.model_dump(
            mode="json",
            exclude_none=True,
        )
        artifact_refs = list(state.get("artifact_refs", []))
        for artifact_id in result.artifact_ids:
            if artifact_id not in artifact_refs:
                artifact_refs.append(artifact_id)
        messages = list(state.get("messages", []))
        messages.append(
            {
                "type": "stage_completed",
                "stage": IMPLEMENTATION_STAGE,
                "status": result.status,
                "summary": result.summary,
            }
        )
        return {
            "stage_results": stage_results,
            "artifact_refs": artifact_refs,
            "messages": messages,
        }

    return run


def build_implementation_subgraph(handler: StageHandler):
    """Build a minimal implementation subgraph around an injected stage handler."""

    graph = StateGraph(AgentState)
    graph.add_node(IMPLEMENTATION_STAGE, handler)
    graph.add_edge(START, IMPLEMENTATION_STAGE)
    graph.add_edge(IMPLEMENTATION_STAGE, END)
    return graph.compile()


def build_interrupting_implementation_subgraph(
    *,
    service: ImplementationService,
    request_builder: ImplementationRequestBuilder,
    checkpointer=None,
):
    """Build the implementation subgraph with an explicit HITL interrupt node."""

    def prepare_patch(state: AgentState) -> dict[str, Any]:
        request = request_builder(state)
        preview = service.prepare_approval(request)
        if preview.result is not None:
            return _state_update_from_result(state, preview.result)
        if preview.payload is None:
            raise ValueError("implementation approval preview produced no payload")
        messages = list(state.get("messages", []))
        messages.append(
            {
                "type": "approval_requested",
                "stage": IMPLEMENTATION_STAGE,
                "interrupt_id": PATCH_INTERRUPT_ID,
            }
        )
        return {
            "pending_interrupt": preview.payload,
            "messages": messages,
        }

    def approve_patch(state: AgentState) -> dict[str, Any]:
        payload = state.get("pending_interrupt")
        if payload is None:
            raise ValueError("implementation approval payload missing")
        decision = interrupt(payload)
        resumed = dict(payload)
        resumed["decision"] = decision
        return {"pending_interrupt": resumed}

    def apply_patch(state: AgentState) -> dict[str, Any]:
        pending = state.get("pending_interrupt") or {}
        decision_payload = pending.get("decision")
        approval = _approval_from_resume(decision_payload)
        request = replace(request_builder(state), approval=approval)
        if approval.decision_type == "edit":
            result = service.run(request)
        else:
            result = service.apply_prepared_patch(
                request,
                approval=approval,
                approved_patch_sha256=_approved_patch_sha256(pending),
            )
        update = _state_update_from_result(state, result)
        update["pending_interrupt"] = None
        return update

    graph = StateGraph(AgentState)
    graph.add_node("prepare_patch", prepare_patch)
    graph.add_node("approve_patch", approve_patch)
    graph.add_node("apply_patch", apply_patch)
    graph.add_edge(START, "prepare_patch")
    graph.add_conditional_edges(
        "prepare_patch",
        _route_after_prepare,
        {"approve_patch": "approve_patch", "end": END},
    )
    graph.add_edge("approve_patch", "apply_patch")
    graph.add_edge("apply_patch", END)
    return graph.compile(checkpointer=checkpointer)


def _route_after_prepare(state: AgentState) -> str:
    if IMPLEMENTATION_STAGE in state.get("stage_results", {}):
        return "end"
    return "approve_patch"


def _state_update_from_result(state: AgentState, result) -> dict[str, Any]:
    stage_results = dict(state.get("stage_results", {}))
    stage_results[IMPLEMENTATION_STAGE] = result.model_dump(
        mode="json",
        exclude_none=True,
    )
    artifact_refs = list(state.get("artifact_refs", []))
    for artifact_id in result.artifact_ids:
        if artifact_id not in artifact_refs:
            artifact_refs.append(artifact_id)
    messages = list(state.get("messages", []))
    messages.append(
        {
            "type": "stage_completed",
            "stage": IMPLEMENTATION_STAGE,
            "status": result.status,
            "summary": result.summary,
        }
    )
    return {
        "stage_results": stage_results,
        "artifact_refs": artifact_refs,
        "messages": messages,
    }


def _approval_from_resume(value: object) -> ApprovalDecision:
    if not isinstance(value, dict):
        raise ValueError("implementation approval resume value must be an object")
    decision_type = value.get("decision_type")
    if decision_type not in {"approve", "edit", "reject", "respond", "cancel"}:
        raise ValueError("implementation approval decision_type is invalid")
    return ApprovalDecision(
        interrupt_id=str(value.get("interrupt_id") or PATCH_INTERRUPT_ID),
        decision_type=decision_type,  # type: ignore[arg-type]
        edited_payload=value.get("edited_payload") if isinstance(value.get("edited_payload"), dict) else None,
        comment=str(value.get("comment") or ""),
        decided_at=str(value.get("decided_at") or datetime.now(timezone.utc).isoformat()),
        decided_by=str(value.get("decided_by") or "user"),
        auto=bool(value.get("auto", False)),
    )


def _approved_patch_sha256(pending: dict[str, Any]) -> str | None:
    payload = pending.get("payload")
    if not isinstance(payload, dict):
        return None
    value = payload.get("patch_sha256")
    return str(value) if value else None
