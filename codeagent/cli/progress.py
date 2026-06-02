"""Small helpers for consistent CLI status output."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel


class ProgressReporter:
    """Render concise status panels for command skeletons."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    def planned(self, command: str, detail: str) -> None:
        self._console.print(
            Panel(
                detail,
                title=f"{command} not implemented yet",
                border_style="yellow",
            )
        )
