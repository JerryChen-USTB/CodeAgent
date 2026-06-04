from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from langgraph.types import Command
from pydantic import ValidationError
import pytest

from codeagent.config.schema import Stage, TaskConfig
from codeagent.reports.artifact_store import ArtifactStore
from codeagent.reports.schemas import StageResult
from codeagent.runtime.run_context import create_run_context
from codeagent.stages.repair_service import (
    RepairFileChange,
    RepairPlan,
    RepairPatchDraft,
    RepairPatchFileChange,
    RepairRequest,
    RepairService,
)
from codeagent.tools.hitl import ApprovalDecision
from codeagent.tools.risk_checker import RepairRiskChecker
from codeagent.workflow.checkpoint import CheckpointManager
from codeagent.workflow.factory import WorkflowFactory
from codeagent.workflow.state import AgentState, create_initial_state
from codeagent.workflow.subgraphs.repair import (
    build_interrupting_repair_subgraph,
    build_repair_subgraph,
    create_repair_stage_handler,
)


def _run_context(tmp_path: Path, project_root: Path):
    task_config = TaskConfig(
        stages=["repair"],
        project_path=project_root,
        output_dir=tmp_path / "runs",
        test_command={"command": "python -m pytest -q", "timeout_seconds": 30},
    )
    return create_run_context(task_config, output_root=tmp_path / "runs")


def _service(tmp_path: Path, project_root: Path) -> tuple[RepairService, object]:
    run_context = _run_context(tmp_path, project_root)
    return RepairService(run_context=run_context), run_context


