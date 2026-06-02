"""Streaming event normalization for CLI progress rendering."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any


def stream_workflow_events(raw_events: Iterable[Any]) -> Iterator[dict[str, Any]]:
    seen_route_events = 0
    final_status: str | None = None

    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            yield {"type": "raw_event", "payload": raw_event}
            continue
        for node, update in raw_event.items():
            yield {"type": "node_completed", "node": node}
            if not isinstance(update, dict):
                continue

            route_events = update.get("decision_trace") or []
            if isinstance(route_events, list):
                for route_event in route_events[seen_route_events:]:
                    if isinstance(route_event, dict):
                        yield dict(route_event)
                seen_route_events = len(route_events)

            stage_results = update.get("stage_results") or {}
            if isinstance(stage_results, dict):
                stage = _completed_stage(node, update, stage_results)
                if stage is not None:
                    result = stage_results.get(stage)
                    if not isinstance(result, dict):
                        continue
                    yield {
                        "type": "stage_result",
                        "stage": stage,
                        "status": result.get("status"),
                        "summary": result.get("summary", ""),
                    }

            current_final_status = update.get("final_status")
            if (
                isinstance(current_final_status, str)
                and current_final_status != final_status
            ):
                final_status = current_final_status
                yield {"type": "final_status", "status": current_final_status}


def _completed_stage(
    node: str,
    update: dict[str, Any],
    stage_results: dict[str, Any],
) -> str | None:
    current_node = update.get("current_node")
    if isinstance(current_node, str) and current_node in stage_results:
        return current_node
    if node in stage_results:
        return node
    return None
