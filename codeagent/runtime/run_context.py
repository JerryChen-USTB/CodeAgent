"""Run directory initialization and context objects."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from codeagent import filesystem as fs
from codeagent.config import defaults
from codeagent.config.schema import Stage, TaskConfig
from codeagent.reports.artifact_store import ArtifactStore
from codeagent.reports.transcript import JsonlRecorder


STAGE_DIR_NAMES = {
    Stage.IMPLEMENT: "implementation",
    Stage.TEST: "testing",
    Stage.DEBUG: "debugging",
    Stage.REPAIR: "repair",
}


@dataclass(frozen=True)
class RunContext:
    run_id: str
    run_dir: Path
    task_config: TaskConfig
    stage_dirs: dict[Stage, Path]
    artifact_store: ArtifactStore
    transcript: JsonlRecorder
    decision_trace: JsonlRecorder


def create_run_context(
    task_config: TaskConfig, *, output_root: str | Path | None = None
) -> RunContext:
    """Create a new run directory and required baseline artifacts."""
    output_base = Path(output_root or task_config.output_dir or defaults.DEFAULT_OUTPUT_DIR)
    fs.mkdir(output_base)
    run_id, run_dir = _create_unique_run_dir(output_base, task_config)

    stage_dirs = _create_stage_dirs(run_dir)
    fs.mkdir(run_dir / "benchmark", parents=False, exist_ok=False)
    _initialize_sqlite_checkpoint(run_dir / "checkpoints.sqlite")
    _write_metadata(run_dir / "metadata.json", run_id, task_config)
    _write_task_config(run_dir / "task_config.yaml", task_config)
    fs.touch(run_dir / "transcript.jsonl")
    fs.touch(run_dir / "decision_trace.jsonl")
    fs.write_text(
        run_dir / "final_report.md",
        "# Final Report\n\nRun has been initialized. No stages have completed yet.\n",
    )
    artifact_store = ArtifactStore.create(run_dir, run_id=run_id)
    artifact_store.write()

    return RunContext(
        run_id=run_id,
        run_dir=run_dir,
        task_config=task_config,
        stage_dirs=stage_dirs,
        artifact_store=artifact_store,
        transcript=JsonlRecorder(run_dir / "transcript.jsonl"),
        decision_trace=JsonlRecorder(run_dir / "decision_trace.jsonl"),
    )


def _create_unique_run_dir(output_base: Path, task_config: TaskConfig) -> tuple[str, Path]:
    stages = "-".join(stage.value for stage in task_config.stages)
    for _ in range(20):
        created_at = _utc_now()
        stamp = created_at.strftime("%Y-%m-%d_%H%M%S_%f")
        digest = _short_hash(task_config.project_path, stages, stamp, uuid4().hex)
        run_id = f"{stamp}_{stages}_{digest}"
        run_dir = output_base / run_id
        try:
            fs.mkdir(run_dir, parents=False, exist_ok=False)
        except FileExistsError:
            continue
        return run_id, run_dir
    raise RuntimeError("Unable to create a unique run directory after 20 attempts.")


def _create_stage_dirs(run_dir: Path) -> dict[Stage, Path]:
    stage_dirs: dict[Stage, Path] = {}
    for stage, dirname in STAGE_DIR_NAMES.items():
        path = run_dir / dirname
        fs.mkdir(path, parents=False, exist_ok=False)
        stage_dirs[stage] = path
    return stage_dirs


def _initialize_sqlite_checkpoint(path: Path) -> None:
    with closing(sqlite3.connect(str(fs.portable_path(path)))) as conn:
        conn.execute("PRAGMA user_version = 1")


def _write_metadata(path: Path, run_id: str, task_config: TaskConfig) -> None:
    metadata = {
        "run_id": run_id,
        "created_at": _utc_now().isoformat(),
        "mode": task_config.mode,
        "stages": [stage.value for stage in task_config.stages],
        "project_path": str(task_config.project_path),
        "language": task_config.language,
        "test_framework": task_config.test_framework,
        "test_command": task_config.test_command.command,
        "model": {
            "provider": task_config.model.provider,
            "model_name": task_config.model.model_name,
            "base_url": task_config.model.base_url,
            "api_key_env": task_config.model.api_key_env,
        },
        "checkpoint": {
            "type": task_config.runtime.checkpoint,
            "path": "checkpoints.sqlite",
            "thread_id": run_id,
        },
    }
    fs.write_text(path, json.dumps(metadata, indent=2, ensure_ascii=False))


def _write_task_config(path: Path, task_config: TaskConfig) -> None:
    data = task_config.model_dump(mode="json", exclude_none=True)
    data["stages"] = [stage.value for stage in task_config.stages]
    fs.write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _short_hash(*parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:6]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
