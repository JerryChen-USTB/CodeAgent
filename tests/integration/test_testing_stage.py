from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from codeagent.config.schema import Stage, TaskConfig
from codeagent.reports.artifact_store import ArtifactStore
from codeagent.reports.schemas import StageResult
from codeagent.workflow.checkpoint import CheckpointManager
from codeagent.runtime.run_context import create_run_context
from codeagent.stages.testing_service import (
    TestFileChange,
    TestingPlan,
    TestingRequest,
    TestingService,
)
from codeagent.tools.hitl import ApprovalDecision
from codeagent.workflow.factory import WorkflowFactory
from codeagent.workflow.state import AgentState, create_initial_state
from codeagent.workflow.subgraphs.testing import (
    build_interrupting_testing_subgraph,
    build_testing_subgraph,
    create_testing_stage_handler,
)
from langgraph.types import Command


def _run_context(tmp_path: Path, project_root: Path):
    task_config = TaskConfig(
        stages=["test"],
        project_path=project_root,
        output_dir=tmp_path / "runs",
        test_command={"command": "python -m pytest -q", "timeout_seconds": 30},
    )
    return create_run_context(task_config, output_root=tmp_path / "runs")


def _service(tmp_path: Path, project_root: Path) -> tuple[TestingService, object]:
    run_context = _run_context(tmp_path, project_root)
    return TestingService(run_context=run_context), run_context


def _decision(kind: str = "approve", *, action_id: str = "testing") -> ApprovalDecision:
    return ApprovalDecision(
        interrupt_id=action_id,
        decision_type=kind,  # type: ignore[arg-type]
        comment=f"{kind} for testing fixture.",
        auto=True,
    )


def _long_readable_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    return Path("\\\\?\\" + str(path.resolve()))


def _plan(test_content: str, *, path: str = "tests/test_math_utils.py") -> TestingPlan:
    return TestingPlan(
        target_summary="Verify the add helper.",
        strategy="Add pytest coverage for the public add function.",
        acceptance_criteria=["add(2, 3) returns 5"],
        changes=[
            TestFileChange(
                path=path,
                old_content=None,
                new_content=test_content,
                rationale="Required regression coverage.",
            )
        ],
        command="python -m pytest -q",
        framework="pytest",
    )


def _write_project(project_root: Path, *, broken: bool = False) -> None:
    project_root.mkdir()
    (project_root / "math_utils.py").write_text(
        "def add(left, right):\n"
        + ("    return left - right\n" if broken else "    return left + right\n"),
        encoding="utf-8",
    )


def _request(plan: TestingPlan, **overrides: ApprovalDecision) -> TestingRequest:
    return TestingRequest(
        plan=plan,
        plan_review=overrides.get("plan_review", _decision(action_id="testing_plan")),
        patch_approval=overrides.get("patch_approval", _decision(action_id="testing_patch")),
        command_approval=overrides.get("command_approval", _decision(action_id="testing_command")),
    )


def test_testing_plan_schema_rejects_empty_changes() -> None:
    with pytest.raises(ValidationError):
        TestingPlan(
            target_summary="",
            strategy="No test work.",
            acceptance_criteria=[],
            changes=[],
            command="python -m pytest -q",
            framework="pytest",
        )


