"""Structured helpers for decision_trace.jsonl."""

from __future__ import annotations

from typing import Any

from codeagent.reports.schemas import HumanDecision
from codeagent.reports.transcript import JsonlRecorder


class DecisionTraceWriter:
    def __init__(self, recorder: JsonlRecorder) -> None:
        self.recorder = recorder

    def append_human_decision(self, decision: HumanDecision) -> dict[str, Any]:
        payload = decision.model_dump(mode="json", exclude_none=True)
        payload["type"] = "human_decision"
        return self.recorder.append(payload)

    def append_route_decision(
        self,
        *,
        from_stage: str,
        to_stage: str,
        reason: str,
    ) -> dict[str, Any]:
        return self.recorder.append(
            {
                "type": "route_decision",
                "from_stage": from_stage,
                "to_stage": to_stage,
                "reason": reason,
            }
        )
