"""Resume inspection helpers for previous run directories."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal

from langgraph.types import Command

from codeagent.reports import ArtifactStore
from codeagent.workflow.checkpoint import CheckpointManager, CheckpointStatus
from codeagent.workflow.factory import WorkflowFactory


ResumeStatus = Literal[
    "pending_interrupt",
    "completed",
    "checkpoint_available",
    "read_only_artifacts",
    "not_found",
]


@dataclass(frozen=True)
class ResumeSummary:
    run_id: str
    run_dir: Path
    status: ResumeStatus
    checkpoint_status: CheckpointStatus
    thread_config: dict[str, dict[str, str]]
    pending_interrupt: dict[str, Any] | None
    final_report_excerpt: str
    artifacts: list[dict[str, Any]]


def inspect_run_for_resume(output_root: str | Path, run_id: str) -> ResumeSummary:
    run_dir = Path(output_root) / run_id
    manager = CheckpointManager(run_dir, run_id=run_id)
    if not run_dir.exists() or not (run_dir / "task_config.yaml").exists():
        return ResumeSummary(
            run_id=run_id,
            run_dir=run_dir,
            status="not_found",
            checkpoint_status="missing",
            thread_config=manager.get_thread_config(),
            pending_interrupt=None,
            final_report_excerpt="",
            artifacts=[],
        )

    checkpoint_status = manager.checkpoint_status()
    pending_interrupt = manager.load_pending_interrupt()
    artifacts = _load_artifacts(run_dir)
    final_report_excerpt = _read_final_report_excerpt(run_dir)
    if pending_interrupt is not None:
        status: ResumeStatus = "pending_interrupt"
    elif checkpoint_status == "available":
        status = "completed" if _has_final_report_artifact(artifacts) else "checkpoint_available"
    else:
        status = "read_only_artifacts"

    return ResumeSummary(
        run_id=run_id,
        run_dir=run_dir,
        status=status,
        checkpoint_status=checkpoint_status,
        thread_config=manager.get_thread_config(),
        pending_interrupt=pending_interrupt,
        final_report_excerpt=final_report_excerpt,
        artifacts=artifacts,
    )


def resume_run_from_checkpoint(
    output_root: str | Path,
    run_id: str,
    *,
    resume_value: Any,
    graph_builder=None,
) -> dict[str, Any]:
    summary = inspect_run_for_resume(output_root, run_id)
    if summary.checkpoint_status != "available":
        raise RuntimeError(
            f"Cannot resume run {run_id}: checkpoint is {summary.checkpoint_status}."
        )
    manager = CheckpointManager(summary.run_dir, run_id=run_id)
    with manager.create_sqlite_saver() as saver:
        if graph_builder is None:
            graph = WorkflowFactory().build(checkpointer=saver)
        else:
            graph = graph_builder(saver)
        result = graph.invoke(
            Command(resume=resume_value),
            config=manager.get_thread_config(),
        )
    manager.clear_pending_interrupt()
    return result


def parse_resume_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc.msg}") from exc


def render_resume_summary(summary: ResumeSummary) -> str:
    lines = [
        f"Run id: {summary.run_id}",
        f"Status: {summary.status}",
        f"Checkpoint: {summary.checkpoint_status}",
        f"Thread id: {summary.thread_config['configurable']['thread_id']}",
    ]
    if summary.pending_interrupt is not None:
        lines.append(f"Pending interrupt: {summary.pending_interrupt.get('interrupt_id', '<unknown>')}")
    if summary.final_report_excerpt:
        lines.extend(["", "Final Report", summary.final_report_excerpt])
    if summary.artifacts:
        lines.extend(["", "Artifacts:"])
        for artifact in summary.artifacts:
            lines.append(
                f"- {artifact.get('artifact_id')}: {artifact.get('path')} "
                f"({artifact.get('kind')})"
            )
    return "\n".join(lines)


def _load_artifacts(run_dir: Path) -> list[dict[str, Any]]:
    try:
        store = ArtifactStore.load(run_dir)
    except (FileNotFoundError, KeyError, ValueError):
        return []
    return [
        artifact.model_dump(mode="json", exclude_none=True)
        for artifact in store.artifacts
    ]


def _has_final_report_artifact(artifacts: list[dict[str, Any]]) -> bool:
    return any(artifact.get("artifact_id") == "final_report" for artifact in artifacts)


def _read_final_report_excerpt(run_dir: Path, *, limit: int = 4000) -> str:
    path = run_dir / "final_report.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]\n"
