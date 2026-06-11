"""Run observability inspection and health summary generation."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from codeagent import filesystem as fs
from codeagent.reports import ArtifactKind, ArtifactRecord, ArtifactStore
from codeagent.reports.jsonl_utils import validate_jsonl


STAGE_ORDER = ("implementation", "testing", "debugging", "repair")
MAX_WINDOWS_PATH_WARNING = 240


@dataclass(frozen=True)
class RunHealthArtifacts:
    json_path: Path
    markdown_path: Path
    payload: dict[str, Any]


def write_run_health_summary(run_dir: str | Path) -> RunHealthArtifacts:
    """Inspect a run directory and persist machine/human-readable health summaries."""
    run_path = Path(run_dir)
    payload = inspect_run_health(run_path)
    json_path = run_path / "run_health.json"
    markdown_path = run_path / "run_health.md"
    fs.write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2))
    fs.write_text(markdown_path, render_run_health_markdown(payload))
    _register_run_health_artifacts(run_path, json_path, markdown_path)
    return RunHealthArtifacts(
        json_path=json_path,
        markdown_path=markdown_path,
        payload=payload,
    )


def inspect_run_health(run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir)
    events, event_issues = _load_workflow_events(run_path)
    stage_results = _load_stage_results(run_path)
    decisions, decision_issues = _load_jsonl(run_path / "decision_trace.jsonl")
    metadata = _load_json(run_path / "metadata.json")
    final_status = _final_status(events, stage_results)
    route = _route_decisions(events)
    phase_timeline = _phase_timeline(events)
    stage_summaries = _stage_summaries(stage_results, events)
    llm_summary = _llm_summary(events)
    approval_summary = _approval_summary(decisions, events)
    test_summary = _test_summary(run_path, events)
    path_warnings = _path_warnings(run_path, events)
    warnings = _health_warnings(
        final_status=final_status,
        stage_summaries=stage_summaries,
        event_issues=event_issues,
        decision_issues=decision_issues,
        path_warnings=path_warnings,
    )
    return {
        "schema_version": 1,
        "run_dir": str(run_path),
        "run_id": _run_id(run_path, metadata, events),
        "final_status": final_status,
        "healthy": final_status == "succeeded" and not any(
            warning["severity"] == "error" for warning in warnings
        ),
        "metadata": metadata,
        "route": route,
        "phase_timeline": phase_timeline,
        "stage_summaries": stage_summaries,
        "llm_summary": llm_summary,
        "approval_summary": approval_summary,
        "test_summary": test_summary,
        "jsonl_validation": {
            "workflow_events": [issue.to_json_dict() for issue in event_issues],
            "decision_trace": [issue.to_json_dict() for issue in decision_issues],
        },
        "path_warnings": path_warnings,
        "warnings": warnings,
        "generated_at": datetime.now().astimezone().isoformat(),
    }


def render_run_health_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Run Health Summary",
        "",
        f"- run_id: `{payload.get('run_id') or ''}`",
        f"- final_status: `{payload.get('final_status') or 'unknown'}`",
        f"- healthy: `{bool(payload.get('healthy'))}`",
        "",
        "## Stage Timeline",
        "",
        "| Stage | Status | Phase Start | Stage Result | Summary |",
        "|---|---|---|---|---|",
    ]
    for item in payload.get("stage_summaries", []):
        lines.append(
            "| {stage} | {status} | {phase_started_at} | {stage_result_at} | {summary} |".format(
                stage=item.get("stage", ""),
                status=item.get("status", ""),
                phase_started_at=item.get("phase_started_at") or "",
                stage_result_at=item.get("stage_result_at") or "",
                summary=_table_text(item.get("summary") or ""),
            )
        )
    lines.extend(["", "## Route", ""])
    for item in payload.get("route", []):
        lines.append(
            "- `{timestamp}`: `{from_node}` -> `{to_node}` ({reason})".format(
                timestamp=item.get("timestamp", ""),
                from_node=item.get("from_node", ""),
                to_node=item.get("to_node", ""),
                reason=item.get("reason", ""),
            )
        )
    lines.extend(["", "## LLM Calls", ""])
    llm = payload.get("llm_summary", {})
    lines.extend(
        [
            f"- prompts: `{llm.get('prompt_count', 0)}`",
            f"- responses: `{llm.get('response_count', 0)}`",
            f"- structured_outputs: `{llm.get('structured_output_count', 0)}`",
            f"- retries: `{llm.get('retry_count', 0)}`",
            f"- invalid_attempts: `{llm.get('invalid_attempt_count', 0)}`",
            "",
            "## Tests",
            "",
        ]
    )
    for item in payload.get("test_summary", []):
        lines.append(
            "- `{stage}` `{command}`: success=`{success}`, passed=`{passed}`, "
            "failed=`{failed}`, total=`{total}`".format(
                stage=item.get("stage", ""),
                command=item.get("command", ""),
                success=item.get("success"),
                passed=item.get("passed", 0),
                failed=item.get("failed", 0),
                total=item.get("total", 0),
            )
        )
    lines.extend(["", "## Approvals", ""])
    approvals = payload.get("approval_summary", {})
    lines.extend(
        [
            f"- requested: `{approvals.get('requested_count', 0)}`",
            f"- decisions: `{approvals.get('decision_count', 0)}`",
            f"- manual_decisions: `{approvals.get('manual_decision_count', 0)}`",
            f"- auto_decisions: `{approvals.get('auto_decision_count', 0)}`",
            "",
            "## Warnings",
            "",
        ]
    )
    warnings = payload.get("warnings") or []
    if not warnings:
        lines.append("- None")
    else:
        for warning in warnings:
            lines.append(
                f"- `{warning.get('severity')}` `{warning.get('code')}`: "
                f"{warning.get('message')}"
            )
    lines.extend(["", "## Long Path Risks", ""])
    path_warnings = payload.get("path_warnings") or []
    if not path_warnings:
        lines.append("- None")
    else:
        for warning in path_warnings[:20]:
            lines.append(
                f"- `{warning.get('length')}` chars: `{warning.get('path')}`"
            )
    return "\n".join(lines) + "\n"


def render_run_health_console(payload: dict[str, Any]) -> str:
    lines = [
        f"运行目录：{payload.get('run_dir')}",
        f"最终状态：{payload.get('final_status')}",
        f"健康状态：{'通过' if payload.get('healthy') else '需关注'}",
        "",
        "阶段：",
    ]
    for item in payload.get("stage_summaries", []):
        lines.append(
            f"- {item.get('stage')}: {item.get('status')} | {item.get('summary')}"
        )
    llm = payload.get("llm_summary", {})
    lines.extend(
        [
            "",
            "LLM："
            f" prompts={llm.get('prompt_count', 0)},"
            f" responses={llm.get('response_count', 0)},"
            f" retries={llm.get('retry_count', 0)},"
            f" invalid={llm.get('invalid_attempt_count', 0)}",
        ]
    )
    tests = payload.get("test_summary", [])
    if tests:
        lines.append("")
        lines.append("测试：")
        for item in tests:
            lines.append(
                f"- {item.get('stage')}: success={item.get('success')}, "
                f"{item.get('passed', 0)}/{item.get('total', 0)} passed, "
                f"failed={item.get('failed', 0)}"
            )
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("警告：")
        for warning in warnings:
            lines.append(
                f"- [{warning.get('severity')}] {warning.get('code')}: "
                f"{warning.get('message')}"
            )
    return "\n".join(lines)


def _load_workflow_events(run_dir: Path) -> tuple[list[dict[str, Any]], list[Any]]:
    path = run_dir / "workflow_events.jsonl"
    issues = validate_jsonl(path) if fs.exists(path) else []
    events, _ = _load_jsonl(path)
    return events, issues


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[Any]]:
    if not fs.exists(path):
        return [], []
    issues = validate_jsonl(path)
    events: list[dict[str, Any]] = []
    try:
        lines = fs.read_text(path).splitlines()
    except OSError:
        return [], issues
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events, issues


def _load_json(path: Path) -> dict[str, Any]:
    if not fs.exists(path):
        return {}
    try:
        payload = json.loads(fs.read_text(path))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_stage_results(run_dir: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for stage in STAGE_ORDER:
        path = run_dir / stage / "stage_result.json"
        if not fs.exists(path):
            continue
        payload = _load_json(path)
        if payload:
            results[stage] = payload
    return results


def _final_status(
    events: list[dict[str, Any]],
    stage_results: dict[str, dict[str, Any]],
) -> str:
    for event in reversed(events):
        if event.get("event_type") == "run_completed":
            return str(event.get("final_status") or "unknown")
        if (
            event.get("event_type") == "workflow_event"
            and event.get("type") == "final_status"
        ):
            return str(event.get("status") or "unknown")
    if not stage_results:
        return "unknown"
    if any(result.get("status") == "succeeded" for result in stage_results.values()):
        last = list(stage_results.values())[-1]
        return str(last.get("status") or "unknown")
    return "unknown"


def _route_decisions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    route = []
    for event in events:
        if event.get("event_type") == "workflow_event" and event.get("type") == "route_decision":
            route.append(
                {
                    "timestamp": event.get("timestamp"),
                    "from_node": event.get("from_node"),
                    "to_node": event.get("to_node"),
                    "reason": event.get("reason"),
                }
            )
    return route


def _phase_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline = []
    for event in events:
        if event.get("event_type") != "workflow_event":
            continue
        if event.get("type") in {"phase_started", "stage_result", "final_status"}:
            timeline.append(
                {
                    "timestamp": event.get("timestamp"),
                    "stage": event.get("stage"),
                    "type": event.get("type"),
                    "status": event.get("status"),
                    "message": event.get("message"),
                    "summary": event.get("summary"),
                }
            )
    return timeline


def _stage_summaries(
    stage_results: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    phase_started = {
        event.get("stage"): event.get("timestamp")
        for event in events
        if event.get("event_type") == "workflow_event"
        and event.get("type") == "phase_started"
    }
    stage_event_time = {
        event.get("stage"): event.get("timestamp")
        for event in events
        if event.get("event_type") == "workflow_event"
        and event.get("type") == "stage_result"
    }
    summaries = []
    for stage in STAGE_ORDER:
        result = stage_results.get(stage)
        if not result:
            continue
        summaries.append(
            {
                "stage": stage,
                "status": result.get("status"),
                "summary": result.get("summary"),
                "phase_started_at": phase_started.get(stage),
                "stage_result_at": stage_event_time.get(stage),
                "stage_result_started_at": result.get("started_at"),
                "stage_result_ended_at": result.get("ended_at"),
                "error": result.get("error"),
                "artifact_ids": result.get("artifact_ids", []),
                "next_suggestion": result.get("next_suggestion", ""),
            }
        )
    return summaries


def _llm_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    counters = Counter(
        event.get("event_type")
        for event in events
        if str(event.get("event_type", "")).startswith("llm_")
    )
    by_stage: dict[str, Counter[str]] = defaultdict(Counter)
    invalid_attempt_count = 0
    model_error_count = 0
    for event in events:
        event_type = event.get("event_type")
        if not str(event_type).startswith("llm_"):
            continue
        stage = str(event.get("stage") or "unknown")
        by_stage[stage][str(event_type)] += 1
        if event_type == "llm_attempt_validation":
            status = event.get("status")
            if status == "invalid":
                invalid_attempt_count += 1
            if status == "model_error":
                model_error_count += 1
    return {
        "prompt_count": counters.get("llm_prompt", 0),
        "response_count": counters.get("llm_response", 0),
        "structured_output_count": counters.get("llm_structured_output", 0),
        "retry_count": counters.get("llm_retry_scheduled", 0),
        "invalid_attempt_count": invalid_attempt_count,
        "model_error_count": model_error_count,
        "by_stage": {
            stage: dict(counter)
            for stage, counter in sorted(by_stage.items())
        },
    }


def _approval_summary(
    decisions: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    requested = [event for event in events if event.get("event_type") == "approval_requested"]
    decision_events = [
        event for event in events if event.get("event_type") == "approval_decision"
    ]
    if not decisions:
        decisions = decision_events
    manual = [item for item in decisions if not item.get("auto")]
    auto = [item for item in decisions if item.get("auto")]
    by_interrupt = Counter(str(item.get("interrupt_id") or "") for item in decisions)
    return {
        "requested_count": len(requested),
        "decision_count": len(decisions),
        "manual_decision_count": len(manual),
        "auto_decision_count": len(auto),
        "by_interrupt": dict(sorted(by_interrupt.items())),
    }


def _test_summary(run_dir: Path, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for stage, filename in (
        ("testing", "test_result.json"),
        ("repair", "repair_test_result.json"),
    ):
        payload = _load_json(run_dir / stage / filename)
        if payload:
            payload = dict(payload)
            payload["stage"] = stage
            summaries.append(payload)
    if summaries:
        return summaries
    for event in events:
        if event.get("event_type") == "workflow_event" and event.get("type") == "test_result":
            summaries.append(
                {
                    "stage": event.get("stage"),
                    "success": (event.get("failed") or 0) == 0,
                    "passed": event.get("passed", 0),
                    "failed": event.get("failed", 0),
                    "errors": event.get("errors", 0),
                    "skipped": event.get("skipped", 0),
                    "total": event.get("total", 0),
                }
            )
    return summaries


def _path_warnings(run_dir: Path, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: set[str] = {str(run_dir)}
    for event in events:
        for key, value in event.items():
            if key.endswith("_path") or key.endswith("_dir"):
                if isinstance(value, str):
                    candidates.add(value)
        call_dir = event.get("call_dir")
        if isinstance(call_dir, str):
            candidates.add(str(run_dir / call_dir))
    warnings = []
    for raw in sorted(candidates):
        path_text = raw.replace("/", "\\")
        length = len(path_text)
        if length >= MAX_WINDOWS_PATH_WARNING:
            warnings.append(
                {
                    "path": raw,
                    "length": length,
                    "severity": "warning",
                    "message": "Path is near or above legacy Windows MAX_PATH limits.",
                }
            )
    return warnings


def _health_warnings(
    *,
    final_status: str,
    stage_summaries: list[dict[str, Any]],
    event_issues: list[Any],
    decision_issues: list[Any],
    path_warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if final_status != "succeeded":
        warnings.append(
            {
                "severity": "error",
                "code": "run_not_succeeded",
                "message": f"Run final status is {final_status}.",
            }
        )
    for item in stage_summaries:
        if item.get("status") == "failed":
            severity = "info" if final_status == "succeeded" else "error"
            warnings.append(
                {
                    "severity": severity,
                    "code": "stage_failed_then_recovered"
                    if severity == "info"
                    else "stage_failed",
                    "stage": item.get("stage"),
                    "message": str(item.get("summary") or ""),
                }
            )
    if event_issues:
        warnings.append(
            {
                "severity": "error",
                "code": "workflow_events_jsonl_invalid",
                "message": f"workflow_events.jsonl has {len(event_issues)} invalid line(s).",
            }
        )
    if decision_issues:
        warnings.append(
            {
                "severity": "error",
                "code": "decision_trace_jsonl_invalid",
                "message": f"decision_trace.jsonl has {len(decision_issues)} invalid line(s).",
            }
        )
    if path_warnings:
        warnings.append(
            {
                "severity": "warning",
                "code": "long_path_risk",
                "message": f"{len(path_warnings)} observed path(s) may be hard to inspect on Windows.",
            }
        )
    return warnings


def _register_run_health_artifacts(
    run_dir: Path,
    json_path: Path,
    markdown_path: Path,
) -> None:
    index_path = run_dir / "artifacts_index.json"
    if not fs.exists(index_path):
        return
    try:
        store = ArtifactStore.load(run_dir)
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return
    store.record(
        ArtifactRecord(
            artifact_id="run_health_json",
            stage="final",
            kind=ArtifactKind.JSON,
            path=json_path,
            summary="Machine-readable run health summary",
        )
    )
    store.record(
        ArtifactRecord(
            artifact_id="run_health_report",
            stage="final",
            kind=ArtifactKind.REPORT,
            path=markdown_path,
            summary="Human-readable run health summary",
        )
    )
    store.write()


def _run_id(
    run_dir: Path,
    metadata: dict[str, Any],
    events: list[dict[str, Any]],
) -> str:
    if metadata.get("run_id"):
        return str(metadata["run_id"])
    for event in events:
        if event.get("run_id"):
            return str(event["run_id"])
    return run_dir.name


def _table_text(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
