from __future__ import annotations

import json
from pathlib import Path

from codeagent import filesystem as fs
from codeagent.cli.inspect_run import (
    inspect_run_health,
    render_run_health_console,
    write_run_health_summary,
)


def _write_json(path: Path, payload: dict) -> None:
    fs.mkdir(path.parent)
    fs.write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _append_jsonl(path: Path, payload: dict) -> None:
    fs.mkdir(path.parent)
    fs.append_text(path, json.dumps(payload, ensure_ascii=False) + "\n")


def test_inspect_run_health_summarizes_recovered_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    fs.mkdir(run_dir)
    _write_json(
        run_dir / "metadata.json",
        {
            "run_id": "run-1",
            "stages": ["implement", "test", "debug", "repair"],
        },
    )
    _write_json(
        run_dir / "artifacts_index.json",
        {"run_id": "run-1", "artifacts": []},
    )
    for stage, status, summary in (
        ("implementation", "succeeded", "Implementation succeeded."),
        ("testing", "failed", "Testing command failed."),
        ("debugging", "succeeded", "Debugging succeeded."),
        ("repair", "succeeded", "Repair verification passed."),
    ):
        _write_json(
            run_dir / stage / "stage_result.json",
            {
                "stage": stage,
                "status": status,
                "started_at": "2026-06-11T00:00:00+00:00",
                "ended_at": "2026-06-11T00:01:00+00:00",
                "summary": summary,
                "artifact_ids": [],
                "next_suggestion": "continue" if status == "failed" else "",
                **(
                    {
                        "error": {
                            "error_id": "testing_pytest_failure",
                            "stage": "testing",
                            "node": "testing",
                            "category": "pytest_failure",
                            "message": "2 failed",
                            "severity": "error",
                            "retryable": True,
                            "artifact_ids": [],
                        }
                    }
                    if status == "failed"
                    else {}
                ),
            },
        )
    _write_json(
        run_dir / "testing" / "test_result.json",
        {
            "framework": "pytest",
            "success": False,
            "passed": 91,
            "failed": 2,
            "total": 93,
            "command": "python -m pytest -q",
        },
    )
    _write_json(
        run_dir / "repair" / "repair_test_result.json",
        {
            "framework": "pytest",
            "success": True,
            "passed": 93,
            "failed": 0,
            "total": 93,
            "command": "python -m pytest -q",
        },
    )
    events_path = run_dir / "workflow_events.jsonl"
    _append_jsonl(
        events_path,
        {
            "timestamp": "2026-06-11T00:00:00+00:00",
            "event_type": "run_initialized",
            "run_id": "run-1",
        },
    )
    _append_jsonl(
        events_path,
        {
            "timestamp": "2026-06-11T00:00:01+00:00",
            "event_type": "workflow_event",
            "type": "phase_started",
            "stage": "testing",
            "message": "start testing",
        },
    )
    _append_jsonl(
        events_path,
        {
            "timestamp": "2026-06-11T00:00:02+00:00",
            "event_type": "workflow_event",
            "type": "route_decision",
            "from_node": "testing",
            "to_node": "debugging",
            "reason": "testing failed; run debugging",
        },
    )
    _append_jsonl(
        events_path,
        {
            "timestamp": "2026-06-11T00:00:03+00:00",
            "event_type": "llm_prompt",
            "stage": "repair",
            "generation_kind": "single_file_patch_generation",
            "schema": "RepairPatchDraft",
            "call_dir": "repair/llm_calls/" + "x" * 230,
        },
    )
    _append_jsonl(
        events_path,
        {
            "timestamp": "2026-06-11T00:00:04+00:00",
            "event_type": "llm_response",
            "stage": "repair",
        },
    )
    _append_jsonl(
        events_path,
        {
            "timestamp": "2026-06-11T00:00:05+00:00",
            "event_type": "llm_structured_output",
            "stage": "repair",
        },
    )
    _append_jsonl(
        events_path,
        {
            "timestamp": "2026-06-11T00:00:06+00:00",
            "event_type": "run_completed",
            "final_status": "succeeded",
        },
    )

    artifacts = write_run_health_summary(run_dir)

    assert artifacts.payload["final_status"] == "succeeded"
    assert artifacts.payload["healthy"] is True
    assert artifacts.payload["llm_summary"]["prompt_count"] == 1
    assert artifacts.payload["test_summary"][0]["failed"] == 2
    assert artifacts.payload["test_summary"][1]["failed"] == 0
    assert any(
        warning["code"] == "stage_failed_then_recovered"
        for warning in artifacts.payload["warnings"]
    )
    assert any(
        warning["code"] == "long_path_risk"
        for warning in artifacts.payload["warnings"]
    )
    assert fs.exists(run_dir / "run_health.json")
    assert fs.exists(run_dir / "run_health.md")
    index = json.loads(fs.read_text(run_dir / "artifacts_index.json"))
    assert {item["artifact_id"] for item in index["artifacts"]} >= {
        "run_health_json",
        "run_health_report",
    }

    console_text = render_run_health_console(inspect_run_health(run_dir))
    assert "最终状态：succeeded" in console_text
    assert "testing: failed" in console_text
