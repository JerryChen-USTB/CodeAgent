"""Streaming event normalization for CLI progress rendering."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any


def stream_workflow_events(raw_events: Iterable[Any]) -> Iterator[dict[str, Any]]:
    seen_route_events = 0
    final_status: str | None = None
    emitted_message_status = False

    for raw_event in raw_events:
        if isinstance(raw_event, tuple):
            mode, payload = _split_stream_tuple(raw_event)
            if mode == "custom":
                if isinstance(payload, dict):
                    yield dict(payload)
                else:
                    yield {"type": "agent_status", "message": str(payload)}
                continue
            if mode == "messages":
                if not emitted_message_status:
                    emitted_message_status = True
                    yield {
                        "type": "agent_status",
                        "message": "模型正在生成结构化输出",
                    }
                continue
            if mode in {"updates", "values"}:
                raw_event = payload
            else:
                yield {"type": "raw_event", "stream_mode": mode, "payload": payload}
                continue
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
                        "error_message": _error_message(result),
                        "retryable": _error_retryable(result),
                        "next_suggestion": result.get("next_suggestion", ""),
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


def _split_stream_tuple(raw_event: tuple[Any, ...]) -> tuple[str, Any]:
    if len(raw_event) == 2 and isinstance(raw_event[0], str):
        return raw_event[0], raw_event[1]
    if len(raw_event) == 3 and isinstance(raw_event[1], str):
        return raw_event[1], raw_event[2]
    return "unknown", raw_event


def _error_message(result: dict[str, Any]) -> str:
    error = result.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "")
    return ""


def _error_retryable(result: dict[str, Any]) -> bool | None:
    error = result.get("error")
    if isinstance(error, dict) and isinstance(error.get("retryable"), bool):
        return bool(error["retryable"])
    return None