def test_testing_service_success_applies_tests_runs_command_and_writes_report(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    request = _request(
        _plan(
            "from math_utils import add\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        )
    )

    result = service.run(request)

    stage_dir = run_context.run_dir / "testing"
    test_result = json.loads((stage_dir / "test_result.json").read_text(encoding="utf-8"))
    artifact_store = ArtifactStore.load(run_context.run_dir)

    assert result.status == "succeeded"
    assert (project_root / "tests" / "test_math_utils.py").exists()
    assert test_result["success"] is True
    assert test_result["passed"] == 1
    for filename in [
        "test_plan.md",
        "test.patch.diff",
        "changed_files.json",
        "test_command.json",
        "test_result.json",
        "test_report.md",
        "stage_result.json",
    ]:
        assert (stage_dir / filename).exists(), filename
    assert artifact_store.find("testing_test_report") is not None


def test_testing_service_rejects_zero_collected_tests(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    plan = TestingPlan(
        target_summary="Ensure the test stage rejects empty discovery.",
        strategy="Create a helper file that unittest will not collect.",
        acceptance_criteria=["A zero-test run is reported as failed."],
        changes=[
            TestFileChange(
                path="tests/__init__.py",
                old_content=None,
                new_content="",
                rationale="Make the unittest discovery directory importable.",
            ),
            TestFileChange(
                path="tests/helper.py",
                old_content=None,
                new_content="VALUE = 1\n",
                rationale="This file is intentionally not a unittest test module.",
            )
        ],
        command="python -m unittest discover -s tests",
        framework="unittest",
    )

    result = service.run(_request(plan))

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.category == "validation"
    assert "no tests were collected" in result.summary
    test_result = json.loads(
        (run_context.run_dir / "testing" / "test_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert test_result["total"] == 0


def test_testing_service_writes_artifacts_under_long_windows_paths(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    run_context = _run_context(tmp_path, project_root)
    long_dir = run_context.run_dir / "testing_long"
    while len(str(long_dir / "test_report.md")) < 285:
        long_dir = long_dir / "deep_segment_for_windows_path_limit"
    run_context.stage_dirs[Stage.TEST] = long_dir
    service = TestingService(run_context=run_context)
    request = _request(
        _plan(
            "from math_utils import add\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        )
    )

    result = service.run(request)

    readable_stage_dir = _long_readable_path(long_dir)
    assert result.status == "succeeded"
    assert (readable_stage_dir / "test_plan.md").exists()
    assert (readable_stage_dir / "test.patch.diff").exists()
    assert (readable_stage_dir / "test_report.md").exists()
    assert (readable_stage_dir / "stage_result.json").exists()


def test_testing_service_failed_tests_produce_failed_stage_result(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root, broken=True)
    service, run_context = _service(tmp_path, project_root)
    request = _request(
        _plan(
            "from math_utils import add\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        )
    )

    result = service.run(request)

    test_result = json.loads(
        (run_context.run_dir / "testing" / "test_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result.status == "failed"
    assert test_result["success"] is False
    assert test_result["failed"] == 1
    assert result.error is not None
    assert result.error.category == "pytest_failure"


def test_testing_service_rejects_source_file_test_patch(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    request = _request(_plan("assert True\n", path="math_utils.py"))

    result = service.run(request)

    attempts = json.loads(
        (run_context.run_dir / "testing" / "test_patch_attempts.json").read_text(
            encoding="utf-8"
        )
    )
    assert result.status == "failed"
    assert "test path" in attempts["attempts"][0]["error"]
    assert "assert True" not in (project_root / "math_utils.py").read_text(encoding="utf-8")


def test_testing_service_command_rejection_does_not_run_tests(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    request = _request(
        _plan("def test_placeholder():\n    assert True\n"),
        command_approval=ApprovalDecision(
            interrupt_id="testing_command",
            decision_type="reject",
            comment="Do not run tests.",
        ),
    )

    result = service.run(request)

    command_record = json.loads(
        (run_context.run_dir / "testing" / "test_command.json").read_text(
            encoding="utf-8"
        )
    )
    assert result.status == "failed"
    assert command_record["executed"] is False
    assert not (run_context.run_dir / "testing" / "logs").exists()


def test_testing_service_command_edit_runs_edited_command(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    request = _request(
        _plan("def test_placeholder():\n    assert True\n"),
        command_approval=ApprovalDecision(
            interrupt_id="testing_command",
            decision_type="edit",
            edited_payload={"command": "python -m pytest tests -q"},
            comment="Narrow test command.",
            auto=True,
        ),
    )

    result = service.run(request)

    command_record = json.loads(
        (run_context.run_dir / "testing" / "test_command.json").read_text(
            encoding="utf-8"
        )
    )
    assert result.status == "succeeded"
    assert command_record["command"] == "python -m pytest tests -q"
    assert command_record["executed"] is True


def test_testing_service_denied_command_edit_writes_failed_stage_result(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    request = _request(
        _plan("def test_placeholder():\n    assert True\n"),
        command_approval=ApprovalDecision(
            interrupt_id="testing_command",
            decision_type="edit",
            edited_payload={"command": "python -m pip install example"},
            comment="Unsafe command should be denied.",
            auto=True,
        ),
    )

    result = service.run(request)

    stage_result = json.loads(
        (run_context.run_dir / "testing" / "stage_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result.status == "failed"
    assert stage_result["error"]["category"] == "shell"
    assert "not allowed" in stage_result["error"]["message"]


def test_testing_service_rejects_hidden_benchmark_command_path(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    request = _request(
        _plan("def test_placeholder():\n    assert True\n"),
        command_approval=ApprovalDecision(
            interrupt_id="testing_command",
            decision_type="edit",
            edited_payload={"command": "python -m pytest evaluation -q"},
            comment="Hidden benchmark path must not be exposed.",
            auto=True,
        ),
    )

    result = service.run(request)

    stage_result = json.loads(
        (run_context.run_dir / "testing" / "stage_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result.status == "failed"
    assert stage_result["error"]["category"] == "validation"
    assert "hidden benchmark path" in stage_result["error"]["message"]
    assert not (run_context.run_dir / "testing" / "logs").exists()


def test_testing_service_rejects_hidden_benchmark_command_option_value(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    request = _request(
        _plan("def test_placeholder():\n    assert True\n"),
        command_approval=ApprovalDecision(
            interrupt_id="testing_command",
            decision_type="edit",
            edited_payload={"command": "python -m pytest --rootdir=evaluation -q"},
            comment="Hidden benchmark path must not be passed as an option value.",
            auto=True,
        ),
    )

    result = service.run(request)

    stage_result = json.loads(
        (run_context.run_dir / "testing" / "stage_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result.status == "failed"
    assert stage_result["error"]["category"] == "validation"
    assert "hidden benchmark path" in stage_result["error"]["message"]
    assert not (run_context.run_dir / "testing" / "logs").exists()


def test_testing_subgraph_handler_routes_failed_testing_to_debug(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root, broken=True)
    service, _run_context_obj = _service(tmp_path, project_root)
    request = _request(
        _plan(
            "from math_utils import add\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        )
    )
    handler = create_testing_stage_handler(
        service=service,
        request_builder=lambda _state: request,
    )

    def debug_handler(state: AgentState) -> dict[str, Any]:
        stage_results = dict(state.get("stage_results", {}))
        stage_results["debugging"] = StageResult(
            stage="debugging",
            status="succeeded",
            started_at="2026-06-03T08:00:00Z",
            summary="debug visited",
        ).model_dump(mode="json", exclude_none=True)
        return {"stage_results": stage_results}

    graph = WorkflowFactory(
        stage_handlers={"testing": handler, "debugging": debug_handler}
    ).build()
    result_state = graph.invoke(
        create_initial_state(
            run_id="run-testing-route",
            mode="run",
            selected_stages=["test", "debug"],
        )
    )

    assert result_state["stage_results"]["testing"]["status"] == "failed"
    assert result_state["stage_results"]["debugging"]["status"] == "succeeded"
    assert result_state["final_status"] == "succeeded"


def test_testing_subgraph_can_run_as_standalone_subgraph(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, _run_context_obj = _service(tmp_path, project_root)
    request = _request(_plan("def test_placeholder():\n    assert True\n"))
    handler = create_testing_stage_handler(
        service=service,
        request_builder=lambda _state: request,
    )
    subgraph = build_testing_subgraph(handler)

    result_state = subgraph.invoke(
        create_initial_state(
            run_id="run-testing-subgraph",
            mode="run",
            selected_stages=["test"],
        )
    )

    assert result_state["stage_results"]["testing"]["status"] == "succeeded"
    assert "testing_test_report" in result_state["artifact_refs"]


def test_interrupting_testing_subgraph_reviews_plan_patch_and_command(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    request = _request(
        _plan(
            "from math_utils import add\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        )
    )
    manager = CheckpointManager(run_context.run_dir, run_id=run_context.run_id)

    with manager.create_sqlite_saver() as saver:
        subgraph = build_interrupting_testing_subgraph(
            service=service,
            request_builder=lambda _state: request,
            checkpointer=saver,
        )
        state = create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["test"],
        )
        first = subgraph.invoke(state, config=manager.get_thread_config())
        first_payload = first["__interrupt__"][0].value
        assert first_payload["action"] == "review_test_plan"
        assert not (project_root / "tests" / "test_math_utils.py").exists()

        second = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )
        second_payload = second["__interrupt__"][0].value
        assert second_payload["action"] == "approve_test_patch"
        assert (run_context.run_dir / "testing" / "test.patch.diff").exists()
        assert not (project_root / "tests" / "test_math_utils.py").exists()

        third = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )
        third_payload = third["__interrupt__"][0].value
        assert third_payload["action"] == "approve_test_command"
        assert (project_root / "tests" / "test_math_utils.py").exists()
        assert not (run_context.run_dir / "testing" / "logs").exists()

        final = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )

    assert final["stage_results"]["testing"]["status"] == "succeeded"
    assert (run_context.run_dir / "testing" / "test_result.json").exists()


def test_interrupting_testing_subgraph_rejects_tampered_approved_patch(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    request = _request(_plan("def test_placeholder():\n    assert True\n"))
    manager = CheckpointManager(run_context.run_dir, run_id=run_context.run_id)

    with manager.create_sqlite_saver() as saver:
        subgraph = build_interrupting_testing_subgraph(
            service=service,
            request_builder=lambda _state: request,
            checkpointer=saver,
        )
        state = create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["test"],
        )
        subgraph.invoke(state, config=manager.get_thread_config())
        second = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )
        patch_payload = second["__interrupt__"][0].value
        assert patch_payload["payload"]["patch_sha256"]
        (run_context.run_dir / "testing" / "test.patch.diff").write_text(
            "tampered patch\n",
            encoding="utf-8",
        )
        final = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )

    assert final["stage_results"]["testing"]["status"] == "failed"
    assert not (project_root / "tests").exists()
    assert "hash mismatch" in final["stage_results"]["testing"]["error"]["message"]


def test_interrupting_testing_subgraph_accepts_edited_plan_review(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    edited_plan = _plan(
        "from math_utils import add\n\n"
        "def test_add_edited_plan():\n"
        "    assert add(1, 4) == 5\n",
        path="tests/test_edited_plan.py",
    ).model_copy(
        update={"command": "python -m pytest tests/test_edited_plan.py -q"}
    )
    request = _request(_plan("def test_placeholder():\n    assert True\n"))
    manager = CheckpointManager(run_context.run_dir, run_id=run_context.run_id)

    with manager.create_sqlite_saver() as saver:
        subgraph = build_interrupting_testing_subgraph(
            service=service,
            request_builder=lambda _state: request,
            checkpointer=saver,
        )
        state = create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["test"],
        )
        subgraph.invoke(state, config=manager.get_thread_config())
        second = subgraph.invoke(
            Command(
                resume={
                    "decision_type": "edit",
                    "edited_payload": {"plan": edited_plan.model_dump(mode="json")},
                    "auto": True,
                }
            ),
            config=manager.get_thread_config(),
        )

    patch_payload = second["__interrupt__"][0].value
    assert patch_payload["action"] == "approve_test_patch"
    assert "tests/test_edited_plan.py" in patch_payload["payload"]["changed_files"]
    with manager.create_sqlite_saver() as saver:
        subgraph = build_interrupting_testing_subgraph(
            service=service,
            request_builder=lambda _state: request,
            checkpointer=saver,
        )
        command_review = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )

    command_payload = command_review["__interrupt__"][0].value
    assert command_payload["action"] == "approve_test_command"
    assert (
        command_payload["payload"]["command"]
        == "python -m pytest tests/test_edited_plan.py -q"
    )


def test_interrupting_testing_subgraph_accepts_edited_patch_plan(tmp_path) -> None:
    project_root = tmp_path / "project"
    _write_project(project_root)
    service, run_context = _service(tmp_path, project_root)
    edited_plan = _plan(
        "from math_utils import add\n\n"
        "def test_add_edited_patch():\n"
        "    assert add(2, 2) == 4\n",
        path="tests/test_edited_patch.py",
    ).model_copy(
        update={"command": "python -m pytest tests/test_edited_patch.py -q"}
    )
    request = _request(_plan("def test_placeholder():\n    assert True\n"))
    manager = CheckpointManager(run_context.run_dir, run_id=run_context.run_id)

    with manager.create_sqlite_saver() as saver:
        subgraph = build_interrupting_testing_subgraph(
            service=service,
            request_builder=lambda _state: request,
            checkpointer=saver,
        )
        state = create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["test"],
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
                    "edited_payload": {"plan": edited_plan.model_dump(mode="json")},
                    "auto": True,
                }
            ),
            config=manager.get_thread_config(),
        )
        edited_payload = edited_patch["__interrupt__"][0].value
        assert edited_payload["action"] == "approve_test_patch"
        assert "tests/test_edited_patch.py" in edited_payload["payload"]["changed_files"]

        command_review = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )
        command_payload = command_review["__interrupt__"][0].value
        assert (
            command_payload["payload"]["command"]
            == "python -m pytest tests/test_edited_patch.py -q"
        )
        final = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )

    assert final["stage_results"]["testing"]["status"] == "succeeded"
    assert (project_root / "tests" / "test_edited_patch.py").exists()
