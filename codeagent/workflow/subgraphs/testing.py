"""Testing stage LangGraph adapter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from codeagent.stages.testing_service import (
    TEST_COMMAND_INTERRUPT_ID,
    TEST_PATCH_INTERRUPT_ID,
    TEST_PLAN_INTERRUPT_ID,
    TESTING_STAGE,
    TestingRequest,
    TestingService,
)
from codeagent.tools.hitl import ApprovalDecision
from codeagent.workflow.main_graph import StageHandler
from codeagent.workflow.state import AgentState


TestingRequestBuilder = Callable[[AgentState], TestingRequest]


def create_testing_stage_handler(
    *,
    service: TestingService,
    request_builder: TestingRequestBuilder,
) -> StageHandler:
    """Create a main-graph compatible handler for the testing service."""

    def run(state: AgentState) -> dict[str, Any]:
        request = request_builder(state)
        result = service.run(request)
        stage_results = dict(state.get("stage_results", {}))
        stage_results[TESTING_STAGE] = result.model_dump(
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
                "stage": TESTING_STAGE,
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


def build_testing_subgraph(handler: StageHandler):
    """Build a focused testing subgraph around an injected stage handler."""

    graph = StateGraph(AgentState)
    graph.add_node(TESTING_STAGE, handler)
    graph.add_edge(START, TESTING_STAGE)
    graph.add_edge(TESTING_STAGE, END)
    return graph.compile()


def build_interrupting_testing_subgraph(
    *,
    service: TestingService,
    request_builder: TestingRequestBuilder,
    checkpointer=None,
):
    """Build the testing subgraph with explicit plan, patch, and command HITL."""

    def prepare_plan(state: AgentState) -> dict[str, Any]:
        preview = service.prepare_plan_review(request_builder(state))
        if preview.payload is None:
            raise ValueError("testing plan preview produced no payload")
        return _pending_update(state, preview.payload, event_type="test_plan_ready")

    def review_plan(state: AgentState) -> dict[str, Any]:
        payload = state.get("pending_interrupt")
        if payload is None:
            raise ValueError("testing plan approval payload missing")
        decision = interrupt(payload)
        resumed = dict(payload)
        resumed["decision"] = decision
        return {"pending_interrupt": resumed}

    def prepare_patch(state: AgentState) -> dict[str, Any]:
        plan_decision = _approval_from_pending(
            state.get("pending_interrupt"),
            expected_interrupt_id=TEST_PLAN_INTERRUPT_ID,
        )
        request = replace(request_builder(state), plan_review=plan_decision)
        preview = service.prepare_patch_approval(request, plan_review=plan_decision)
        if preview.result is not None:
            update = _state_update_from_result(state, preview.result)
            update["pending_interrupt"] = None
            return update
        if preview.payload is None:
            raise ValueError("testing patch preview produced no payload")
        return _pending_update(state, preview.payload, event_type="test_patch_ready")

    def approve_patch(state: AgentState) -> dict[str, Any]:
        payload = state.get("pending_interrupt")
        if payload is None:
            raise ValueError("testing patch approval payload missing")
        decision = interrupt(payload)
        resumed = dict(payload)
        resumed["decision"] = decision
        return {"pending_interrupt": resumed}

    def apply_patch(state: AgentState) -> dict[str, Any]:
        patch_decision = _approval_from_pending(
            state.get("pending_interrupt"),
            expected_interrupt_id=TEST_PATCH_INTERRUPT_ID,
        )
        request = replace(request_builder(state), patch_approval=patch_decision)
        preview = service.apply_patch_and_prepare_command(
            request,
            patch_approval=patch_decision,
            approved_patch_sha256=_approved_patch_sha256(state.get("pending_interrupt")),
        )
        if preview.result is not None:
            update = _state_update_from_result(state, preview.result)
            update["pending_interrupt"] = None
            return update
        if preview.payload is None:
            raise ValueError("testing command preview produced no payload")
        return _pending_update(state, preview.payload, event_type="test_command_ready")

    def approve_command(state: AgentState) -> dict[str, Any]:
        payload = state.get("pending_interrupt")
        if payload is None:
            raise ValueError("testing command approval payload missing")
        decision = interrupt(payload)
        resumed = dict(payload)
        resumed["decision"] = decision
        return {"pending_interrupt": resumed}

    def run_command(state: AgentState) -> dict[str, Any]:
        command_decision = _approval_from_pending(
            state.get("pending_interrupt"),
            expected_interrupt_id=TEST_COMMAND_INTERRUPT_ID,
        )
        request = replace(request_builder(state), command_approval=command_decision)
        result = service.run_prepared_command(request, command_approval=command_decision)
        update = _state_update_from_result(state, result)
        update["pending_interrupt"] = None
        return update

    graph = StateGraph(AgentState)
    graph.add_node("prepare_plan", prepare_plan)
    graph.add_node("review_plan", review_plan)
    graph.add_node("prepare_patch", prepare_patch)
    graph.add_node("approve_patch", approve_patch)
    graph.add_node("apply_patch", apply_patch)
    graph.add_node("approve_command", approve_command)
    graph.add_node("run_command", run_command)
    graph.add_edge(START, "prepare_plan")
    graph.add_edge("prepare_plan", "review_plan")
    graph.add_edge("review_plan", "prepare_patch")
    graph.add_conditional_edges(
        "prepare_patch",
        _route_after_result_or_continue("approve_patch"),
        {"continue": "approve_patch", "end": END},
    )
    graph.add_edge("approve_patch", "apply_patch")
    graph.add_conditional_edges(
        "apply_patch",
        _route_after_apply_patch,
        {
            "approve_patch": "approve_patch",
            "approve_command": "approve_command",
            "end": END,
        },
    )
    graph.add_edge("approve_command", "run_command")
    graph.add_edge("run_command", END)
    return graph.compile(checkpointer=checkpointer)


def _pending_update(
    state: AgentState,
    payload: dict[str, object],
    *,
    event_type: str,
) -> dict[str, Any]:
    messages = list(state.get("messages", []))
    messages.append(
        {
            "type": event_type,
            "stage": TESTING_STAGE,
            "interrupt_id": payload["interrupt_id"],
        }
    )
    return {"pending_interrupt": payload, "messages": messages}


def _route_after_result_or_continue(next_node: str):
    def route(state: AgentState) -> str:
        if TESTING_STAGE in state.get("stage_results", {}):
            return "end"
        return "continue"

    return route


def _route_after_apply_patch(state: AgentState) -> str:
    if TESTING_STAGE in state.get("stage_results", {}):
        return "end"
    pending = state.get("pending_interrupt")
    if isinstance(pending, dict) and pending.get("interrupt_id") == TEST_PATCH_INTERRUPT_ID:
        return "approve_patch"
    return "approve_command"


def _state_update_from_result(state: AgentState, result) -> dict[str, Any]:
    stage_results = dict(state.get("stage_results", {}))
    stage_results[TESTING_STAGE] = result.model_dump(mode="json", exclude_none=True)
    artifact_refs = list(state.get("artifact_refs", []))
    for artifact_id in result.artifact_ids:
        if artifact_id not in artifact_refs:
            artifact_refs.append(artifact_id)
    messages = list(state.get("messages", []))
    messages.append(
        {
            "type": "stage_completed",
            "stage": TESTING_STAGE,
            "status": result.status,
            "summary": result.summary,
        }
    )
    return {
        "stage_results": stage_results,
        "artifact_refs": artifact_refs,
        "messages": messages,
    }


def _approval_from_pending(
    pending: object,
    *,
    expected_interrupt_id: str,
) -> ApprovalDecision:
    if not isinstance(pending, dict):
        raise ValueError("testing pending interrupt is missing")
    decision = pending.get("decision")
    if not isinstance(decision, dict):
        raise ValueError("testing approval resume value must be an object")
    decision_type = decision.get("decision_type")
    if decision_type not in {"approve", "edit", "reject", "respond", "cancel"}:
        raise ValueError("testing approval decision_type is invalid")
    return ApprovalDecision(
        interrupt_id=str(decision.get("interrupt_id") or expected_interrupt_id),
        decision_type=decision_type,  # type: ignore[arg-type]
        edited_payload=decision.get("edited_payload")
        if isinstance(decision.get("edited_payload"), dict)
        else None,
        comment=str(decision.get("comment") or ""),
        decided_at=str(decision.get("decided_at") or datetime.now(timezone.utc).isoformat()),
        decided_by=str(decision.get("decided_by") or "user"),
        auto=bool(decision.get("auto", False)),
    )


def _approved_patch_sha256(pending: object) -> str | None:
    if not isinstance(pending, dict):
        return None
    payload = pending.get("payload")
    if not isinstance(payload, dict):
        return None
    value = payload.get("patch_sha256")
    return str(value) if value else None
