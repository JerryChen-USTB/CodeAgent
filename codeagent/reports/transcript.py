"""Append-only JSONL recorder for transcripts and decision traces."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codeagent import filesystem as fs
from codeagent.reports.jsonl_utils import jsonl_path_lock


class JsonlRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        fs.mkdir(self.path.parent)
        fs.touch(self.path)

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with jsonl_path_lock(self.path):
            fs.append_text(self.path, line)
        return payload
