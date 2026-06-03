from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from codeagent.config.schema import Stage, TaskConfig
from codeagent.reports.artifact_store import ArtifactStore
from codeagent.runtime.run_context import create_run_context
from codeagent.stages.implementation_service import (
    ImplementationFileChange,
    ImplementationPlan,
    ImplementationRequest,
    ImplementationService,
)
from codeagent.tools.hitl import ApprovalDecision
from codeagent.workflow.checkpoint import CheckpointManager
from codeagent.workflow.state import create_initial_state
from codeagent.workflow.subgraphs.implementation import (
    build_interrupting_implementation_subgraph,
    build_implementation_subgraph,
    create_implementation_stage_handler,
)
from langgraph.types import Command


def _run_context(tmp_path: Path, project_root: Path):
    task_config = TaskConfig(
        stages=["implement"],
        project_path=project_root,
        output_dir=tmp_path / "runs",
    )
    return create_run_context(task_config, output_root=tmp_path / "runs")


def _service(tmp_path: Path, project_root: Path) -> tuple[ImplementationService, object]:
    run_context = _run_context(tmp_path, project_root)
    return ImplementationService(run_context=run_context), run_context


def _approve(*, interrupt_id: str = "implementation_patch") -> ApprovalDecision:
    return ApprovalDecision(
        interrupt_id=interrupt_id,
        decision_type="approve",
        comment="Approved for integration fixture.",
        auto=True,
    )


def _long_readable_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    return Path("\\\\?\\" + str(path.resolve()))


def _plan(path: str, content: str, *, summary: str = "Create implementation file.") -> ImplementationPlan:
    return ImplementationPlan(
        requirements_summary="Add a small calculator utility.",
        impact_summary=summary,
        changes=[
            ImplementationFileChange(
                path=path,
                old_content=None,
                new_content=content,
                rationale="Required by the fixture acceptance criteria.",
            )
        ],
        syntax_check_targets=[path] if path.endswith(".py") else [],
    )


def test_implementation_plan_schema_rejects_empty_plans() -> None:
    with pytest.raises(ValidationError):
        ImplementationPlan(
            requirements_summary="",
            impact_summary="No changes.",
            changes=[],
        )


