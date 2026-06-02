from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeagent.reports.transcript import JsonlRecorder
from codeagent.tools.hitl import ApprovalDecision, ToolCall, ToolHITLInterceptor
from codeagent.tools.permissions import ToolCallContext, ToolPermissionPolicy
from codeagent.tools.registry import create_default_tool_registry


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_permission_policy_allows_readonly_and_denies_unknown_tools() -> None:
    policy = ToolPermissionPolicy()
    context = ToolCallContext(stage="debug", mode="run")

    allowed = policy.classify("read_file", {"path": "src/app.py"}, context)
    denied = policy.classify("delete_everything", {}, context)

    assert allowed.action == "allow"
    assert allowed.requires_approval is False
    assert denied.action == "deny"
    assert "unregistered" in denied.reason


def test_permission_policy_asks_for_side_effects_but_auto_approves_benchmark() -> None:
    policy = ToolPermissionPolicy()

    normal = policy.classify(
        "run_shell",
        {"command": "python -m pytest -q"},
        ToolCallContext(stage="test", mode="run"),
    )
    benchmark = policy.classify(
        "run_shell",
        {"command": "python -m pytest -q"},
        ToolCallContext(
            stage="test",
            mode="benchmark",
            auto_approve_in_benchmark=True,
        ),
    )

    assert normal.action == "ask"
    assert normal.requires_approval is True
    assert benchmark.action == "allow"
    assert benchmark.auto_approved is True


def test_permission_policy_denies_direct_calls_outside_stage_scope() -> None:
    policy = ToolPermissionPolicy()

    run_shell = policy.classify(
        "run_shell",
        {"command": "python -m pytest -q"},
        ToolCallContext(
            stage="implement",
            mode="benchmark",
            auto_approve_in_benchmark=True,
        ),
    )
    apply_patch = policy.classify(
        "apply_patch",
        {"patch_path": "change.diff"},
        ToolCallContext(
            stage="debug",
            mode="benchmark",
            auto_approve_in_benchmark=True,
        ),
    )

    assert run_shell.action == "deny"
    assert "not available in stage" in run_shell.reason
    assert apply_patch.action == "deny"
    assert "not available in stage" in apply_patch.reason


def test_permission_policy_allows_output_writes_only_under_output_dir(
    tmp_path: Path,
) -> None:
    policy = ToolPermissionPolicy()
    run_dir = tmp_path / "run"
    context = ToolCallContext(stage="test", mode="run", output_dir=run_dir)

    allowed = policy.classify(
        "write_report",
        {"path": run_dir / "testing" / "test_report.md"},
        context,
    )
    denied = policy.classify(
        "write_report",
        {"path": tmp_path / "outside.md"},
        context,
    )

    assert allowed.action == "allow"
    assert denied.action == "deny"
    assert "output directory" in denied.reason


def test_permission_policy_denies_malformed_output_write_paths(
    tmp_path: Path,
) -> None:
    policy = ToolPermissionPolicy()
    context = ToolCallContext(stage="test", mode="run", output_dir=tmp_path / "run")

    non_path = policy.classify("write_report", {"path": []}, context)
    invalid_path = policy.classify("write_report", {"path": "bad\0path.md"}, context)

    assert non_path.action == "deny"
    assert "invalid path" in non_path.reason
    assert invalid_path.action == "deny"
    assert "invalid path" in invalid_path.reason


def test_default_registry_scopes_tools_by_stage() -> None:
    registry = create_default_tool_registry()

    implement_tools = registry.tool_names_for_stage("implement")
    test_tools = registry.tool_names_for_stage("test")
    debug_tools = registry.tool_names_for_stage("debug")

    assert {"scan_project", "read_file", "search_code"}.issubset(implement_tools)
    assert "apply_patch" in implement_tools
    assert "run_shell" not in implement_tools
    assert {"run_shell", "parse_test_result"}.issubset(test_tools)
    assert "apply_patch" not in debug_tools
    assert registry.get_side_effect_tool_names() == {"apply_patch", "run_shell"}


def test_default_registry_rejects_unknown_stage() -> None:
    registry = create_default_tool_registry()

    with pytest.raises(ValueError, match="unknown stage"):
        registry.tool_names_for_stage("deploy")


