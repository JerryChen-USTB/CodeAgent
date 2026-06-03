"""Debugging stage LangGraph adapter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from codeagent.stages.debugging_service import (
    DEBUGGING_STAGE,
    REPRODUCTION_COMMAND_INTERRUPT_ID,
    DebuggingRequest,
    DebuggingService,
)
from codeagent.tools.hitl import ApprovalDecision
from codeagent.workflow.main_graph import StageHandler
from codeagent.workflow.state import AgentState


DebuggingRequestBuilder = Callable[[AgentState], DebuggingRequest]


def create_debugging_stage_handler(
    *,
    service: DebuggingService,
    request_builder: DebuggingRequestBuilder,
) -> StageHandler:
    """Create a main-graph compatible handler for the debugging service."""

    def run(state: AgentState) -> dict[str, Any]:
        request = request_builder(state)
        result = service.run(request)
        stage_results = dict(state.get("stage_results", {}))
        stage_results[DEBUGGING_STAGE] = result.model_dump(
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
                "stage": DEBUGGING_STAGE,
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


def build_debugging_subgraph(handler: StageHandler):
    """Build a focused debugging subgraph around an injected stage handler."""

    graph = StateGraph(AgentState)
    graph.add_node(DEBUGGING_STAGE, handler)
    graph.add_edge(START, DEBUGGING_STAGE)
    graph.add_edge(DEBUGGING_STAGE, END)
    return graph.compile()


def build_interrupting_debugging_subgraph(
    *,
    service: DebuggingService,
    request_builder: DebuggingRequestBuilder,
    checkpointer=None,
):
    """Build the debugging subgraph with explicit reproduction command HITL."""

    def prepare_command(state: AgentState) -> dict[str, Any]:
        request = request_builder(state)
        preview = service.prepare_reproduction_approval(request)
        if preview.result is not None:
            update = _state_update_from_result(state, preview.result)
            update["pending_interrupt"] = None
            return update
        if preview.payload is None:
            raise ValueError("debugging command preview produced no payload")
        return _pending_update(state, preview.payload)

    def approve_command(state: AgentState) -> dict[str, Any]:
        payload = state.get("pending_interrupt")
        if payload is None:
            raise ValueError("debugging command approval payload missing")
        decision = interrupt(payload)
        resumed = dict(payload)
        resumed["decision"] = decision
        return {"pending_interrupt": resumed}

    def run_debugging(state: AgentState) -> dict[str, Any]:
        command_decision = _approval_from_pending(state.get("pending_interrupt"))
        request = replace(request_builder(state), command_approval=command_decision)
        result = service.run_after_approval(request, command_approval=command_decision)
        update = _state_update_from_result(state, result)
        update["pending_interrupt"] = None
        return update

    graph = StateGraph(AgentState)
    graph.add_node("prepare_command", prepare_command)
    graph.add_node("approve_command", approve_command)
    graph.add_node("run_debugging", run_debugging)
    graph.add_edge(START, "prepare_command")
    graph.add_conditional_edges(
        "prepare_command",
        _route_after_prepare,
        {"approve_command": "approve_command", "end": END},
    )
    graph.add_edge("approve_command", "run_debugging")
    graph.add_edge("run_debugging", END)
    return graph.compile(checkpointer=checkpointer)


def _pending_update(state: AgentState, payload: dict[str, object]) -> dict[str, Any]:
    messages = list(state.get("messages", []))
    messages.append(
        {
            "type": "debug_reproduction_command_ready",
            "stage": DEBUGGING_STAGE,
            "interrupt_id": payload["interrupt_id"],
        }
    )
    return {"pending_interrupt": payload, "messages": messages}


def _route_after_prepare(state: AgentState) -> str:
    if DEBUGGING_STAGE in state.get("stage_results", {}):
        return "end"
    return "approve_command"


def _state_update_from_result(state: AgentState, result) -> dict[str, Any]:
    stage_results = dict(state.get("stage_results", {}))
    stage_results[DEBUGGING_STAGE] = result.model_dump(mode="json", exclude_none=True)
    artifact_refs = list(state.get("artifact_refs", []))
    for artifact_id in result.artifact_ids:
        if artifact_id not in artifact_refs:
            artifact_refs.append(artifact_id)
    messages = list(state.get("messages", []))
    messages.append(
        {
            "type": "stage_completed",
            "stage": DEBUGGING_STAGE,
            "status": result.status,
            "summary": result.summary,
        }
    )
    return {
        "stage_results": stage_results,
        "artifact_refs": artifact_refs,
        "messages": messages,
    }


def _approval_from_pending(pending: object) -> ApprovalDecision:
    if not isinstance(pending, dict):
        raise ValueError("debugging pending interrupt is missing")
    decision = pending.get("decision")
    if not isinstance(decision, dict):
        raise ValueError("debugging approval resume value must be an object")
    decision_type = decision.get("decision_type")
    if decision_type not in {"approve", "edit", "reject", "respond", "cancel"}:
        raise ValueError("debugging approval decision_type is invalid")
    return ApprovalDecision(
        interrupt_id=str(
            decision.get("interrupt_id") or REPRODUCTION_COMMAND_INTERRUPT_ID
        ),
        decision_type=decision_type,  # type: ignore[arg-type]
        edited_payload=decision.get("edited_payload")
        if isinstance(decision.get("edited_payload"), dict)
        else None,
        comment=str(decision.get("comment") or ""),
        decided_at=str(decision.get("decided_at") or datetime.now(timezone.utc).isoformat()),
        decided_by=str(decision.get("decided_by") or "user"),
        auto=bool(decision.get("auto", False)),
    )
