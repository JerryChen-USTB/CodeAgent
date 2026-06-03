from __future__ import annotations

from collections.abc import Callable
from typing import Any

from codeagent.reports.schemas import StageResult
from codeagent.workflow.factory import WorkflowFactory
from codeagent.workflow.events import stream_workflow_events
from codeagent.workflow.routing import StageRouter
from codeagent.workflow.state import AgentState, create_initial_state


def _stage_result(stage: str, status: str, summary: str | None = None) -> dict[str, Any]:
    return StageResult(
        stage=stage,
        status=status,
        started_at="2026-06-03T06:00:00Z",
        ended_at="2026-06-03T06:01:00Z",
        summary=summary or f"{stage} {status}",
    ).model_dump(mode="json", exclude_none=True)


def _state(stages: list[str]) -> AgentState:
    return create_initial_state(run_id="run-routing", mode="run", selected_stages=stages)


def _with_result(state: AgentState, stage: str, status: str) -> AgentState:
    updated = dict(state)
    stage_results = dict(updated.get("stage_results", {}))
    stage_results[stage] = _stage_result(stage, status)
    updated["stage_results"] = stage_results
    return updated


def _handler(stage: str, status: str) -> Callable[[AgentState], dict[str, Any]]:
    def run(state: AgentState) -> dict[str, Any]:
        messages = list(state.get("messages", []))
        messages.append({"type": "stage_visit", "stage": stage})
        stage_results = dict(state.get("stage_results", {}))
        stage_results[stage] = _stage_result(stage, status)
        return {"messages": messages, "stage_results": stage_results}

    return run


def test_router_entry_respects_selected_stage_subsets() -> None:
    router = StageRouter()

    assert router.route_entry(_state(["implement", "test"])) == "implementation"
    assert router.route_entry(_state(["test", "debug"])) == "testing"
    assert router.route_entry(_state(["debug"])) == "debugging"
    assert router.route_entry(_state(["repair"])) == "repair"


def test_router_stops_after_failed_or_cancelled_implementation() -> None:
    router = StageRouter()
    failed = _with_result(_state(["implement", "test"]), "implementation", "failed")
    cancelled = _with_result(_state(["implement", "test"]), "implementation", "cancelled")

    assert router.decide_after_implementation(failed).route == "final_failed"
    assert router.decide_after_implementation(cancelled).route == "final_cancelled"


def test_router_sends_failed_testing_to_debug_when_selected() -> None:
    router = StageRouter()
    with_debug = _with_result(_state(["test", "debug"]), "testing", "failed")
    without_debug = _with_result(_state(["test"]), "testing", "failed")
    passed_with_later_stages = _with_result(
        _state(["test", "debug", "repair"]),
        "testing",
        "succeeded",
    )

    assert router.decide_after_testing(with_debug).route == "debugging"
    assert router.decide_after_testing(without_debug).route == "final_failed"
    assert router.decide_after_testing(passed_with_later_stages).route == "final_success"


def test_router_repair_failure_loops_until_max_attempts() -> None:
    router = StageRouter()
    retry_state = _with_result(_state(["debug", "repair"]), "repair", "failed")
    retry_state["repair_attempt"] = 1
    retry_state["max_repair_attempts"] = 3
    exhausted_state = _with_result(_state(["debug", "repair"]), "repair", "failed")
    exhausted_state["repair_attempt"] = 3
    exhausted_state["max_repair_attempts"] = 3

    assert router.decide_after_repair(retry_state).route == "debugging"
    assert router.decide_after_repair(exhausted_state).route == "final_failed"


def test_router_does_not_treat_incomplete_stage_statuses_as_success() -> None:
    router = StageRouter()

    for status in ["skipped", "pending", "running"]:
        implementation = _with_result(
            _state(["implement", "test"]),
            "implementation",
            status,
        )
        testing = _with_result(_state(["test", "debug"]), "testing", status)
        debugging = _with_result(_state(["debug", "repair"]), "debugging", status)
        repair = _with_result(_state(["debug", "repair"]), "repair", status)

        assert router.decide_after_implementation(implementation).route == "final_failed"
        assert router.decide_after_testing(testing).route == "final_failed"
        assert router.decide_after_debugging(debugging).route == "final_failed"
        assert router.decide_after_repair(repair).route == "final_failed"


