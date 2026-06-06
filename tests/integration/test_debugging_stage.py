from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from langgraph.types import Command

from codeagent.config.schema import Stage, TaskConfig
from codeagent.reports.artifact_store import ArtifactStore
from codeagent.reports.schemas import StageResult
from codeagent.runtime.run_context import create_run_context
from codeagent.stages.debugging_service import (
    DebuggingAnalysis,
    DebuggingRequest,
    DebuggingService,
    FaultCandidate,
)
from codeagent.tools.hitl import ApprovalDecision
from codeagent.workflow.checkpoint import CheckpointManager
from codeagent.workflow.factory import WorkflowFactory
from codeagent.workflow.state import AgentState, create_initial_state
from codeagent.workflow.subgraphs.debugging import (
    build_debugging_subgraph,
    build_interrupting_debugging_subgraph,
    create_debugging_stage_handler,
)


def _run_context(tmp_path: Path, project_root: Path):
    task_config = TaskConfig(
        stages=["debug"],
        project_path=project_root,
        output_dir=tmp_path / "runs",
        test_command={"command": "python -m pytest -q", "timeout_seconds": 30},
    )
    return create_run_context(task_config, output_root=tmp_path / "runs")


def _service(tmp_path: Path, project_root: Path) -> tuple[DebuggingService, object]:
    run_context = _run_context(tmp_path, project_root)
    return DebuggingService(run_context=run_context), run_context


class _FakeDebuggingAnalysisProvider:
    def __init__(self, analysis: DebuggingAnalysis) -> None:
        self.analysis = analysis
        self.failure_summary = ""
        self.static_localization: object | None = None

    def create_debugging_analysis(
        self,
        context,
        *,
        failure_summary: str,
        static_localization,
        feedback: str | None = None,
    ) -> DebuggingAnalysis:
        self.failure_summary = failure_summary
        self.static_localization = static_localization
        return self.analysis


def _decision(kind: str = "approve") -> ApprovalDecision:
    return ApprovalDecision(
        interrupt_id="debugging_reproduction_command",
        decision_type=kind,  # type: ignore[arg-type]
        comment=f"{kind} reproduction command.",
        auto=True,
    )


def _long_readable_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    return Path("\\\\?\\" + str(path.resolve()))


def _write_buggy_project(project_root: Path) -> None:
    project_root.mkdir()
    (project_root / "math_utils.py").write_text(
        "def add(left, right):\n"
        "    return left - right\n",
        encoding="utf-8",
    )
    tests_dir = project_root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_math_utils.py").write_text(
        "from math_utils import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )


def _request(
    *,
    command: str | None = "python -m pytest -q",
    logs: list[Path] | None = None,
    decision: ApprovalDecision | None = None,
) -> DebuggingRequest:
    return DebuggingRequest(
        test_command=command,
        command_approval=decision or _decision(),
        failure_logs=logs or [],
        expected_behavior="add(left, right) should return the sum.",
        framework="pytest",
    )


def test_fault_candidate_schema_requires_evidence() -> None:
    candidate = FaultCandidate(
        file_path=Path("math_utils.py"),
        function_name="add",
        line_number=2,
        confidence="high",
        evidence=["traceback mentions math_utils.py"],
        rationale="The failing assertion reaches add().",
    )

    assert candidate.file_path == Path("math_utils.py")
    assert candidate.confidence == "high"


