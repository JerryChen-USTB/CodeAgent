"""Detailed workflow trace logs for human audit and machine checks."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codeagent import filesystem as fs
from codeagent.context.redaction import redact_sensitive_text
from codeagent.reports.jsonl_utils import jsonl_path_lock


_MAX_TEXT_FIELD_CHARS = 200_000


class WorkflowTraceRecorder:
    """Append structured workflow events to JSONL and a readable log file."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.log_path = run_dir / "workflow.log"
        self.events_path = run_dir / "workflow_events.jsonl"
        fs.mkdir(run_dir)
        if not self.log_path.exists():
            fs.write_text(
                self.log_path,
                "# CodeAgent Workflow Trace\n\n"
                "This file records the visible workflow, approvals, LLM calls, "
                "artifacts, state transitions, and command results. Secrets, API "
                "keys, and hidden benchmark oracle material are redacted.\n\n",
            )
        fs.touch(self.events_path)

    def record(self, event_type: str, **payload: Any) -> dict[str, Any]:
        event = _sanitize_event(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                **payload,
            }
        )
        event_line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        log_entry = _render_event(event)
        with jsonl_path_lock(self.events_path):
            fs.append_text(self.events_path, event_line)
            fs.append_text(self.log_path, log_entry)
        return event


def _sanitize_event(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_event(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [_sanitize_event(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_event(item) for item in value]
    if isinstance(value, Path):
        return _sanitize_text(str(value))
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in {
        "api_key",
        "authorization",
        "bearer",
        "password",
        "secret",
        "token",
    }


def _sanitize_text(text: str) -> str:
    redacted = redact_sensitive_text(text)
    redacted = re.sub(
        r"(?i)(?:^|[\\/])oracle_tests(?:[\\/][^\s,;:'\")}\]]*)*",
        "/<hidden benchmark oracle_tests>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(?:^|[\\/])evaluation(?:[\\/][^\s,;:'\")}\]]*)*",
        "/<hidden benchmark evaluation>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)expected_result\.json",
        "<hidden benchmark expected_result.json>",
        redacted,
    )
    if len(redacted) > _MAX_TEXT_FIELD_CHARS:
        return (
            redacted[:_MAX_TEXT_FIELD_CHARS]
            + f"\n<truncated {len(redacted) - _MAX_TEXT_FIELD_CHARS} chars>"
        )
    return redacted


def _render_event(event: dict[str, Any]) -> str:
    timestamp = event.get("timestamp", "")
    event_type = event.get("event_type", "event")
    stage = event.get("stage")
    node = event.get("node")
    heading_parts = [str(timestamp), str(event_type)]
    if stage:
        heading_parts.append(f"stage={stage}")
    if node:
        heading_parts.append(f"node={node}")
    heading = " | ".join(heading_parts)
    body = json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True)
    return f"## {heading}\n\n```json\n{body}\n```\n\n"