def _decision(kind: str = "approve", *, action_id: str = "repair_patch") -> ApprovalDecision:
    return ApprovalDecision(
        interrupt_id=action_id,
        decision_type=kind,  # type: ignore[arg-type]
        comment=f"{kind} for repair fixture.",
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


_PLAN_FIXTURES: dict[int, tuple[str, str, str, str]] = {}


def _plan(new_content: str, *, path: str = "math_utils.py") -> RepairPlan:
    plan = RepairPlan(
        root_cause="The add helper subtracts instead of adding.",
        strategy="Replace subtraction with addition in the implementation.",
        changes=[
            RepairFileChange(
                path=Path(path),
                rationale="Minimal implementation repair.",
                expected_effect="The public add helper returns the arithmetic sum.",
            )
        ],
        verification_command="python -m pytest -q",
        framework="pytest",
    )
    _PLAN_FIXTURES[id(plan)] = (path, new_content, plan.verification_command, plan.framework)
    return plan


def _with_verification_command(plan: RepairPlan, command: str) -> RepairPlan:
    updated = plan.model_copy(update={"verification_command": command})
    path, content, _old_command, framework = _PLAN_FIXTURES[id(plan)]
    _PLAN_FIXTURES[id(updated)] = (path, content, command, framework)
    return updated


def _draft_from_plan(plan: RepairPlan) -> RepairPatchDraft:
    path, content, command, framework = _PLAN_FIXTURES.get(
        id(plan),
        (
            plan.changes[0].path.as_posix(),
            "def add(left, right):\n    return left + right\n",
            plan.verification_command,
            plan.framework,
        ),
    )
    return RepairPatchDraft(
        plan_summary="Concrete repair patch for the approved repair plan.",
        changes=[
            RepairPatchFileChange(
                path=path,
                old_content=None,
                new_content=content,
                rationale="Minimal implementation repair.",
            )
        ],
        verification_command=command,
        framework=framework,  # type: ignore[arg-type]
    )


def _good_plan() -> RepairPlan:
    return _plan("def add(left, right):\n    return left + right\n")


def _bad_plan() -> RepairPlan:
    return _plan("def add(left, right):\n    return left * right\n")


def _request(plan: RepairPlan, **overrides: ApprovalDecision) -> RepairRequest:
    return RepairRequest(
        plan=plan,
        patch_draft=_draft_from_plan(plan),
        plan_review=overrides.get("plan_review", _decision("approve", action_id="repair_plan")),
        patch_approval=overrides.get("patch_approval", _decision("approve")),
        command_approval=overrides.get(
            "command_approval",
            _decision("approve", action_id="repair_regression_command"),
        ),
    )


def test_repair_plan_schema_rejects_empty_changes() -> None:
    try:
        RepairPlan(
            root_cause="broken",
            strategy="do nothing",
            changes=[],
            verification_command="python -m pytest -q",
            framework="pytest",
        )
    except ValidationError:
        return
    raise AssertionError("empty repair changes should be rejected")


def test_repair_risk_checker_rejects_test_skip_patch(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    risky_test = (
        "import pytest\n"
        "from math_utils import add\n\n"
        "@pytest.mark.skip(reason='hide failing case')\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
    )

    result = service.run(_request(_plan(risky_test, path="tests/test_math_utils.py")))

    assert result.status == "failed"
    assert result.error is not None
    assert "risky" in result.summary.lower()
    assert "skip" in result.error.message.lower()
    assert "pytest.mark.skip" not in (project_root / "tests" / "test_math_utils.py").read_text(
        encoding="utf-8"
    )
    assert (run_context.run_dir / "repair" / "repair_risk.json").exists()


@pytest.mark.parametrize(
    "target_path",
    [
        ".env",
        "evaluation/hidden.py",
        "oracle_tests/test_hidden.py",
        "expected_result.json",
    ],
)
def test_repair_service_rejects_sensitive_or_hidden_targets_before_diff_artifacts(
    tmp_path,
    target_path: str,
) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    if "/" in target_path:
        (project_root / target_path).parent.mkdir(parents=True)
    if target_path != ".env":
        (project_root / target_path).write_text("hidden = True\n", encoding="utf-8")
    service, run_context = _service(tmp_path, project_root)

    result = service.run(
        _request(
            _plan(
                "hidden = False\n",
                path=target_path,
            )
        )
    )

    stage_dir = run_context.run_dir / "repair"
    assert result.status == "failed"
    assert result.error is not None
    assert (
        "hidden benchmark" in result.error.message
        or "sensitive or generated" in result.error.message
    )
    assert list(stage_dir.glob("repair_patch_attempt_*.diff")) == []
    assert not (stage_dir / "repair.patch.diff").exists()


def test_repair_risk_checker_rejects_test_infrastructure_patch(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    (project_root / "conftest.py").write_text(
        "def pytest_configure(config):\n"
        "    pass\n",
        encoding="utf-8",
    )
    service, run_context = _service(tmp_path, project_root)

    result = service.run(
        _request(
            _plan(
                "def pytest_configure(config):\n"
                "    config.option.keyword = 'not test_add'\n",
                path="conftest.py",
            )
        )
    )

    risk = json.loads((run_context.run_dir / "repair" / "repair_risk.json").read_text(encoding="utf-8"))
    assert result.status == "failed"
    assert result.error is not None
    assert "test infrastructure" in result.error.message
    assert risk["level"] == "high"


def test_repair_service_applies_patch_runs_regression_and_writes_report(
    tmp_path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    progress_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        "codeagent.stages.repair_service.emit_progress",
        lambda event_type, **payload: progress_events.append(
            {"type": event_type, **payload}
        ),
    )

    result = service.run(_request(_good_plan()))

    stage_dir = run_context.run_dir / "repair"
    test_result = json.loads((stage_dir / "repair_test_result.json").read_text(encoding="utf-8"))
    changed_files = json.loads((stage_dir / "changed_files.json").read_text(encoding="utf-8"))
    artifact_store = ArtifactStore.load(run_context.run_dir)

    assert result.status == "succeeded"
    assert "return left + right" in (project_root / "math_utils.py").read_text(
        encoding="utf-8"
    )
    assert test_result["success"] is True
    assert changed_files["changed_files"] == ["math_utils.py"]
    for filename in [
        "repair_plan.md",
        "repair_plan.json",
        "repair_patch_draft.json",
        "repair.patch.diff",
        "repair_risk.json",
        "changed_files.json",
        "after_test.log",
        "repair_test_result.json",
        "repair_report.md",
        "stage_result.json",
        "stage_report.md",
    ]:
        assert (stage_dir / filename).exists()
    assert artifact_store.find("repair_patch") is not None
    assert artifact_store.find("repair_report") is not None
    assert any(event["type"] == "tool_started" for event in progress_events)
    assert any(
        event["type"] == "test_result" and event["total"] == 1
        for event in progress_events
    )
    assert any(
        event["type"] == "agent_status" and "修复验证通过" in str(event["message"])
        for event in progress_events
    )


def test_repair_service_writes_artifacts_under_long_windows_paths(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    run_context = _run_context(tmp_path, project_root)
    long_dir = run_context.run_dir / "repair_long"
    while len(str(long_dir / "repair_report.md")) < 285:
        long_dir = long_dir / "deep_segment_for_windows_path_limit"
    run_context.stage_dirs[Stage.REPAIR] = long_dir
    service = RepairService(run_context=run_context)

    result = service.run(_request(_good_plan()))

    readable_stage_dir = _long_readable_path(long_dir)
    assert result.status == "succeeded"
    assert (readable_stage_dir / "repair_plan.md").exists()
    assert (readable_stage_dir / "repair.patch.diff").exists()
    assert (readable_stage_dir / "repair_report.md").exists()
    assert (readable_stage_dir / "stage_result.json").exists()


def test_repair_service_rejected_regression_command_fails_without_running(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)

    result = service.run(
        _request(
            _good_plan(),
            command_approval=_decision("reject", action_id="repair_regression_command"),
        )
    )

    assert result.status == "failed"
    assert result.error is not None
    assert "approval decision: reject" in result.error.message
    assert "return left + right" in (project_root / "math_utils.py").read_text(
        encoding="utf-8"
    )
    assert not (run_context.run_dir / "repair" / "after_test.log").exists()


def test_repair_service_rejects_hidden_benchmark_regression_command(tmp_path) -> None:
    hidden_commands = [
        "python -m pytest evaluation -q",
        "python -m pytest --rootdir=benchmark/cases/demo/oracle_tests",
    ]

    for index, command in enumerate(hidden_commands):
        project_root = tmp_path / f"project_{index}"
        _write_buggy_project(project_root)
        service, _run_context = _service(tmp_path / f"run_{index}", project_root)
        plan = _with_verification_command(_good_plan(), command)
        result = service.run(_request(plan))

        assert result.status == "failed"
        assert result.error is not None
        assert "hidden benchmark path" in result.error.message


def test_repair_service_failed_regression_returns_failed_stage(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)

    result = service.run(_request(_bad_plan()))

    failure_report = (run_context.run_dir / "repair" / "repair_report.md").read_text(
        encoding="utf-8"
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.category == "pytest_failure"
    assert "Testing failed" in failure_report


def test_repair_subgraph_handler_and_main_graph_retry_until_max_attempts(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    repair_handler = create_repair_stage_handler(
        service=service,
        request_builder=lambda _state: _request(_bad_plan()),
    )

    def debug_handler(state: AgentState) -> dict[str, Any]:
        stage_results = dict(state.get("stage_results", {}))
        stage_results["debugging"] = StageResult(
            stage="debugging",
            status="succeeded",
            started_at="2026-06-03T00:00:00+00:00",
            ended_at="2026-06-03T00:00:01+00:00",
            summary="debug visited",
        ).model_dump(mode="json", exclude_none=True)
        return {"stage_results": stage_results}

    graph = WorkflowFactory(
        stage_handlers={"debugging": debug_handler, "repair": repair_handler}
    ).build()
    state = create_initial_state(
        run_id=run_context.run_id,
        mode="run",
        selected_stages=["debug", "repair"],
    )
    state["max_repair_attempts"] = 2
    result_state = graph.invoke(state)

    assert result_state["repair_attempt"] == 2
    assert result_state["stage_results"]["repair"]["status"] == "failed"
    assert result_state["final_status"] == "failed"


def test_repair_subgraph_can_run_as_standalone_subgraph(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    handler = create_repair_stage_handler(
        service=service,
        request_builder=lambda _state: _request(_good_plan()),
    )
    subgraph = build_repair_subgraph(handler)

    result_state = subgraph.invoke(
        create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["repair"],
        )
    )

    assert result_state["stage_results"]["repair"]["status"] == "succeeded"
    assert "repair_patch" in result_state["artifact_refs"]


def test_interrupting_repair_subgraph_approves_patch_and_command(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    request = _request(_good_plan())
    manager = CheckpointManager(run_context.run_dir, run_id=run_context.run_id)

    with manager.create_sqlite_saver() as saver:
        subgraph = build_interrupting_repair_subgraph(
            service=service,
            request_builder=lambda _state: request,
            checkpointer=saver,
        )
        state = create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["repair"],
        )
        first = subgraph.invoke(state, config=manager.get_thread_config())
        plan_payload = first["__interrupt__"][0].value
        second = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )
        patch_payload = second["__interrupt__"][0].value
        command_review = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )
        command_payload = command_review["__interrupt__"][0].value
        final = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )

    assert plan_payload["action"] == "review_repair_plan"
    assert plan_payload["title"] == "实施此修复计划？"
    assert plan_payload["allowed_decisions"] == ["approve", "respond"]
    assert patch_payload["action"] == "approve_repair_patch"
    assert patch_payload["title"] == "应用此修复补丁？"
    assert patch_payload["allowed_decisions"] == ["approve", "respond"]
    assert patch_payload["default_decision"] == "approve"
    assert patch_payload["payload"]["changed_files"] == ["math_utils.py"]
    assert "patch_sha256" in patch_payload["payload"]
    assert command_payload["action"] == "approve_regression_command"
    assert command_payload["title"] == "运行此回归验证命令？"
    assert final["stage_results"]["repair"]["status"] == "succeeded"


def test_interrupting_repair_subgraph_rejects_tampered_approved_patch(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    request = _request(_good_plan())
    manager = CheckpointManager(run_context.run_dir, run_id=run_context.run_id)

    with manager.create_sqlite_saver() as saver:
        subgraph = build_interrupting_repair_subgraph(
            service=service,
            request_builder=lambda _state: request,
            checkpointer=saver,
        )
        state = create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["repair"],
        )
        subgraph.invoke(state, config=manager.get_thread_config())
        subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )
        patch_path = run_context.run_dir / "repair" / "repair.patch.diff"
        patch_path.write_text(
            patch_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        final = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )

    assert final["stage_results"]["repair"]["status"] == "failed"
    assert "hash mismatch" in final["stage_results"]["repair"]["error"]["message"]


def test_interrupting_repair_subgraph_accepts_edited_patch_plan(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_buggy_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    edited_draft = _draft_from_plan(_good_plan())
    request = _request(_bad_plan())
    manager = CheckpointManager(run_context.run_dir, run_id=run_context.run_id)

    with manager.create_sqlite_saver() as saver:
        subgraph = build_interrupting_repair_subgraph(
            service=service,
            request_builder=lambda _state: request,
            checkpointer=saver,
        )
        state = create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["repair"],
        )
        subgraph.invoke(state, config=manager.get_thread_config())
        subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )
        edited_patch = subgraph.invoke(
            Command(
                resume={
                    "decision_type": "edit",
                    "edited_payload": {"patch_draft": edited_draft.model_dump(mode="json")},
                    "auto": True,
                }
            ),
            config=manager.get_thread_config(),
        )
        edited_payload = edited_patch["__interrupt__"][0].value
        command_review = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )
        final = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )

    assert edited_payload["action"] == "approve_repair_patch"
    assert edited_payload["payload"]["changed_files"] == ["math_utils.py"]
    assert command_review["__interrupt__"][0].value["action"] == "approve_regression_command"
    assert final["stage_results"]["repair"]["status"] == "succeeded"