def test_debugging_service_reproduces_failure_and_writes_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    progress_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        "codeagent.stages.debugging_service.emit_progress",
        lambda event_type, **payload: progress_events.append(
            {"type": event_type, **payload}
        ),
    )

    result = service.run(_request())

    stage_dir = run_context.run_dir / "debugging"
    fault = json.loads((stage_dir / "fault_localization.json").read_text(encoding="utf-8"))
    artifact_store = ArtifactStore.load(run_context.run_dir)

    assert result.status == "succeeded"
    assert fault["confidence"] in {"high", "medium"}
    assert any("test_add" in name for name in fault["failing_tests"])
    assert fault["candidates"][0]["file_path"] == "math_utils.py"
    assert fault["candidates"][0]["evidence"]
    for filename in [
        "reproduction_report.md",
        "before_test.log",
        "failure_summary.md",
        "fault_localization.json",
        "root_cause.md",
        "repair_plan.md",
        "debug_trace.jsonl",
        "debug_report.md",
        "stage_result.json",
        "stage_report.md",
    ]:
        assert (stage_dir / filename).exists()
    assert artifact_store.find("debugging_fault_localization") is not None
    assert "math_utils.py" in (stage_dir / "root_cause.md").read_text(encoding="utf-8")
    assert "math_utils.py" in (stage_dir / "repair_plan.md").read_text(encoding="utf-8")
    assert any(
        event["type"] == "agent_status" and "第 1 次调试" in str(event["message"])
        for event in progress_events
    )
    assert any(
        event["type"] == "tool_started" and event["tool_name"] == "run_shell"
        for event in progress_events
    )
    assert any(
        event["type"] == "agent_status" and "调试完成" in str(event["message"])
        for event in progress_events
    )
    workflow_log = (run_context.run_dir / "workflow.log").read_text(encoding="utf-8")
    assert "debugging_attempt_started" in workflow_log
    assert "debugging_attempt_finished" in workflow_log


