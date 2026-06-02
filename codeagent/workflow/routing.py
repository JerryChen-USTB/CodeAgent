"""Deterministic routing decisions for the main workflow graph."""

from __future__ import annotations

from dataclasses import dataclass
from codeagent.config.validators import normalize_stage
from codeagent.reports.schemas import StageResult
from codeagent.workflow.state import AgentState


STAGE_NODE_NAMES = {
    "implement": "implementation",
    "test": "testing",
    "debug": "debugging",
    "repair": "repair",
}


@dataclass(frozen=True)
class RouteDecision:
    route: str
    reason: str

    def to_event(self, *, from_node: str) -> dict[str, str]:
        return {
            "type": "route_decision",
            "from_node": from_node,
            "to_node": self.route,
            "reason": self.reason,
        }


class StageRouter:
    def route_entry(self, state: AgentState) -> str:
        return self.decide_entry(state).route

    def route_after_implementation(self, state: AgentState) -> str:
        return self.decide_after_implementation(state).route

    def route_after_testing(self, state: AgentState) -> str:
        return self.decide_after_testing(state).route

    def route_after_debugging(self, state: AgentState) -> str:
        return self.decide_after_debugging(state).route

    def route_after_repair(self, state: AgentState) -> str:
        return self.decide_after_repair(state).route

    def decide_entry(self, state: AgentState) -> RouteDecision:
        selected = _selected_stage_values(state)
        if not selected:
            return RouteDecision("final_failed", "no selected stages")
        return RouteDecision(
            STAGE_NODE_NAMES[selected[0]],
            f"start selected stage {selected[0]}",
        )

    def decide_after_implementation(self, state: AgentState) -> RouteDecision:
        result = _stage_result(state, "implementation")
        if result is None:
            return RouteDecision("final_failed", "implementation result missing")
        if result.status == "cancelled":
            return RouteDecision("final_cancelled", "implementation cancelled")
        if result.status == "failed":
            return RouteDecision("final_failed", "implementation failed")
        if result.status != "succeeded":
            return RouteDecision(
                "final_failed",
                f"implementation status {result.status} cannot continue",
            )
        if _has_stage(state, "test"):
            return RouteDecision("testing", "implementation succeeded; run testing")
        return RouteDecision("final_success", "implementation succeeded")

    def decide_after_testing(self, state: AgentState) -> RouteDecision:
        result = _stage_result(state, "testing")
        if result is None:
            return RouteDecision("final_failed", "testing result missing")
        if result.status == "cancelled":
            return RouteDecision("final_cancelled", "testing cancelled")
        if result.status == "failed":
            if _has_stage(state, "debug"):
                return RouteDecision("debugging", "testing failed; run debugging")
            return RouteDecision("final_failed", "testing failed and debug not selected")
        if result.status != "succeeded":
            return RouteDecision(
                "final_failed",
                f"testing status {result.status} cannot continue",
            )
        return RouteDecision("final_success", "testing succeeded; skip later debug/repair")

    def decide_after_debugging(self, state: AgentState) -> RouteDecision:
        result = _stage_result(state, "debugging")
        if result is None:
            return RouteDecision("final_failed", "debugging result missing")
        if result.status == "cancelled":
            return RouteDecision("final_cancelled", "debugging cancelled")
        if result.status == "failed":
            return RouteDecision("final_failed", "debugging failed")
        if result.status != "succeeded":
            return RouteDecision(
                "final_failed",
                f"debugging status {result.status} cannot continue",
            )
        if _has_stage(state, "repair"):
            return RouteDecision("repair", "debugging succeeded; run repair")
        return RouteDecision("final_success", "debugging succeeded")

    def decide_after_repair(self, state: AgentState) -> RouteDecision:
        result = _stage_result(state, "repair")
        if result is None:
            return RouteDecision("final_failed", "repair result missing")
        if result.status == "cancelled":
            return RouteDecision("final_cancelled", "repair cancelled")
        if result.status == "succeeded":
            return RouteDecision("final_success", "repair succeeded")
        if result.status != "failed":
            return RouteDecision(
                "final_failed",
                f"repair status {result.status} cannot continue",
            )
        attempts = int(state.get("repair_attempt", 0))
        max_attempts = int(state.get("max_repair_attempts", 3))
        if result.status == "failed" and _has_stage(state, "debug") and attempts < max_attempts:
            return RouteDecision(
                "debugging",
                f"repair failed at attempt {attempts}/{max_attempts}; retry debugging",
            )
        return RouteDecision(
            "final_failed",
            f"repair failed at attempt {attempts}/{max_attempts}",
        )


def _selected_stage_values(state: AgentState) -> list[str]:
    return [normalize_stage(stage).value for stage in state.get("selected_stages", [])]


def _has_stage(state: AgentState, stage: str) -> bool:
    return stage in _selected_stage_values(state)


def _stage_result(state: AgentState, stage: str) -> StageResult | None:
    stage_results = state.get("stage_results", {})
    aliases = {stage, _canonical_stage_key(stage)}
    for key in aliases:
        raw = stage_results.get(key)
        if raw is None:
            continue
        if isinstance(raw, StageResult):
            return raw
        if isinstance(raw, dict):
            return StageResult.model_validate(raw)
    return None


def _canonical_stage_key(stage: str) -> str:
    aliases = {
        "implement": "implementation",
        "implementation": "implementation",
        "test": "testing",
        "testing": "testing",
        "debug": "debugging",
        "debugging": "debugging",
        "repair": "repair",
    }
    return aliases.get(stage, stage)
