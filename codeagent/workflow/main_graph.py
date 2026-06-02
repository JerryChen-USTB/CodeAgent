"""LangGraph main graph skeleton."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph

from codeagent.reports.schemas import StageResult
from codeagent.workflow.routing import RouteDecision, StageRouter
from codeagent.workflow.state import AgentState


StageHandler = Callable[[AgentState], dict[str, Any] | AgentState]


def build_main_graph(
    *,
    stage_handlers: Mapping[str, StageHandler] | None = None,
    router: StageRouter | None = None,
):
    stage_handlers = stage_handlers or {}
    router = router or StageRouter()
    graph = StateGraph(AgentState)

    graph.add_node("route_entry", _route_node("entry", router.decide_entry))
    graph.add_node(
        "implementation",
        _stage_node("implementation", stage_handlers.get("implementation")),
    )
    graph.add_node("testing", _stage_node("testing", stage_handlers.get("testing")))
    graph.add_node("debugging", _stage_node("debugging", stage_handlers.get("debugging")))
    graph.add_node("repair", _stage_node("repair", stage_handlers.get("repair")))
    graph.add_node("route_after_implementation", _route_node("implementation", router.decide_after_implementation))
    graph.add_node("route_after_testing", _route_node("testing", router.decide_after_testing))
    graph.add_node("route_after_debugging", _route_node("debugging", router.decide_after_debugging))
    graph.add_node("route_after_repair", _route_node("repair", router.decide_after_repair))
    graph.add_node("final_success", _final_node("succeeded"))
    graph.add_node("final_failed", _final_node("failed"))
    graph.add_node("final_cancelled", _final_node("cancelled"))

    graph.add_edge(START, "route_entry")
    graph.add_conditional_edges(
        "route_entry",
        _next_node,
        {
            "implementation": "implementation",
            "testing": "testing",
            "debugging": "debugging",
            "repair": "repair",
            "final_failed": "final_failed",
        },
    )
    graph.add_edge("implementation", "route_after_implementation")
    graph.add_conditional_edges(
        "route_after_implementation",
        _next_node,
        _route_targets("testing"),
    )
    graph.add_edge("testing", "route_after_testing")
    graph.add_conditional_edges(
        "route_after_testing",
        _next_node,
        _route_targets("debugging"),
    )
    graph.add_edge("debugging", "route_after_debugging")
    graph.add_conditional_edges(
        "route_after_debugging",
        _next_node,
        _route_targets("repair"),
    )
    graph.add_edge("repair", "route_after_repair")
    graph.add_conditional_edges(
        "route_after_repair",
        _next_node,
        _route_targets("debugging"),
    )
    graph.add_edge("final_success", END)
    graph.add_edge("final_failed", END)
    graph.add_edge("final_cancelled", END)
    return graph.compile()


def _stage_node(stage: str, handler: StageHandler | None):
    effective_handler = handler or _default_stage_handler(stage)

    def run(state: AgentState) -> AgentState:
        base: AgentState = dict(state)
        output = effective_handler(base)
        output_dict = dict(output)
        _validate_state_update_keys(output_dict, stage=stage)
        updated: AgentState = {**base, **output_dict}
        updated["current_stage"] = stage
        updated["current_node"] = stage
        if stage == "repair":
            updated["repair_attempt"] = int(updated.get("repair_attempt", 0)) + 1
        return updated

    return run


def _route_node(from_node: str, decide: Callable[[AgentState], RouteDecision]):
    def run(state: AgentState) -> AgentState:
        decision = decide(state)
        trace = list(state.get("decision_trace", []))
        trace.append(decision.to_event(from_node=from_node))
        updated: AgentState = dict(state)
        updated["decision_trace"] = trace
        updated["current_node"] = f"route_after_{from_node}" if from_node != "entry" else "route_entry"
        updated["next_node"] = decision.route
        return updated

    return run


def _final_node(status: str):
    def run(state: AgentState) -> AgentState:
        updated: AgentState = dict(state)
        updated["current_node"] = f"final_{status}"
        updated["current_stage"] = None
        updated["final_status"] = status
        return updated

    return run


def _next_node(state: AgentState) -> str:
    return str(state["next_node"])


def _route_targets(*stage_nodes: str) -> dict[str, str]:
    targets = {
        "final_success": "final_success",
        "final_failed": "final_failed",
        "final_cancelled": "final_cancelled",
    }
    for stage in stage_nodes:
        targets[stage] = stage
    return targets


def _default_stage_handler(stage: str) -> StageHandler:
    def run(state: AgentState) -> dict[str, Any]:
        stage_results = dict(state.get("stage_results", {}))
        stage_results[stage] = StageResult(
            stage=stage,
            status="succeeded",
            started_at=datetime.now(timezone.utc).isoformat(),
            summary=f"{stage} stage completed by skeleton handler",
        ).model_dump(mode="json", exclude_none=True)
        return {"stage_results": stage_results}

    return run


def _validate_state_update_keys(update: Mapping[str, Any], *, stage: str) -> None:
    allowed = set(AgentState.__annotations__)
    unknown = sorted(set(update) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"{stage} handler returned unknown AgentState keys: {joined}")
