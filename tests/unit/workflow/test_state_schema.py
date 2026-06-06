from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from codeagent.errors.exceptions import CodeAgentError, ErrorRecord
from codeagent.reports.schemas import (
    CodeChange,
    DebugResult,
    HumanDecision,
    RepairResult,
    StageResult,
    TestResultRecord,
    ToolCallRecord,
)
from codeagent.workflow.state import (
    CheckpointSafetyError,
    create_initial_state,
    state_to_json_dict,
)


def test_initial_state_is_checkpoint_safe_json() -> None:
    state = create_initial_state(
        run_id="run-001",
        mode="run",
        selected_stages=["implement", "test"],
    )

    payload = state_to_json_dict(state)
    encoded = json.dumps(payload, ensure_ascii=False)
    decoded = json.loads(encoded)

    assert decoded["run_id"] == "run-001"
    assert decoded["mode"] == "run"
    assert decoded["selected_stages"] == ["implement", "test"]
    assert decoded["current_stage"] is None
    assert decoded["stage_results"] == {}


def test_state_to_json_dict_converts_nested_paths_to_posix_strings() -> None:
    state = create_initial_state(run_id="run-002", mode="benchmark", selected_stages=["test"])
    state["pending_interrupt"] = {
        "action": "approve_command",
        "log_path": Path("testing") / "pytest.log",
        "choices": [{"patch": Path("implementation") / "patch.diff"}],
    }

    payload = state_to_json_dict(state)

    assert payload["pending_interrupt"]["log_path"] == "testing/pytest.log"
    assert payload["pending_interrupt"]["choices"][0]["patch"] == "implementation/patch.diff"
    json.dumps(payload)


def test_state_to_json_dict_rejects_unserializable_values() -> None:
    state = create_initial_state(run_id="run-003", mode="run", selected_stages=["implement"])
    state["pending_interrupt"] = {"raw_object": object()}

    with pytest.raises(CheckpointSafetyError, match="not checkpoint safe"):
        state_to_json_dict(state)


@pytest.mark.parametrize("bad_float", [math.nan, math.inf, -math.inf])
def test_state_to_json_dict_rejects_non_finite_floats(bad_float: float) -> None:
    state = create_initial_state(run_id="run-004", mode="run", selected_stages=["test"])
    state["pending_interrupt"] = {"score": bad_float}

    with pytest.raises(CheckpointSafetyError, match="not checkpoint safe"):
        state_to_json_dict(state)


def test_stage_result_json_roundtrip_preserves_artifact_references() -> None:
    error = ErrorRecord(
        error_id="err-1",
        stage="testing",
        node="run_tests",
        category="pytest_failure",
        message="pytest returned exit code 1",
        artifact_ids=["testing_log"],
    )
    result = StageResult(
        stage="testing",
        status="failed",
        started_at="2026-06-03T04:00:00Z",
        ended_at="2026-06-03T04:01:00Z",
        summary="Regression command failed; see log artifact.",
        artifact_ids=["testing_log", "test_report"],
        report_path=Path("testing") / "stage_result.json",
        error=error,
    )

    payload = result.model_dump(mode="json")
    restored = StageResult.model_validate_json(result.model_dump_json())

    assert payload["report_path"] == "testing/stage_result.json"
    assert restored.artifact_ids == ["testing_log", "test_report"]
    assert restored.error is not None
    assert restored.error.category == "pytest_failure"


def test_stage_result_rejects_large_raw_content_in_summary() -> None:
    with pytest.raises(ValidationError, match="summary"):
        StageResult(
            stage="implement",
            status="succeeded",
            started_at="2026-06-03T04:00:00Z",
            ended_at="2026-06-03T04:01:00Z",
            summary="x" * 8001,
        )


def test_report_records_validate_and_roundtrip_paths() -> None:
    test_result = TestResultRecord(
        command="python -m pytest tests/unit -q",
        exit_code=1,
        passed=3,
        failed=1,
        errors=0,
        skipped=0,
        log_paths=[Path("testing") / "pytest.log"],
    )
    repair = RepairResult(
        patch_path=Path("repair") / "repair.patch.diff",
        changed_files=["src/example.py"],
        before_result=test_result,
        after_result=None,
        success=False,
    )

    payload = repair.model_dump(mode="json")
    restored = RepairResult.model_validate_json(repair.model_dump_json())

    assert payload["patch_path"] == "repair/repair.patch.diff"
    assert payload["before_result"]["log_paths"] == ["testing/pytest.log"]
    assert restored.before_result is not None
    assert restored.before_result.success is False


def test_report_records_reject_invalid_status_or_decision() -> None:
    with pytest.raises(ValidationError):
        ToolCallRecord(
            call_id="call-1",
            tool_name="shell.run",
            args_summary={"command": "pytest"},
            result_summary="denied by policy",
            status="maybe",
            artifact_ids=[],
            timestamp="2026-06-03T04:00:00Z",
        )

    with pytest.raises(ValidationError):
        HumanDecision(
            interrupt_id="interrupt-1",
            action="approve_command",
            decision_type="ignore",
            comment="invalid",
            timestamp="2026-06-03T04:00:00Z",
        )


def test_human_decision_and_tool_call_preserve_non_execute_outcomes() -> None:
    decision = HumanDecision(
        interrupt_id="interrupt-2",
        action="approve_command",
        decision_type="edit",
        edited_payload={"command": "python -m pytest tests/unit/workflow -q"},
        comment="Narrow command before execution.",
        timestamp="2026-06-03T04:00:00Z",
    )
    skipped_tool = ToolCallRecord(
        call_id="call-2",
        tool_name="shell.run",
        args_summary={"command": "pytest"},
        result_summary="Skipped after human response.",
        status="skipped",
        artifact_ids=[],
        timestamp="2026-06-03T04:00:01Z",
    )

    restored_decision = HumanDecision.model_validate_json(decision.model_dump_json())
    restored_tool = ToolCallRecord.model_validate_json(skipped_tool.model_dump_json())

    assert restored_decision.edited_payload == {
        "command": "python -m pytest tests/unit/workflow -q"
    }
    assert restored_tool.status == "skipped"


def test_codeagent_error_converts_to_error_record() -> None:
    exc = CodeAgentError(
        "tool failed",
        stage="testing",
        node="run_tests",
        category="tool",
        retryable=True,
        artifact_ids=["tool_log"],
    )

    record = exc.to_record(error_id="err-tool")
    restored = ErrorRecord.model_validate_json(record.model_dump_json())

    assert restored.error_id == "err-tool"
    assert restored.category == "tool"
    assert restored.retryable is True
    assert restored.artifact_ids == ["tool_log"]


def test_debug_and_code_change_records_are_json_serializable() -> None:
    debug = DebugResult(
        failing_tests=["tests/test_example.py::test_failure"],
        suspect_files=["src/example.py"],
        root_cause="Off-by-one in boundary check.",
        confidence="medium",
    )
    change = CodeChange(
        file_path=Path("src") / "example.py",
        change_type="modified",
        diff_artifact_id="implementation_patch",
        approved=True,
    )

    encoded = json.dumps(
        {
            "debug": debug.model_dump(mode="json"),
            "change": change.model_dump(mode="json"),
        }
    )

    assert "src/example.py" in encoded
    assert "Off-by-one" in encoded