def test_debugging_service_writes_llm_analysis_and_merges_candidates(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    analysis = DebuggingAnalysis(
        failure_origin="generated_test_code",
        confidence="high",
        candidates=[
            {
                "path": "tests/test_math_utils.py",
                "kind": "test_code",
                "line_number": 4,
                "confidence": "high",
                "evidence": ["pytest traceback points to an invalid generated test"],
                "rationale": "The visible generated test contains the bad fixture.",
            }
        ],
        evidence=["The failure occurs before product code is exercised."],
        root_cause="生成测试自身脚手架错误导致失败。",
        repair_strategy="修复 tests/test_math_utils.py 中的可见测试脚手架。",
        test_repair_allowed=True,
        test_repair_rationale="错误位于可见生成测试代码。",
        recommended_verification_command="python -m pytest -q",
        framework="pytest",
    )
    provider = _FakeDebuggingAnalysisProvider(analysis)
    run_context = _run_context(tmp_path, project_root)
    service = DebuggingService(run_context=run_context, analysis_provider=provider)

    result = service.run(_request())

    stage_dir = run_context.run_dir / "debugging"
    fault = json.loads((stage_dir / "fault_localization.json").read_text(encoding="utf-8"))
    llm_payload = json.loads(
        (stage_dir / "llm_debug_analysis.json").read_text(encoding="utf-8")
    )
    report = (stage_dir / "debug_report.md").read_text(encoding="utf-8")

    assert result.status == "succeeded"
    assert fault["failure_origin"] == "generated_test_code"
    assert fault["test_repair_allowed"] is True
    assert fault["llm_analysis_available"] is True
    assert any(
        candidate["file_path"] == "tests/test_math_utils.py"
        for candidate in fault["candidates"]
    )
    assert llm_payload["test_repair_allowed"] is True
    assert "生成测试自身脚手架错误" in report
    assert provider.static_localization is not None


def test_debugging_service_writes_artifacts_under_long_windows_paths(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    run_context = _run_context(tmp_path, project_root)
    long_dir = run_context.run_dir / "debugging_long"
    while len(str(long_dir / "debug_report.md")) < 285:
        long_dir = long_dir / "deep_segment_for_windows_path_limit"
    run_context.stage_dirs[Stage.DEBUG] = long_dir
    service = DebuggingService(run_context=run_context)

    result = service.run(_request())

    readable_stage_dir = _long_readable_path(long_dir)
    assert result.status == "succeeded"
    assert (readable_stage_dir / "reproduction_report.md").exists()
    assert (readable_stage_dir / "fault_localization.json").exists()
    assert (readable_stage_dir / "debug_report.md").exists()
    assert (readable_stage_dir / "stage_result.json").exists()


def test_debugging_service_rejected_reproduction_uses_static_logs(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    failure_log = project_root / "failure.log"
    failure_log.write_text(
        "FAILED tests/test_math_utils.py::test_add - AssertionError\n"
        "Traceback (most recent call last):\n"
        '  File "math_utils.py", line 2, in add\n'
        "AssertionError: expected 5\n",
        encoding="utf-8",
    )
    service, run_context = _service(tmp_path, project_root)

    result = service.run(
        _request(logs=[failure_log], decision=_decision("reject"))
    )

    stage_dir = run_context.run_dir / "debugging"
    reproduction_report = (stage_dir / "reproduction_report.md").read_text(
        encoding="utf-8"
    )
    fault = json.loads((stage_dir / "fault_localization.json").read_text(encoding="utf-8"))

    assert result.status == "succeeded"
    assert not (stage_dir / "before_test.log").exists()
    assert "not executed" in reproduction_report
    assert fault["candidates"][0]["file_path"] == "math_utils.py"


def test_debugging_service_static_fallback_reports_low_confidence(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    vague_log = project_root / "vague.log"
    vague_log.write_text("A calculation failed, but no traceback was captured.\n", encoding="utf-8")
    service, run_context = _service(tmp_path, project_root)

    result = service.run(_request(command=None, logs=[vague_log]))

    fault = json.loads(
        (run_context.run_dir / "debugging" / "fault_localization.json").read_text(
            encoding="utf-8"
        )
    )

    assert result.status == "succeeded"
    assert fault["confidence"] == "low"
    assert fault["candidates"] == []
    assert "low confidence" in result.summary.lower()


def test_debugging_service_reports_generated_test_harness_cwd_failure(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    harness_log = project_root / "harness_failure.log"
    test_file = project_root / "tests" / "test_cli.py"
    harness_log.write_text(
        "FAILED tests/test_cli.py::test_add_via_cli - NotADirectoryError\n"
        "Traceback (most recent call last):\n"
        f'  File "{test_file}", line 18, in run\n'
        "    return subprocess.run(cmd, cwd=str(PROJECT_ROOT))\n"
        '  File "D:\\Python\\Python313\\Lib\\subprocess.py", line 1550, in _execute_child\n'
        "NotADirectoryError: [WinError 267] 目录名称无效: "
        f"'{project_root / 'project'}'\n",
        encoding="utf-8",
    )
    service, run_context = _service(tmp_path, project_root)

    result = service.run(_request(command=None, logs=[harness_log]))

    stage_dir = run_context.run_dir / "debugging"
    fault = json.loads((stage_dir / "fault_localization.json").read_text(encoding="utf-8"))
    workflow_log = (run_context.run_dir / "workflow.log").read_text(encoding="utf-8")

    assert result.status == "succeeded"
    assert result.error is None
    assert fault["failure_origin"] == "test_harness"
    assert fault["test_repair_allowed"] is True
    assert "Repair the visible generated" in (stage_dir / "repair_plan.md").read_text(
        encoding="utf-8"
    )
    assert fault["candidates"] == []
    assert "debugging_test_harness_failure" in workflow_log


def test_debugging_service_rejects_hidden_benchmark_command_path(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, _run_context = _service(tmp_path, project_root)

    result = service.run(
        _request(
            command="python -m pytest --rootdir=benchmark/cases/demo/evaluation",
            decision=_decision("approve"),
        )
    )

    assert result.status == "failed"
    assert result.error is not None
    assert "hidden benchmark path" in result.error.message


def test_debugging_service_rejects_bare_hidden_benchmark_command_path(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, _run_context = _service(tmp_path, project_root)

    for command in [
        "python -m pytest evaluation -q",
        "python -m pytest oracle_tests -q",
    ]:
        result = service.run(_request(command=command, decision=_decision("approve")))

        assert result.status == "failed"
        assert result.error is not None
        assert "hidden benchmark path" in result.error.message


def test_debugging_service_rejects_hidden_benchmark_failure_log(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    hidden_dir = tmp_path / "benchmark" / "selfbuilt" / "cases" / "demo" / "oracle_tests"
    hidden_dir.mkdir(parents=True)
    hidden_log = hidden_dir / "failure.log"
    hidden_log.write_text("hidden oracle failure\n", encoding="utf-8")
    service, run_context = _service(tmp_path, project_root)

    result = service.run(_request(command=None, logs=[hidden_log]))

    assert result.status == "failed"
    assert result.error is not None
    assert "failure log path is denied" in result.error.message
    assert not (run_context.run_dir / "debugging" / "failure_summary.md").exists()


def test_debugging_service_rejects_failure_log_outside_allowed_roots(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_log = outside_dir / "failure.log"
    outside_log.write_text("FAILED tests/test_math_utils.py::test_add\n", encoding="utf-8")
    service, run_context = _service(tmp_path, project_root)

    result = service.run(_request(command=None, logs=[outside_log]))

    assert result.status == "failed"
    assert result.error is not None
    assert "failure log path is denied" in result.error.message
    assert not (run_context.run_dir / "debugging" / "failure_summary.md").exists()


def test_debugging_service_rejects_secret_parent_failure_log(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    secret_dir = project_root / "secret_logs"
    secret_dir.mkdir()
    secret_log = secret_dir / "failure.log"
    secret_log.write_text("FAILED tests/test_math_utils.py::test_add\n", encoding="utf-8")
    service, run_context = _service(tmp_path, project_root)

    result = service.run(_request(command=None, logs=[secret_log]))

    assert result.status == "failed"
    assert result.error is not None
    assert "failure log path is denied" in result.error.message
    assert not (run_context.run_dir / "debugging" / "failure_summary.md").exists()


def test_debugging_subgraph_handler_routes_to_repair(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    handler = create_debugging_stage_handler(
        service=service,
        request_builder=lambda _state: _request(),
    )

    def repair_handler(state: AgentState) -> dict[str, Any]:
        stage_results = dict(state.get("stage_results", {}))
        stage_results["repair"] = StageResult(
            stage="repair",
            status="succeeded",
            started_at="2026-06-03T00:00:00+00:00",
            ended_at="2026-06-03T00:00:01+00:00",
            summary="repair visited",
        ).model_dump(mode="json", exclude_none=True)
        return {"stage_results": stage_results}

    graph = WorkflowFactory(
        stage_handlers={"debugging": handler, "repair": repair_handler}
    ).build()
    result_state = graph.invoke(
        create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["debug", "repair"],
        )
    )

    assert result_state["stage_results"]["debugging"]["status"] == "succeeded"
    assert result_state["stage_results"]["repair"]["status"] == "succeeded"
    assert result_state["final_status"] == "succeeded"


def test_debugging_subgraph_can_run_as_standalone_subgraph(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    handler = create_debugging_stage_handler(
        service=service,
        request_builder=lambda _state: _request(),
    )
    subgraph = build_debugging_subgraph(handler)

    result_state = subgraph.invoke(
        create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["debug"],
        )
    )

    assert result_state["stage_results"]["debugging"]["status"] == "succeeded"
    assert "debugging_fault_localization" in result_state["artifact_refs"]


def test_interrupting_debugging_subgraph_rejects_reproduction_command(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    failure_log = project_root / "failure.log"
    failure_log.write_text(
        "FAILED tests/test_math_utils.py::test_add - AssertionError\n"
        '  File "math_utils.py", line 2, in add\n',
        encoding="utf-8",
    )
    service, run_context = _service(tmp_path, project_root)
    request = _request(logs=[failure_log])
    manager = CheckpointManager(run_context.run_dir, run_id=run_context.run_id)

    with manager.create_sqlite_saver() as saver:
        subgraph = build_interrupting_debugging_subgraph(
            service=service,
            request_builder=lambda _state: request,
            checkpointer=saver,
        )
        initial = create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["debug"],
        )
        first = subgraph.invoke(initial, config=manager.get_thread_config())
        payload = first["__interrupt__"][0].value
        final = subgraph.invoke(
            Command(resume={"decision_type": "reject", "auto": True}),
            config=manager.get_thread_config(),
        )

    assert payload["action"] == "approve_reproduction_command"
    assert final["stage_results"]["debugging"]["status"] == "succeeded"
    assert not (run_context.run_dir / "debugging" / "before_test.log").exists()
