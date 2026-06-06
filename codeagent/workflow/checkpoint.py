"""Checkpoint and pending-interrupt helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

from langgraph.checkpoint.sqlite import SqliteSaver

from codeagent import filesystem as fs
from codeagent.workflow.state import CheckpointSafetyError, state_to_json_dict


CheckpointStatus = Literal["available", "missing", "corrupt"]


@dataclass(frozen=True)
class CheckpointManager:
    run_dir: Path
    run_id: str | None = None

    @property
    def checkpoint_path(self) -> Path:
        return self.run_dir / "checkpoints.sqlite"

    @property
    def pending_interrupt_path(self) -> Path:
        return self.run_dir / "pending_interrupt.json"

    def get_thread_config(self) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": self.run_id or self.run_dir.name}}

    def initialize_sqlite(self) -> None:
        fs.mkdir(self.run_dir)
        with closing(sqlite3.connect(str(fs.portable_path(self.checkpoint_path)))) as conn:
            conn.execute("PRAGMA user_version = 1")

    @contextmanager
    def create_sqlite_saver(self) -> Iterator[SqliteSaver]:
        fs.mkdir(self.run_dir)
        with closing(
            sqlite3.connect(
                str(fs.portable_path(self.checkpoint_path)),
                check_same_thread=False,
            )
        ) as conn:
            saver = SqliteSaver(conn)
            saver.setup()
            yield saver

    def checkpoint_status(self) -> CheckpointStatus:
        if not fs.exists(self.checkpoint_path):
            return "missing"
        try:
            with closing(sqlite3.connect(str(fs.portable_path(self.checkpoint_path)))) as conn:
                conn.execute("PRAGMA schema_version").fetchone()
        except sqlite3.DatabaseError:
            return "corrupt"
        return "available"

    def save_pending_interrupt(self, payload: dict[str, Any]) -> dict[str, Any]:
        safe = state_to_json_dict({"pending_interrupt": payload})["pending_interrupt"]
        if not isinstance(safe, dict):
            raise CheckpointSafetyError("pending interrupt payload must be a JSON object")
        fs.write_text(
            self.pending_interrupt_path,
            json.dumps(safe, indent=2, ensure_ascii=False, allow_nan=False),
        )
        return safe

    def load_pending_interrupt(self) -> dict[str, Any] | None:
        if not fs.exists(self.pending_interrupt_path):
            return None
        try:
            payload = json.loads(fs.read_text(self.pending_interrupt_path))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def clear_pending_interrupt(self) -> None:
        if fs.exists(self.pending_interrupt_path):
            fs.unlink(self.pending_interrupt_path)
