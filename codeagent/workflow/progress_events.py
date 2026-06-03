"""Helpers for emitting LangGraph custom progress events."""

from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer


def emit_progress(event_type: str, **payload: Any) -> None:
    """Emit a best-effort custom stream event from inside a LangGraph node."""
    try:
        writer = get_stream_writer()
    except Exception:
        return
    writer({"type": event_type, **payload})
