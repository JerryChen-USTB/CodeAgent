from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from codeagent.cli.plugin_bridge import (
    JsonlBridgeChannel,
    PluginApprovalConsole,
    PluginProgressReporter,
)
from codeagent.tools.hitl import ApprovalRequest


def test_plugin_progress_reporter_emits_run_started_and_workflow_events(tmp_path: Path) -> None:
    output = StringIO()
    channel = JsonlBridgeChannel(input_stream=StringIO(), output_stream=output)
    reporter = PluginProgressReporter(channel)
    context = SimpleNamespace(
        run_id="run-1",
        run_dir=tmp_path / "runs" / "run-1",
        task_config=SimpleNamespace(
            stages=[SimpleNamespace(value="implement"), SimpleNamespace(value="test")],
            project_path=tmp_path / "project",
            output_dir=tmp_path / "runs",
            test_command=SimpleNamespace(command="pytest -q", timeout_seconds=120),
            permissions=SimpleNamespace(approval_mode="manual"),
            model=SimpleNamespace(
                provider="openai_compatible",
                model_name="google/gemini-3.5-flash",
                base_url="https://openrouter.ai/api/v1",
                api_key_env="OPENROUTER_API_KEY",
            ),
            input_materials=[],
        ),
    )

    reporter.bind_run_context(context)
    line = reporter.render_event(
        {"type": "agent_status", "stage": "implementation", "message": "正在生成实现计划"}
    )

    messages = [json.loads(raw) for raw in output.getvalue().splitlines()]
    assert messages[0]["type"] == "run_started"
    assert messages[0]["run_id"] == "run-1"
    assert messages[0]["task_config"]["stages"] == ["implement", "test"]
    assert messages[1]["type"] == "workflow_event"
    assert messages[1]["event"]["stage"] == "implementation"
    assert "正在生成实现计划" in line


def test_plugin_approval_console_emits_request_and_reads_respond_decision(tmp_path: Path) -> None:
    output = StringIO()
    input_stream = StringIO(
        json.dumps(
            {
                "interrupt_id": "implementation_plan",
                "decision_type": "respond",
                "comment": "请缩小修改范围。",
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    channel = JsonlBridgeChannel(input_stream=input_stream, output_stream=output)
    console = PluginApprovalConsole(channel)
    request = ApprovalRequest(
        interrupt_id="implementation_plan",
        action="review_implementation_plan",
        title="实施此实现计划？",
        payload={"plan_path": "implementation/implementation_plan.md"},
        risk_level="medium",
        allowed_decisions=("approve", "respond"),
        default_decision="approve",
    )
    console.set_approval_context(
        request,
        [
            SimpleNamespace(
                display="implementation_plan.md (implementation/implementation_plan.md)",
                absolute_path=tmp_path / "run" / "implementation" / "implementation_plan.md",
            )
        ],
        None,
        "当前动作：补丁尚未生成；同意后开始生成补丁草案。",
    )

    decision = console.prompt(request)

    messages = [json.loads(raw) for raw in output.getvalue().splitlines()]
    assert messages[0]["type"] == "approval_requested"
    assert messages[0]["request"]["interrupt_id"] == "implementation_plan"
    assert messages[0]["choices"] == [
        {"value": "approve", "decision_type": "approve", "label": "是，实施此计划"},
        {"value": "respond", "decision_type": "respond", "label": "否，告知 CodeAgent 如何调整"},
    ]
    assert messages[0]["context"]["files"][0]["path"].endswith(
        "implementation/implementation_plan.md"
    )
    assert decision.decision_type == "respond"
    assert decision.comment == "请缩小修改范围。"
    assert decision.decision_source == "vscode"


def test_plugin_approval_console_reports_invalid_decision_then_accepts_valid() -> None:
    output = StringIO()
    input_stream = StringIO(
        '{"decision_type":"respond","comment":""}\n'
        '{"decision_type":"approve"}\n'
    )
    channel = JsonlBridgeChannel(input_stream=input_stream, output_stream=output)
    console = PluginApprovalConsole(channel)
    request = ApprovalRequest(
        interrupt_id="test_patch",
        action="approve_test_patch",
        title="应用此测试补丁？",
        payload={"patch_path": "testing/test.patch.diff"},
        risk_level="low",
        allowed_decisions=("approve", "respond"),
        default_decision="approve",
    )

    decision = console.prompt(request)

    messages = [json.loads(raw) for raw in output.getvalue().splitlines()]
    assert [message["type"] for message in messages] == [
        "approval_requested",
        "error",
    ]
    assert messages[1]["code"] == "invalid_approval_decision"
    assert decision.decision_type == "approve"