def test_implementation_service_success_generates_patch_artifacts_and_stage_result(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    service, run_context = _service(tmp_path, project_root)
    request = ImplementationRequest(
        plan=_plan(
            "calculator.py",
            "def add(left: int, right: int) -> int:\n    return left + right\n",
        ),
        approval=_approve(),
    )

    result = service.run(request)

    stage_dir = run_context.run_dir / "implementation"
    assert result.status == "succeeded"
    assert (project_root / "calculator.py").read_text(encoding="utf-8").strip().endswith(
        "return left + right"
    )
    for filename in [
        "implementation_plan.md",
        "implementation.patch.diff",
        "changed_files.json",
        "syntax_check.log",
        "implementation_report.md",
        "stage_result.json",
    ]:
        assert (stage_dir / filename).exists(), filename
    changed_files = json.loads((stage_dir / "changed_files.json").read_text(encoding="utf-8"))
    stage_result = json.loads((stage_dir / "stage_result.json").read_text(encoding="utf-8"))
    syntax_log = (stage_dir / "syntax_check.log").read_text(encoding="utf-8")
    artifact_store = ArtifactStore.load(run_context.run_dir)

    assert changed_files["changed_files"] == ["calculator.py"]
    assert stage_result["status"] == "succeeded"
    assert "exit_code: 0" in syntax_log
    assert artifact_store.find("implementation_plan") is not None
    assert artifact_store.find("implementation_patch") is not None
    assert artifact_store.find("implementation_report") is not None


def test_implementation_service_writes_artifacts_under_long_windows_paths(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    run_context = _run_context(tmp_path, project_root)
    long_dir = run_context.run_dir
    while len(str(long_dir / "implementation_report.md")) < 285:
        long_dir = long_dir / "deep_segment_for_windows_path_limit"
    run_context.stage_dirs[Stage.IMPLEMENT] = long_dir
    service = ImplementationService(run_context=run_context)
    request = ImplementationRequest(
        plan=_plan(
            "long_path_module.py",
            "def value() -> int:\n    return 42\n",
        ),
        approval=_approve(),
    )

    result = service.run(request)

    readable_stage_dir = (
        Path("\\\\?\\" + str(long_dir.resolve())) if os.name == "nt" else long_dir
    )
    assert result.status == "succeeded"
    assert (readable_stage_dir / "implementation_plan.md").exists()
    assert (readable_stage_dir / "implementation_report.md").exists()
    assert (readable_stage_dir / "stage_result.json").exists()


def test_implementation_syntax_check_does_not_write_pycache_under_long_project_paths(
    tmp_path,
) -> None:
    project_root = tmp_path / "project"
    while len(
        str(
            project_root
            / "workspace"
            / "student_gradebook"
            / "__pycache__"
            / "__init__.cpython-313.pyc"
        )
    ) < 285:
        project_root = project_root / "deep_segment_for_windows_path_limit"
    service, run_context = _service(tmp_path, project_root)
    request = ImplementationRequest(
        plan=_plan(
            "workspace/student_gradebook/__init__.py",
            "VALUE = 42\n",
        ),
        approval=_approve(),
    )

    result = service.run(request)

    readable_project_root = _long_readable_path(project_root)
    syntax_log = (run_context.run_dir / "implementation" / "syntax_check.log").read_text(
        encoding="utf-8"
    )
    assert result.status == "succeeded"
    assert "exit_code: 0" in syntax_log
    assert not (
        readable_project_root / "workspace" / "student_gradebook" / "__pycache__"
    ).exists()


def test_implementation_service_retries_next_patch_candidate_after_validation_failure(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    service, run_context = _service(tmp_path, project_root)
    invalid_plan = _plan("../escape.py", "VALUE = 1\n", summary="Invalid path first.")
    valid_plan = _plan("safe_module.py", "VALUE = 42\n", summary="Fallback valid path.")
    request = ImplementationRequest(
        plan=invalid_plan,
        alternate_plans=[valid_plan],
        approval=_approve(),
    )

    result = service.run(request)

    attempts = json.loads(
        (run_context.run_dir / "implementation" / "patch_attempts.json").read_text(
            encoding="utf-8"
        )
    )
    assert result.status == "succeeded"
    assert attempts["attempts"][0]["status"] == "validation_failed"
    assert "outside project root" in attempts["attempts"][0]["error"]
    assert (project_root / "safe_module.py").exists()
    assert not (tmp_path / "escape.py").exists()


def test_implementation_service_does_not_persist_sensitive_candidate_diff(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    service, run_context = _service(tmp_path, project_root)
    request = ImplementationRequest(
        plan=_plan(".env", "SAMPLE_VALUE=fixture\n", summary="Invalid sensitive path."),
        approval=_approve(),
    )

    result = service.run(request)

    stage_dir = run_context.run_dir / "implementation"
    attempts = json.loads((stage_dir / "patch_attempts.json").read_text(encoding="utf-8"))
    assert result.status == "failed"
    assert attempts["attempts"][0]["status"] == "validation_failed"
    assert "sensitive" in attempts["attempts"][0]["error"]
    assert not (stage_dir / "implementation_attempt_1.patch.diff").exists()
    assert not (stage_dir / "implementation.patch.diff").exists()


def test_implementation_service_reports_syntax_check_failure(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    service, run_context = _service(tmp_path, project_root)
    request = ImplementationRequest(
        plan=_plan("broken.py", "def broken(:\n    return 1\n"),
        approval=_approve(),
    )

    result = service.run(request)

    stage_result = json.loads(
        (run_context.run_dir / "implementation" / "stage_result.json").read_text(
            encoding="utf-8"
        )
    )
    syntax_log = (run_context.run_dir / "implementation" / "syntax_check.log").read_text(
        encoding="utf-8"
    )

    assert result.status == "failed"
    assert stage_result["status"] == "failed"
    assert stage_result["error"]["category"] == "shell"
    assert "exit_code:" in syntax_log
    assert "SyntaxError" in syntax_log
    assert (project_root / "broken.py").exists()


def test_implementation_service_cancelled_approval_does_not_apply_patch(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    service, run_context = _service(tmp_path, project_root)
    request = ImplementationRequest(
        plan=_plan("cancelled.py", "VALUE = 'should not be written'\n"),
        approval=ApprovalDecision(
            interrupt_id="implementation_patch",
            decision_type="cancel",
            comment="Stop this implementation.",
        ),
    )

    result = service.run(request)

    decisions = [
        json.loads(line)
        for line in (run_context.run_dir / "decision_trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert result.status == "cancelled"
    assert not (project_root / "cancelled.py").exists()
    assert decisions[-1]["decision_type"] == "cancel"
    assert decisions[-1]["action"] == "approve_implementation_patch"


def test_implementation_service_applies_edited_approval_plan(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    service, run_context = _service(tmp_path, project_root)
    edited_plan = _plan("edited.py", "VALUE = 2\n", summary="Human edited implementation.")
    request = ImplementationRequest(
        plan=_plan("edited.py", "VALUE = 1\n", summary="Initial implementation."),
        approval=ApprovalDecision(
            interrupt_id="implementation_patch",
            decision_type="edit",
            edited_payload={"plan": edited_plan.model_dump(mode="json")},
            comment="Use the edited implementation plan.",
        ),
    )

    result = service.run(request)

    decisions = [
        json.loads(line)
        for line in (run_context.run_dir / "decision_trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert result.status == "succeeded"
    assert (project_root / "edited.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert [decision["decision_type"] for decision in decisions] == ["edit", "approve"]


def test_implementation_subgraph_handler_updates_checkpoint_safe_state(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    service, _run_context_obj = _service(tmp_path, project_root)
    request = ImplementationRequest(
        plan=_plan("subgraph_file.py", "ANSWER = 42\n"),
        approval=_approve(),
    )
    handler = create_implementation_stage_handler(
        service=service,
        request_builder=lambda _state: request,
    )
    subgraph = build_implementation_subgraph(handler)
    state = create_initial_state(
        run_id="run-implementation-subgraph",
        mode="run",
        selected_stages=["implement"],
    )

    result_state = subgraph.invoke(state)

    assert result_state["stage_results"]["implementation"]["status"] == "succeeded"
    assert "implementation_patch" in result_state["artifact_refs"]
    assert result_state["messages"][-1]["type"] == "stage_completed"
    assert result_state["messages"][-1]["stage"] == "implementation"


def test_interrupting_implementation_subgraph_pauses_before_apply_and_resumes(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    service, run_context = _service(tmp_path, project_root)
    request = ImplementationRequest(
        plan=_plan("interrupt_file.py", "VALUE = 7\n"),
        approval=_approve(),
    )
    manager = CheckpointManager(run_context.run_dir, run_id=run_context.run_id)

    with manager.create_sqlite_saver() as saver:
        subgraph = build_interrupting_implementation_subgraph(
            service=service,
            request_builder=lambda _state: request,
            checkpointer=saver,
        )
        initial = create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["implement"],
        )
        first = subgraph.invoke(initial, config=manager.get_thread_config())
        interrupt_payload = first["__interrupt__"][0].value

        assert interrupt_payload["interrupt_id"] == "implementation_patch"
        assert interrupt_payload["action"] == "approve_implementation_patch"
        assert "interrupt_file.py" in interrupt_payload["payload"]["changed_files"]
        assert (run_context.run_dir / "implementation" / "implementation.patch.diff").exists()
        assert not (project_root / "interrupt_file.py").exists()

        resumed = subgraph.invoke(
            Command(
                resume={
                    "decision_type": "approve",
                    "comment": "Resume and apply the approved implementation patch.",
                    "auto": True,
                }
            ),
            config=manager.get_thread_config(),
        )

    assert (project_root / "interrupt_file.py").read_text(encoding="utf-8") == "VALUE = 7\n"
    assert resumed["pending_interrupt"] is None
    assert resumed["stage_results"]["implementation"]["status"] == "succeeded"


def test_interrupting_subgraph_applies_approved_patch_without_regenerating(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    service, run_context = _service(tmp_path, project_root)
    request_box = {
        "request": ImplementationRequest(
            plan=_plan("approved_file.py", "APPROVED = True\n"),
            approval=_approve(),
        )
    }
    manager = CheckpointManager(run_context.run_dir, run_id=run_context.run_id)

    with manager.create_sqlite_saver() as saver:
        subgraph = build_interrupting_implementation_subgraph(
            service=service,
            request_builder=lambda _state: request_box["request"],
            checkpointer=saver,
        )
        initial = create_initial_state(
            run_id=run_context.run_id,
            mode="run",
            selected_stages=["implement"],
        )
        first = subgraph.invoke(initial, config=manager.get_thread_config())
        approved_hash = first["__interrupt__"][0].value["payload"]["patch_sha256"]

        request_box["request"] = ImplementationRequest(
            plan=_plan("mutated_file.py", "MUTATED = True\n"),
            approval=_approve(),
        )
        resumed = subgraph.invoke(
            Command(resume={"decision_type": "approve", "auto": True}),
            config=manager.get_thread_config(),
        )

    patch_payload = json.loads(
        (run_context.run_dir / "implementation" / "stage_result.json").read_text(
            encoding="utf-8"
        )
    )
    syntax_log = (run_context.run_dir / "implementation" / "syntax_check.log").read_text(
        encoding="utf-8"
    )
    implementation_report = (
        run_context.run_dir / "implementation" / "implementation_report.md"
    ).read_text(encoding="utf-8")
    assert approved_hash
    assert resumed["stage_results"]["implementation"]["status"] == "succeeded"
    assert (project_root / "approved_file.py").exists()
    assert not (project_root / "mutated_file.py").exists()
    assert "approved_file.py" in syntax_log
    assert "approved_file.py" in implementation_report
    assert "mutated_file.py" not in implementation_report
    assert "implementation_patch" in patch_payload["artifact_ids"]