def test_workflow_graph_invokes_mocked_stages_and_logs_route_decisions() -> None:
    graph = WorkflowFactory(
        stage_handlers={
            "implementation": _handler("implementation", "succeeded"),
            "testing": _handler("testing", "succeeded"),
        }
    ).build()

    result = graph.invoke(_state(["implement", "test"]))

    assert [message["stage"] for message in result["messages"]] == [
        "implementation",
        "testing",
    ]
    assert result["final_status"] == "succeeded"
    route_events = result["decision_trace"]
    assert [event["type"] for event in route_events] == ["route_decision"] * 3
    assert route_events[-1]["to_node"] == "final_success"


def test_stream_workflow_events_normalizes_langgraph_updates() -> None:
    graph = WorkflowFactory(
        stage_handlers={
            "implementation": _handler("implementation", "succeeded"),
        }
    ).build()

    events = list(stream_workflow_events(graph.stream(_state(["implement"]))))
    event_types = [event["type"] for event in events]

    assert "node_completed" in event_types
    assert "route_decision" in event_types
    assert "stage_result" in event_types
    assert events[-1] == {"type": "final_status", "status": "succeeded"}


def test_stream_workflow_events_normalizes_multi_mode_stream_chunks() -> None:
    raw_events = [
        ("custom", {"type": "agent_status", "stage": "testing", "message": "生成测试"}),
        ("messages", ("token", {"langgraph_node": "testing"})),
        (
            "updates",
            {
                "testing": {
                    "current_node": "testing",
                    "stage_results": {
                        "testing": _stage_result("testing", "succeeded")
                    },
                }
            },
        ),
    ]

    events = list(stream_workflow_events(raw_events))

    assert events[0] == {
        "type": "agent_status",
        "stage": "testing",
        "message": "生成测试",
    }
    assert {"type": "agent_status", "message": "模型正在生成结构化输出"} in events
    assert any(
        event["type"] == "stage_result" and event["stage"] == "testing"
        for event in events
    )


def test_stream_workflow_events_preserves_retry_stage_results() -> None:
    initial = _state(["debug", "repair"])
    initial["max_repair_attempts"] = 2
    graph = WorkflowFactory(
        stage_handlers={
            "debugging": _handler("debugging", "succeeded"),
            "repair": _handler("repair", "failed"),
        }
    ).build()

    events = list(stream_workflow_events(graph.stream(initial)))
    stage_events = [event for event in events if event["type"] == "stage_result"]

    assert [event["stage"] for event in stage_events] == [
        "debugging",
        "repair",
        "debugging",
        "repair",
    ]


def test_stage_handler_unknown_state_keys_are_rejected() -> None:
    def bad_handler(state: AgentState) -> dict[str, Any]:
        return {
            "unknown_key": "would be dropped by LangGraph",
            "stage_results": {"implementation": _stage_result("implementation", "succeeded")},
        }

    graph = WorkflowFactory(stage_handlers={"implementation": bad_handler}).build()

    try:
        graph.invoke(_state(["implement"]))
    except ValueError as exc:
        assert "unknown_key" in str(exc)
    else:
        raise AssertionError("unknown handler output key should be rejected")


def test_workflow_graph_routes_failed_testing_to_debug_and_repair() -> None:
    graph = WorkflowFactory(
        stage_handlers={
            "testing": _handler("testing", "failed"),
            "debugging": _handler("debugging", "succeeded"),
            "repair": _handler("repair", "succeeded"),
        }
    ).build()

    result = graph.invoke(_state(["test", "debug", "repair"]))

    assert [message["stage"] for message in result["messages"]] == [
        "testing",
        "debugging",
        "repair",
    ]
    assert result["repair_attempt"] == 1
    assert result["final_status"] == "succeeded"


def test_workflow_graph_retries_repair_until_attempt_limit() -> None:
    initial = _state(["debug", "repair"])
    initial["max_repair_attempts"] = 2
    graph = WorkflowFactory(
        stage_handlers={
            "debugging": _handler("debugging", "succeeded"),
            "repair": _handler("repair", "failed"),
        }
    ).build()

    result = graph.invoke(initial)

    assert [message["stage"] for message in result["messages"]] == [
        "debugging",
        "repair",
        "debugging",
        "repair",
    ]
    assert result["repair_attempt"] == 2
    assert result["final_status"] == "failed"
