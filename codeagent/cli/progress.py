"""Small helpers for consistent CLI status output."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.panel import Panel


@dataclass(frozen=True)
class ProgressEventFormatter:
    """Format normalized workflow events as concise CLI progress lines."""

    def format_event(self, event: dict[str, Any]) -> str:
        event_type = str(event.get("type") or "event")
        if event_type == "node_completed":
            return f"[stage] {event.get('node', '<unknown>')} completed"
        if event_type == "route_decision":
            source = event.get("from_node") or event.get("from_stage") or "<unknown>"
            target = event.get("to_node") or event.get("to_stage") or "<unknown>"
            reason = event.get("reason") or ""
            suffix = f": {reason}" if reason else ""
            return f"[route] {source} -> {target}{suffix}"
        if event_type == "stage_result":
            stage = event.get("stage", "<unknown>")
            status = event.get("status", "<unknown>")
            summary = str(event.get("summary") or "").strip()
            suffix = f": {summary}" if summary else ""
            return f"[result] {stage} {status}{suffix}"
        if event_type == "tool_call":
            tool_name = event.get("tool_name") or event.get("name") or "<unknown>"
            status = event.get("status") or event.get("result") or "started"
            return f"[tool] {tool_name} {status}"
        if event_type == "final_status":
            return f"[final] {event.get('status', '<unknown>')}"
        if event_type == "human_decision":
            decision = event.get("decision_type", "<unknown>")
            action = event.get("action", "approval")
            return f"[approval] {action} {decision}"
        return f"[event] {event_type}"


class ProgressReporter:
    """Render concise status panels for command skeletons."""

    def __init__(
        self,
        console: Console | None = None,
        formatter: ProgressEventFormatter | None = None,
    ) -> None:
        self._console = console or Console()
        self._formatter = formatter or ProgressEventFormatter()

    def planned(self, command: str, detail: str) -> None:
        self._console.print(
            Panel(
                detail,
                title=f"{command} not implemented yet",
                border_style="yellow",
            )
        )

    def render_event(self, event: dict[str, Any]) -> str:
        line = self._formatter.format_event(event)
        self._console.print(line, markup=False)
        return line

    def render_events(self, events: Iterable[dict[str, Any]]) -> list[str]:
        return [self.render_event(event) for event in events]
