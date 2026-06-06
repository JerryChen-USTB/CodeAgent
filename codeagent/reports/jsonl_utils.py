"""Utilities for append-only JSONL artifacts."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from codeagent import filesystem as fs


_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class JsonlValidationIssue:
    line_number: int
    error_type: str
    message: str
    preview: str

    def to_json_dict(self) -> dict[str, object]:
        return asdict(self)


def jsonl_path_lock(path: Path) -> threading.RLock:
    key = _lock_key(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def validate_jsonl(path: Path, *, preview_chars: int = 240) -> list[JsonlValidationIssue]:
    issues: list[JsonlValidationIssue] = []
    try:
        lines = fs.read_text(path).splitlines()
    except OSError as exc:
        return [
            JsonlValidationIssue(
                line_number=0,
                error_type=type(exc).__name__,
                message=str(exc),
                preview="",
            )
        ]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(
                JsonlValidationIssue(
                    line_number=line_number,
                    error_type=type(exc).__name__,
                    message=str(exc),
                    preview=line[:preview_chars],
                )
            )
    return issues


def recover_workflow_events_from_log(
    workflow_log_path: Path,
    *,
    output_path: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Recover workflow event JSONL from the readable workflow log.

    The original workflow_events.jsonl is intentionally left untouched. By default
    this writes workflow_events.jsonl.repaired next to workflow.log.
    """

    output = output_path or (workflow_log_path.parent / "workflow_events.jsonl.repaired")
    if fs.exists(output) and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing repaired JSONL: {output}")
    log_text = fs.read_text(workflow_log_path)
    lines: list[str] = []
    for match in re.finditer(r"```json\n(.*?)\n```", log_text, flags=re.DOTALL):
        block = match.group(1)
        try:
            payload: Any = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    fs.write_text(output, "\n".join(lines) + ("\n" if lines else ""))
    return output


def _lock_key(path: Path) -> str:
    try:
        return str(Path(path).resolve()).lower()
    except OSError:
        return str(path).lower()