def test_hitl_creates_request_for_side_effect_without_decision() -> None:
    interceptor = ToolHITLInterceptor(policy=ToolPermissionPolicy())
    result = interceptor.intercept(
        ToolCall(
            name="apply_patch",
            args={"patch_path": "change.diff"},
            operation_id="patch-1",
        ),
        ToolCallContext(stage="repair", mode="run"),
    )

    assert result.execute is False
    assert result.request is not None
    assert result.request.action == "review_tool_call"
    assert result.request.default_decision == "reject"
    assert "approve" in result.request.allowed_decisions


def test_hitl_reject_records_decision_and_blocks_execution(tmp_path: Path) -> None:
    trace_path = tmp_path / "decision_trace.jsonl"
    interceptor = ToolHITLInterceptor(policy=ToolPermissionPolicy())

    result = interceptor.intercept(
        ToolCall(name="run_shell", args={"command": "pytest -q"}, operation_id="cmd-1"),
        ToolCallContext(stage="test", mode="run"),
        decision=ApprovalDecision(
            interrupt_id="cmd-1",
            decision_type="reject",
            comment="not now",
        ),
        decision_recorder=JsonlRecorder(trace_path),
    )

    events = _read_jsonl(trace_path)
    assert result.execute is False
    assert "rejected" in result.message
    assert events[0]["type"] == "human_decision"
    assert events[0]["decision_type"] == "reject"
    assert events[0]["tool_name"] == "run_shell"


def test_hitl_respond_records_decision_and_returns_feedback(tmp_path: Path) -> None:
    trace_path = tmp_path / "decision_trace.jsonl"
    interceptor = ToolHITLInterceptor(policy=ToolPermissionPolicy())

    result = interceptor.intercept(
        ToolCall(name="run_shell", args={"command": "pytest -q"}, operation_id="cmd-r"),
        ToolCallContext(stage="test", mode="run"),
        decision=ApprovalDecision(
            interrupt_id="cmd-r",
            decision_type="respond",
            comment="explain the command first",
        ),
        decision_recorder=JsonlRecorder(trace_path),
    )

    events = _read_jsonl(trace_path)
    assert result.execute is False
    assert result.message == "explain the command first"
    assert events[0]["decision_type"] == "respond"
    assert events[0]["comment"] == "explain the command first"


def test_hitl_edit_records_decision_and_returns_edited_args(tmp_path: Path) -> None:
    trace_path = tmp_path / "decision_trace.jsonl"
    interceptor = ToolHITLInterceptor(policy=ToolPermissionPolicy())

    result = interceptor.intercept(
        ToolCall(name="run_shell", args={"command": "pytest"}, operation_id="cmd-2"),
        ToolCallContext(stage="test", mode="run"),
        decision=ApprovalDecision(
            interrupt_id="cmd-2",
            decision_type="edit",
            edited_payload={"command": "pytest -q"},
            comment="make output concise",
        ),
        decision_recorder=JsonlRecorder(trace_path),
    )

    events = _read_jsonl(trace_path)
    assert result.execute is True
    assert result.args == {"command": "pytest -q"}
    assert events[0]["decision_type"] == "edit"
    assert events[0]["payload_summary"] == "run_shell"


def test_hitl_edit_accepts_empty_payload(tmp_path: Path) -> None:
    trace_path = tmp_path / "decision_trace.jsonl"
    interceptor = ToolHITLInterceptor(policy=ToolPermissionPolicy())

    result = interceptor.intercept(
        ToolCall(name="run_shell", args={"command": "pytest"}, operation_id="cmd-empty"),
        ToolCallContext(stage="test", mode="run"),
        decision=ApprovalDecision(
            interrupt_id="cmd-empty",
            decision_type="edit",
            edited_payload={},
        ),
        decision_recorder=JsonlRecorder(trace_path),
    )

    assert result.execute is True
    assert result.args == {}


def test_hitl_records_benchmark_auto_approval(tmp_path: Path) -> None:
    trace_path = tmp_path / "decision_trace.jsonl"
    interceptor = ToolHITLInterceptor(policy=ToolPermissionPolicy())

    result = interceptor.intercept(
        ToolCall(name="run_shell", args={"command": "pytest -q"}, operation_id="cmd-3"),
        ToolCallContext(
            stage="test",
            mode="benchmark",
            auto_approve_in_benchmark=True,
        ),
        decision_recorder=JsonlRecorder(trace_path),
    )

    events = _read_jsonl(trace_path)
    assert result.execute is True
    assert result.permission.auto_approved is True
    assert events[0]["decision_type"] == "approve"
    assert events[0]["auto"] is True
    assert events[0]["reason"] == "benchmark mode auto approval enabled"
