from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from codeagent.cli.app import app
from codeagent.cli.approval_console import PATCH_AUTO_APPROVE_REMAINING_KEY
from codeagent.cli.executor import _failure_logs_from_config
from codeagent.config.schema import InputMaterial, Stage, TaskConfig
from codeagent.runtime.run_context import create_run_context
from codeagent.stages.implementation_service import (
    ImplementationApprovalPreview,
    ImplementationFileChange,
    ImplementationPlan,
    ImplementationPatchDraft,
    ImplementationPatchFileChange,
    ImplementationRequest,
    PATCH_INTERRUPT_ID,
    PLAN_INTERRUPT_ID,
)
from codeagent.stages.repair_service import (
    REPAIR_COMMAND_INTERRUPT_ID,
    REPAIR_PLAN_INTERRUPT_ID,
    REPAIR_PATCH_INTERRUPT_ID,
    RepairPatchDraft,
    RepairPatchFileChange,
    RepairFileChange,
    RepairPlan,
    RepairRequest,
)
from codeagent.stages.testing_service import (
    TEST_COMMAND_INTERRUPT_ID,
    TEST_PATCH_INTERRUPT_ID,
    TEST_PLAN_INTERRUPT_ID,
    TestPatchFileChange,
    TestFileChange,
    TestingPatchDraft,
    TestingPlan,
    TestingRequest,
)
from codeagent.tools.hitl import ApprovalDecision


runner = CliRunner()


def _implementation_plan(path: str, *, strategy: str = "Create implementation file.") -> ImplementationPlan:
    return ImplementationPlan(
        requirements_summary="Add the requested implementation.",
        implementation_strategy=strategy,
        changes=[
            ImplementationFileChange(
                path=path,
                rationale="Required by the visible requirements.",
                public_interfaces=[],
                acceptance_notes=["Generated source should satisfy visible requirements."],
            )
        ],
        acceptance_criteria=["Generated source should satisfy visible requirements."],
    )


def _implementation_draft(path: str, content: str) -> ImplementationPatchDraft:
    return ImplementationPatchDraft(
        plan_summary="Concrete implementation patch for the approved plan.",
        changes=[
            ImplementationPatchFileChange(
                path=path,
                old_content=None,
                new_content=content,
                rationale="Required by the visible requirements.",
            )
        ],
        syntax_check_targets=[path] if path.endswith(".py") else [],
    )


def _testing_plan(
    path: str,
    *,
    command: str,
    framework: str = "pytest",
) -> TestingPlan:
    return TestingPlan(
        target_summary="Generated visible regression tests.",
        strategy="Add visible tests so the testing stage executes real discovered tests.",
        acceptance_criteria=["At least one generated visible test is collected and executed."],
        changes=[
            TestFileChange(
                path=path,
                test_focus="Exercise the visible product behavior.",
                rationale="Exercise the testing pipeline without hidden benchmark material.",
            )
        ],
        command=command,
        framework=framework,  # type: ignore[arg-type]
    )


def _testing_draft(
    path: str,
    content: str,
    *,
    command: str,
    framework: str = "pytest",
) -> TestingPatchDraft:
    return TestingPatchDraft(
        plan_summary="Concrete visible test patch for the approved plan.",
        changes=[
            TestPatchFileChange(
                path=path,
                old_content=None,
                new_content=content,
                rationale="Exercise the testing pipeline without hidden benchmark material.",
            )
        ],
        command=command,
        framework=framework,  # type: ignore[arg-type]
    )


def _repair_plan(
    path: str,
    *,
    command: str,
    framework: str = "pytest",
) -> RepairPlan:
    return RepairPlan(
        root_cause="Visible failure identifies an implementation bug.",
        strategy="Repair the implementation source and verify with visible tests.",
        changes=[
            RepairFileChange(
                path=path,
                rationale="Required by the visible failure evidence.",
                expected_effect="The failing visible tests pass after repair.",
            )
        ],
        verification_command=command,
        framework=framework,  # type: ignore[arg-type]
    )


def _repair_draft(
    path: str,
    content: str,
    *,
    old_content: str | None = None,
    command: str,
    framework: str = "pytest",
) -> RepairPatchDraft:
    return RepairPatchDraft(
        plan_summary="Concrete repair patch for the approved repair plan.",
        changes=[
            RepairPatchFileChange(
                path=path,
                old_content=old_content,
                new_content=content,
                rationale="Required by the visible failure evidence.",
            )
        ],
        verification_command=command,
        framework=framework,  # type: ignore[arg-type]
    )


def _debug_fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "calculator.py").write_text(
        "def add(left, right):\n    return left - right\n",
        encoding="utf-8",
    )
    log = project / "failing.log"
    log.write_text(
        "\n".join(
            [
                "FAILED tests/test_calculator.py::test_add - AssertionError",
                "E assert 0 == 2",
                "1 failed, 3 passed in 0.05s",
            ]
        ),
        encoding="utf-8",
    )
    return project, log


def test_run_config_executes_debug_stage_and_writes_reports(tmp_path) -> None:
    project, log = _debug_fixture(tmp_path)
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
task_id: debug-demo
stages: [debug]
project_path: {project.as_posix()}
output_dir: {output_dir.as_posix()}
input_materials:
  - material_type: error_log
    path: {log.as_posix()}
    required: true
test_command:
  command: "pytest -q"
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "[节点] 调试阶段 已完成" in result.output
    assert "[最终结果] 成功" in result.output
    run_dirs = [path for path in output_dir.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    stage_result = json.loads(
        (run_dir / "debugging" / "stage_result.json").read_text(encoding="utf-8")
    )
    assert metadata["stages"] == ["debug"]
    assert stage_result["status"] == "succeeded"
    assert (run_dir / "debugging" / "debug_report.md").exists()
    assert (run_dir / "final_report.md").exists()


def test_debug_subcommand_maps_to_task_config_and_executes_static_log(tmp_path) -> None:
    project, log = _debug_fixture(tmp_path)
    output_dir = tmp_path / "runs"

    result = runner.invoke(
        app,
        [
            "debug",
            "--project",
            str(project),
            "--log",
            str(log),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert "[最终结果] 成功" in result.output
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    task_config = (run_dir / "task_config.yaml").read_text(encoding="utf-8")
    assert "stages:" in task_config
    assert "- debug" in task_config
    assert "mode: run" in task_config


def test_test_debug_run_uses_generated_test_logs_before_external_failure_material(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    (project / "math_utils.py").write_text(
        "def add(left, right):\n    return left - right\n",
        encoding="utf-8",
    )
    (tests_dir / "test_math_utils.py").write_text(
        "\n".join(
            [
                "import unittest",
                "from math_utils import add",
                "",
                "class MathUtilsTest(unittest.TestCase):",
                "    def test_add(self):",
                "        self.assertEqual(add(1, 2), 3)",
                "",
                "if __name__ == '__main__':",
                "    unittest.main()",
            ]
        ),
        encoding="utf-8",
    )
    external_log = tmp_path / "input" / "before_test.log"
    external_log.parent.mkdir()
    external_log.write_text("Old visible case input log.\n", encoding="utf-8")
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [test, debug]
project_path: {project.as_posix()}
output_dir: {output_dir.as_posix()}
test_framework: unittest
input_materials:
  - material_type: error_log
    path: {external_log.as_posix()}
    required: true
test_command:
  command: "python -m unittest discover -s tests"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "codeagent.cli.executor.PlanGenerationService",
        _FakePlanGenerationService,
    )

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "failure log path is not allowed" not in result.output
    assert "[最终结果] 成功" in result.output


def test_failure_logs_from_testing_stage_discovers_shortened_shell_logs(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    external_log = tmp_path / "input" / "before_test.log"
    external_log.parent.mkdir()
    external_log.write_text("Old visible case input log.\n", encoding="utf-8")
    context = create_run_context(
        TaskConfig(
            stages=[Stage.TEST, Stage.DEBUG],
            project_path=project,
            output_dir=tmp_path / "runs",
            input_materials=[
                InputMaterial(
                    material_type="error_log",
                    path=external_log,
                    required=True,
                )
            ],
        ),
        output_root=tmp_path / "runs",
    )
    logs_dir = context.stage_dirs[Stage.TEST] / "logs"
    logs_dir.mkdir(parents=True)
    shortened_stdout = logs_dir / "cmd-8cfce45a271f.stdout.log"
    shortened_stderr = logs_dir / "cmd-8cfce45a271f.stderr.log"
    shortened_stdout.write_text("", encoding="utf-8")
    shortened_stderr.write_text("RecursionError: maximum recursion depth exceeded\n", encoding="utf-8")

    logs = _failure_logs_from_config(
        context,
        {"stage_results": {"testing": {"status": "failed"}}},
    )

    assert logs == [shortened_stdout, shortened_stderr]


def test_run_project_options_reject_invalid_stage_order(tmp_path) -> None:
    project, _log = _debug_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["run", "--project", str(project), "--stages", "test,repair"],
    )

    assert result.exit_code != 0
    assert "选择的阶段必须连续" in result.output


def test_run_config_rejects_file_as_project_path(tmp_path) -> None:
    project_file = tmp_path / "not_a_project.py"
    project_file.write_text("print('not a project directory')\n", encoding="utf-8")
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [debug]
project_path: {project_file.as_posix()}
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code != 0
    assert "project_path" in result.output
    assert "必须是目录" in result.output


class _FakePlanGenerationService:
    implementation_plan: ImplementationPlan | None = None
    implementation_patch_draft: ImplementationPatchDraft | None = None
    repair_plan: RepairPlan | None = None
    repair_patch_draft: RepairPatchDraft | None = None
    testing_plan: TestingPlan | None = None
    testing_patch_draft: TestingPatchDraft | None = None

    def __init__(self) -> None:
        pass

    def create_implementation_request(self, _context) -> ImplementationRequest:
        assert self.implementation_plan is not None
        return ImplementationRequest(
            plan=self.implementation_plan,
            approval=ApprovalDecision(
                interrupt_id=PATCH_INTERRUPT_ID,
                decision_type="approve",
                comment="Generated by fake LLM planner.",
                auto=True,
                decided_by="test",
            ),
        )

    def create_implementation_patch_draft(
        self,
        _context,
        _plan,
        *,
        feedback: str | None = None,
    ) -> ImplementationPatchDraft:
        assert self.implementation_patch_draft is not None
        return self.implementation_patch_draft

    def create_repair_request(self, _context) -> RepairRequest:
        assert self.repair_plan is not None
        return RepairRequest(
            plan=self.repair_plan,
            plan_review=ApprovalDecision(
                interrupt_id=REPAIR_PLAN_INTERRUPT_ID,
                decision_type="approve",
                comment="Generated by fake LLM repair planner.",
                auto=True,
                decided_by="test",
            ),
            patch_approval=ApprovalDecision(
                interrupt_id=REPAIR_PATCH_INTERRUPT_ID,
                decision_type="approve",
                comment="Generated by fake LLM repair planner.",
                auto=True,
                decided_by="test",
            ),
            command_approval=ApprovalDecision(
                interrupt_id=REPAIR_COMMAND_INTERRUPT_ID,
                decision_type="approve",
                comment="Generated by fake LLM repair planner.",
                auto=True,
                decided_by="test",
            ),
        )

    def create_repair_patch_draft(
        self,
        _context,
        _plan,
        *,
        feedback: str | None = None,
    ) -> RepairPatchDraft:
        assert self.repair_patch_draft is not None
        return self.repair_patch_draft

    def create_testing_request(self, context) -> TestingRequest:
        plan, draft = (
            (self.testing_plan, self.testing_patch_draft)
            if self.testing_plan is not None and self.testing_patch_draft is not None
            else _generated_testing_plan_for_context(context)
        )
        assert plan is not None
        return TestingRequest(
            plan=plan,
            plan_review=ApprovalDecision(
                interrupt_id=TEST_PLAN_INTERRUPT_ID,
                decision_type="approve",
                comment="Generated by fake LLM testing planner.",
                auto=True,
                decided_by="test",
            ),
            patch_approval=ApprovalDecision(
                interrupt_id=TEST_PATCH_INTERRUPT_ID,
                decision_type="approve",
                comment="Generated by fake LLM testing planner.",
                auto=True,
                decided_by="test",
            ),
            command_approval=ApprovalDecision(
                interrupt_id=TEST_COMMAND_INTERRUPT_ID,
                decision_type="approve",
                comment="Generated by fake LLM testing planner.",
                auto=True,
                decided_by="test",
            ),
        )

    def create_testing_patch_draft(
        self,
        context,
        _plan,
        *,
        feedback: str | None = None,
    ) -> TestingPatchDraft:
        if self.testing_patch_draft is not None:
            return self.testing_patch_draft
        _plan_value, draft = _generated_testing_plan_for_context(context)
        return draft


def _generated_testing_plan_for_context(context) -> tuple[TestingPlan, TestingPatchDraft]:
    if context.task_config.test_framework == "unittest":
        content = (
            "import unittest\n\n"
            "class CodeAgentGeneratedTest(unittest.TestCase):\n"
            "    def test_generated_smoke(self):\n"
            "        self.assertTrue(True)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n"
        )
        command = "python -m unittest discover -s tests"
        framework = "unittest"
    else:
        content = "def test_generated_smoke():\n    assert True\n"
        command = "python -m pytest tests -q"
        framework = "pytest"
    configured = context.task_config.test_command.command
    if "oracle_tests" not in configured and "evaluation" not in configured and "py_compile" not in configured:
        command = configured
    path = "tests/test_codeagent_generated.py"
    return (
        _testing_plan(path, command=command, framework=framework),
        _testing_draft(path, content, command=command, framework=framework),
    )


class _FailingImplementationService:
    def __init__(self, *, run_context) -> None:
        self.run_context = run_context

    def prepare_plan_review(self, _request) -> ImplementationApprovalPreview:
        return ImplementationApprovalPreview(
            payload={
                "interrupt_id": PLAN_INTERRUPT_ID,
                "action": "review_implementation_plan",
                "title": "实施此实现计划？",
                "summary": "Synthetic plan preview.",
                "risk_level": "medium",
                "allowed_decisions": ["approve", "respond"],
                "default_decision": "approve",
                "payload": {},
            }
        )

    def apply_plan_review_decision(self, request, *, approval):
        return request

    def run(self, _request) -> None:
        raise RuntimeError("synthetic implementation service failure")


class _ManualApprovalPlanGenerationService:
    implementation_plan: ImplementationPlan | None = None
    implementation_patch_draft: ImplementationPatchDraft | None = None

    def create_implementation_request(self, _context) -> ImplementationRequest:
        assert self.implementation_plan is not None
        return ImplementationRequest(
            plan=self.implementation_plan,
            approval=ApprovalDecision(
                interrupt_id=PATCH_INTERRUPT_ID,
                decision_type="approve",
                comment="Planner supplied a non-auto approval placeholder.",
                auto=False,
                decided_by="workflow",
            ),
        )

    def create_implementation_patch_draft(
        self,
        _context,
        _plan,
        *,
        feedback: str | None = None,
    ) -> ImplementationPatchDraft:
        assert self.implementation_patch_draft is not None
        return self.implementation_patch_draft


class _ScriptedApprovalConsole:
    decisions: list[ApprovalDecision] = []

    def prompt(self, request):
        if not self.decisions:
            raise AssertionError(f"unexpected approval prompt: {request.interrupt_id}")
        decision = self.decisions.pop(0)
        assert decision.interrupt_id == request.interrupt_id
        return decision


def test_implement_subcommand_generates_plan_and_applies_patch(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = tmp_path / "requirements.md"
    requirements.write_text("Add a tiny feature.\n", encoding="utf-8")
    output_dir = tmp_path / "runs"
    _FakePlanGenerationService.implementation_plan = _implementation_plan(
        "feature.py",
        strategy="Create feature.py with a public constant.",
    )
    _FakePlanGenerationService.implementation_patch_draft = _implementation_draft(
        "feature.py",
        'VERSION = "1.0"\n',
    )
    monkeypatch.setattr(
        "codeagent.cli.executor.PlanGenerationService",
        _FakePlanGenerationService,
    )

    result = runner.invoke(
        app,
        [
            "implement",
            "--project",
            str(project),
            "--requirements",
            str(requirements),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert "[最终结果] 成功" in result.output
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    stage_result = json.loads(
        (run_dir / "implementation" / "stage_result.json").read_text(encoding="utf-8")
    )
    report = (run_dir / "final_report.md").read_text(encoding="utf-8")
    assert stage_result["status"] == "succeeded"
    assert (project / "feature.py").read_text(encoding="utf-8") == 'VERSION = "1.0"\n'
    assert "implementation | succeeded" in report


def test_incremental_implementation_applies_each_file_before_next_generation(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [implement]
project_path: {project.as_posix()}
output_dir: {output_dir.as_posix()}
permissions:
  approval_mode: auto
""".strip(),
        encoding="utf-8",
    )

    class IncrementalImplementationPlanner:
        saw_applied_first_file = False
        saw_plan_before_first_file = False
        saw_aggregate_patch_before_second_file = False

        def create_implementation_request(self, _context) -> ImplementationRequest:
            return ImplementationRequest(
                plan=ImplementationPlan(
                    requirements_summary="Add two coordinated modules.",
                    implementation_strategy="Write a.py first, then b.py imports it.",
                    changes=[
                        ImplementationFileChange(
                            path="a.py",
                            rationale="Provide a shared value.",
                        ),
                        ImplementationFileChange(
                            path="b.py",
                            rationale="Consume the shared value.",
                        ),
                    ],
                    acceptance_criteria=["Both modules are present and syntactically valid."],
                ),
                approval=ApprovalDecision(
                    interrupt_id=PATCH_INTERRUPT_ID,
                    decision_type="approve",
                    auto=True,
                    decided_by="test",
                ),
            )

        def select_patch_file_context(
            self,
            _context,
            *,
            stage,
            plan,
            target_path,
            workspace_tree,
            work_summary,
            completed_files,
            failed_attempts,
            feedback=None,
        ):
            raise AssertionError("per-file LLM context selection should not be called")

        def create_implementation_file_patch_draft(
            self,
            _context,
            _plan,
            *,
            target_path,
            workspace_context,
            work_summary,
            completed_files,
            failed_attempts,
            feedback=None,
        ) -> ImplementationPatchDraft:
            stage_dir = _context.run_dir / "implementation"
            if Path(target_path).as_posix() == "a.py":
                type(self).saw_plan_before_first_file = (
                    (stage_dir / "implementation_plan.md").exists()
                    and (stage_dir / "implementation_plan.json").exists()
                )
                return _implementation_draft("a.py", "VALUE = 1\n")
            aggregate_patch = stage_dir / "implementation.patch.diff"
            aggregate_draft = stage_dir / "implementation_patch_draft.json"
            type(self).saw_aggregate_patch_before_second_file = (
                aggregate_patch.exists()
                and aggregate_draft.exists()
                and "b/a.py" in aggregate_patch.read_text(encoding="utf-8")
                and "b/b.py" not in aggregate_patch.read_text(encoding="utf-8")
            )
            type(self).saw_applied_first_file = "VALUE = 1" in workspace_context
            return _implementation_draft(
                "b.py",
                "from a import VALUE\n\nRESULT = VALUE + 1\n",
            )

    monkeypatch.setattr(
        "codeagent.cli.executor.PlanGenerationService",
        IncrementalImplementationPlanner,
    )

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    assert (project / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (
        project / "b.py"
    ).read_text(encoding="utf-8") == "from a import VALUE\n\nRESULT = VALUE + 1\n"
    assert IncrementalImplementationPlanner.saw_plan_before_first_file is True
    assert IncrementalImplementationPlanner.saw_aggregate_patch_before_second_file is True
    assert IncrementalImplementationPlanner.saw_applied_first_file is True
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    final_patch = (run_dir / "implementation" / "implementation.patch.diff").read_text(
        encoding="utf-8"
    )
    assert "b/a.py" in final_patch
    assert "b/b.py" in final_patch
    file_patches = list((run_dir / "implementation" / "file_patches").glob("*.patch.diff"))
    assert len(file_patches) == 2
    workflow_events = [
        json.loads(line)
        for line in (run_dir / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert (
        sum(
            1
            for event in workflow_events
            if event.get("event_type") == "incremental_file_patch_applied"
        )
        == 2
    )
    assert not any(
        event.get("event_type") == "incremental_patch_decision_requested"
        for event in workflow_events
    )
    assert not any(
        event.get("event_type") == "incremental_patch_context_requested"
        for event in workflow_events
    )
    assert (
        sum(
            1
            for event in workflow_events
            if event.get("event_type") == "incremental_stage_patch_context_built"
        )
        == 1
    )
    assert (
        sum(
            1
            for event in workflow_events
            if event.get("event_type") == "incremental_stage_patch_context_reused"
        )
        == 2
    )
    assert (
        sum(
            1
            for event in workflow_events
            if event.get("event_type")
            == "incremental_aggregate_patch_artifacts_written"
        )
        == 2
    )
    assert (run_dir / "implementation" / "stage_patch_context.md").exists()
    applied_context = (
        run_dir / "implementation" / "applied_file_context.md"
    ).read_text(encoding="utf-8")
    assert "VALUE = 1" in applied_context
    decisions = [
        json.loads(line)
        for line in (run_dir / "decision_trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    single_file_decisions = [
        event
        for event in decisions
        if event.get("comment", "").startswith("Generated single-file patch for")
    ]
    assert [event["auto"] for event in single_file_decisions] == [True, True]


def test_incremental_patch_approval_can_auto_approve_rest_of_stage(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [implement]
project_path: {project.as_posix()}
output_dir: {output_dir.as_posix()}
permissions:
  approval_mode: manual
""".strip(),
        encoding="utf-8",
    )

    class IncrementalImplementationPlanner:
        def create_implementation_request(self, _context) -> ImplementationRequest:
            return ImplementationRequest(
                plan=ImplementationPlan(
                    requirements_summary="Add two files.",
                    implementation_strategy="Write both files incrementally.",
                    changes=[
                        ImplementationFileChange(path="a.py", rationale="First file."),
                        ImplementationFileChange(path="b.py", rationale="Second file."),
                    ],
                    acceptance_criteria=["Both files are present."],
                ),
                approval=ApprovalDecision(
                    interrupt_id=PATCH_INTERRUPT_ID,
                    decision_type="approve",
                    auto=False,
                    decided_by="test",
                ),
            )

        def select_patch_file_context(
            self,
            _context,
            *,
            stage,
            plan,
            target_path,
            workspace_tree,
            work_summary,
            completed_files,
            failed_attempts,
            feedback=None,
        ):
            raise AssertionError("per-file LLM context selection should not be called")

        def create_implementation_file_patch_draft(
            self,
            _context,
            _plan,
            *,
            target_path,
            workspace_context,
            work_summary,
            completed_files,
            failed_attempts,
            feedback=None,
        ) -> ImplementationPatchDraft:
            if Path(target_path).as_posix() == "a.py":
                return _implementation_draft("a.py", "VALUE = 1\n")
            return _implementation_draft("b.py", "VALUE = 2\n")

    class AutoRestApprovalConsole:
        prompts: list[str] = []

        def prompt(self, request):
            self.prompts.append(request.interrupt_id)
            if request.action == "review_implementation_plan":
                return ApprovalDecision(
                    interrupt_id=request.interrupt_id,
                    decision_type="approve",
                    auto=False,
                    decided_by="user",
                )
            if request.action == "approve_implementation_patch":
                assert request.allowed_decisions == ("approve", "respond")
                return ApprovalDecision(
                    interrupt_id=request.interrupt_id,
                    decision_type="approve",
                    edited_payload={PATCH_AUTO_APPROVE_REMAINING_KEY: True},
                    comment="Apply and stop prompting for this stage.",
                    auto=False,
                    decided_by="user",
                )
            raise AssertionError(f"unexpected approval prompt: {request.action}")

    monkeypatch.setattr(
        "codeagent.cli.executor.PlanGenerationService",
        IncrementalImplementationPlanner,
    )
    monkeypatch.setattr("codeagent.cli.executor.ApprovalConsole", AutoRestApprovalConsole)

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    assert (project / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (project / "b.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert AutoRestApprovalConsole.prompts == [
        PLAN_INTERRUPT_ID,
        PATCH_INTERRUPT_ID,
    ]
    assert "auto-approved" in result.output
    assert "目标文件：b.py (b.py)" in result.output
    assert "file:///" not in result.output
    assert "]8;;" not in result.output
    auto_lines = [
        line for line in result.output.splitlines() if "auto-approved" in line
    ]
    assert auto_lines
    assert all(".patch.diff" not in line for line in auto_lines)
    assert all("file_patches" not in line for line in auto_lines)
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    decisions = [
        json.loads(line)
        for line in (run_dir / "decision_trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    patch_decisions = [
        event
        for event in decisions
        if event.get("action") == "approve_implementation_patch"
    ]
    assert patch_decisions[0]["edited_payload"] == {
        PATCH_AUTO_APPROVE_REMAINING_KEY: True
    }
    assert patch_decisions[1]["auto"] is True
    assert patch_decisions[1]["decision_source"] == "stage_patch_auto_approve"


def test_incremental_patch_retry_reports_reason_to_cli(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [implement]
project_path: {project.as_posix()}
output_dir: {output_dir.as_posix()}
permissions:
  approval_mode: auto
""".strip(),
        encoding="utf-8",
    )

    class RetryingImplementationPlanner:
        calls = 0

        def create_implementation_request(self, _context) -> ImplementationRequest:
            return ImplementationRequest(
                plan=ImplementationPlan(
                    requirements_summary="Update app value.",
                    implementation_strategy="Modify app.py incrementally.",
                    changes=[
                        ImplementationFileChange(
                            path="app.py",
                            rationale="Update the visible value.",
                        )
                    ],
                    acceptance_criteria=["app.py has the updated value."],
                ),
                approval=ApprovalDecision(
                    interrupt_id=PATCH_INTERRUPT_ID,
                    decision_type="approve",
                    auto=True,
                    decided_by="test",
                ),
            )

        def select_patch_file_context(
            self,
            _context,
            *,
            stage,
            plan,
            target_path,
            workspace_tree,
            work_summary,
            completed_files,
            failed_attempts,
            feedback=None,
        ):
            raise AssertionError("per-file LLM context selection should not be called")

        def create_implementation_file_patch_draft(
            self,
            _context,
            _plan,
            *,
            target_path,
            workspace_context,
            work_summary,
            completed_files,
            failed_attempts,
            feedback=None,
        ) -> ImplementationPatchDraft:
            type(self).calls += 1
            old_content = "WRONG = 0\n" if type(self).calls == 1 else "VALUE = 0\n"
            return ImplementationPatchDraft(
                plan_summary="Update app.py.",
                changes=[
                    ImplementationPatchFileChange(
                        path=target_path,
                        old_content=old_content,
                        new_content="VALUE = 1\n",
                        rationale="Update the visible value.",
                    )
                ],
                syntax_check_targets=["app.py"],
            )

    monkeypatch.setattr(
        "codeagent.cli.executor.PlanGenerationService",
        RetryingImplementationPlanner,
    )

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    assert (project / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert RetryingImplementationPlanner.calls == 2
    assert "单文件补丁 app.py 第 1 次未通过" in result.output
    assert "补丁应用失败" in result.output
    assert "正在重新生成当前文件" in result.output
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    events = [
        json.loads(line)
        for line in (run_dir / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    retry_events = [
        event
        for event in events
        if event.get("event_type") == "incremental_file_patch_retry"
    ]
    assert retry_events
    assert retry_events[0]["reason"] == "补丁应用失败"


def test_manual_implementation_prompts_for_plan_before_patch(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [implement]
project_path: {project.as_posix()}
output_dir: {output_dir.as_posix()}
permissions:
  approval_mode: manual
""".strip(),
        encoding="utf-8",
    )
    _ManualApprovalPlanGenerationService.implementation_plan = _implementation_plan(
        "feature.py",
        strategy="Create feature.py with a public constant.",
    )
    _ManualApprovalPlanGenerationService.implementation_patch_draft = _implementation_draft(
        "feature.py",
        'VERSION = "1.0"\n',
    )
    _ScriptedApprovalConsole.decisions = [
        ApprovalDecision(
            interrupt_id=PLAN_INTERRUPT_ID,
            decision_type="approve",
            comment="计划可以执行。",
            auto=False,
            decision_source="user",
            presented_to_user=True,
        ),
        ApprovalDecision(
            interrupt_id=PATCH_INTERRUPT_ID,
            decision_type="approve",
            comment="补丁可以应用。",
            auto=False,
            decision_source="user",
            presented_to_user=True,
        ),
    ]
    monkeypatch.setattr(
        "codeagent.cli.executor.PlanGenerationService",
        _ManualApprovalPlanGenerationService,
    )
    monkeypatch.setattr("codeagent.cli.executor.ApprovalConsole", _ScriptedApprovalConsole)

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    assert (project / "feature.py").exists()
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    decisions = [
        json.loads(line)
        for line in (run_dir / "decision_trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert [event["action"] for event in decisions] == [
        "review_implementation_plan",
        "approve_implementation_patch",
    ]
    assert decisions[0]["presented_to_user"] is True


def test_run_config_auto_approval_records_user_configured_source(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [implement]
project_path: {project.as_posix()}
output_dir: {output_dir.as_posix()}
permissions:
  approval_mode: auto
""".strip(),
        encoding="utf-8",
    )
    _ManualApprovalPlanGenerationService.implementation_plan = _implementation_plan(
        "feature.py",
        strategy="Create feature.py with a public constant.",
    )
    _ManualApprovalPlanGenerationService.implementation_patch_draft = _implementation_draft(
        "feature.py",
        'VERSION = "1.0"\n',
    )
    monkeypatch.setattr(
        "codeagent.cli.executor.PlanGenerationService",
        _ManualApprovalPlanGenerationService,
    )

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    decisions = [
        json.loads(line)
        for line in (run_dir / "decision_trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    approval = next(event for event in decisions if event["type"] == "human_decision")
    assert approval["event_type"] == "approval_decision"
    assert approval["auto"] is True
    assert approval["decision_source"] == "user_configured_auto"
    assert approval["presented_to_user"] is False
    assert (run_dir / "workflow.log").exists()
    assert (run_dir / "workflow_events.jsonl").exists()


def test_testing_plan_response_regenerates_tests_without_entering_debug(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "math_utils.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [test, debug, repair]
project_path: {project.as_posix()}
output_dir: {output_dir.as_posix()}
test_command:
  command: python -m pytest tests/test_math_utils.py -q
permissions:
  approval_mode: manual
""".strip(),
        encoding="utf-8",
    )

    class FeedbackTestingPlanGenerationService:
        calls: list[str | None] = []
        draft_content: str = "def test_placeholder():\n    assert True\n"

        def create_testing_request(self, _context, *, feedback: str | None = None):
            self.calls.append(feedback)
            if feedback:
                self.draft_content = (
                    "from math_utils import add\n\n"
                    "def test_add_positive_numbers():\n"
                    "    assert add(2, 3) == 5\n\n"
                    "def test_add_zero_boundary():\n"
                    "    assert add(0, 4) == 4\n"
                )
            else:
                self.draft_content = "def test_placeholder():\n    assert True\n"
            return TestingRequest(
                plan=_testing_plan(
                    "tests/test_math_utils.py",
                    command="python -m pytest tests/test_math_utils.py -q",
                ),
                plan_review=ApprovalDecision(
                    interrupt_id=TEST_PLAN_INTERRUPT_ID,
                    decision_type="approve",
                    auto=False,
                ),
                patch_approval=ApprovalDecision(
                    interrupt_id=TEST_PATCH_INTERRUPT_ID,
                    decision_type="approve",
                    auto=False,
                ),
                command_approval=ApprovalDecision(
                    interrupt_id=TEST_COMMAND_INTERRUPT_ID,
                    decision_type="approve",
                    auto=False,
                ),
            )

        def create_testing_patch_draft(self, _context, _plan, *, feedback: str | None = None):
            return _testing_draft(
                "tests/test_math_utils.py",
                self.draft_content,
                command="python -m pytest tests/test_math_utils.py -q",
            )

    _ScriptedApprovalConsole.decisions = [
        ApprovalDecision(
            interrupt_id=TEST_PLAN_INTERRUPT_ID,
            decision_type="respond",
            comment="请补充边界测试，不要只做占位测试。",
            auto=False,
            decision_source="user",
            presented_to_user=True,
        ),
        ApprovalDecision(
            interrupt_id=TEST_PLAN_INTERRUPT_ID,
            decision_type="approve",
            auto=False,
            decision_source="user",
            presented_to_user=True,
        ),
        ApprovalDecision(
            interrupt_id=TEST_PATCH_INTERRUPT_ID,
            decision_type="approve",
            auto=False,
            decision_source="user",
            presented_to_user=True,
        ),
        ApprovalDecision(
            interrupt_id=TEST_COMMAND_INTERRUPT_ID,
            decision_type="approve",
            auto=False,
            decision_source="user",
            presented_to_user=True,
        ),
    ]
    monkeypatch.setattr(
        "codeagent.cli.executor.PlanGenerationService",
        FeedbackTestingPlanGenerationService,
    )
    monkeypatch.setattr("codeagent.cli.executor.ApprovalConsole", _ScriptedApprovalConsole)

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    assert (project / "tests" / "test_math_utils.py").read_text(
        encoding="utf-8"
    ).count("def test_") == 2
    decisions = [
        json.loads(line)
        for line in (run_dir / "decision_trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert [event["decision_type"] for event in decisions[:4]] == [
        "respond",
        "approve",
        "approve",
        "approve",
    ]
    assert FeedbackTestingPlanGenerationService.calls == [
        None,
        "请补充边界测试，不要只做占位测试。",
    ]
    assert not (run_dir / "debugging" / "stage_result.json").exists()
    workflow_log = (run_dir / "workflow.log").read_text(encoding="utf-8")
    assert "approval_feedback_regeneration" in workflow_log


def test_incremental_testing_applies_file_patches_before_running_generated_tests(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "math_utils.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [test]
project_path: {project.as_posix()}
output_dir: {output_dir.as_posix()}
test_command:
  command: python -m pytest tests -q
permissions:
  approval_mode: auto
""".strip(),
        encoding="utf-8",
    )

    class IncrementalTestingPlanner:
        def create_testing_request(self, _context) -> TestingRequest:
            return TestingRequest(
                plan=TestingPlan(
                    target_summary="Generate visible math tests.",
                    strategy="Write two independent test files.",
                    acceptance_criteria=["Generated tests are collected and pass."],
                    changes=[
                        TestFileChange(
                            path="tests/test_math_add.py",
                            test_focus="Positive addition.",
                            rationale="Cover the main addition path.",
                        ),
                        TestFileChange(
                            path="tests/test_math_zero.py",
                            test_focus="Zero boundary.",
                            rationale="Cover zero as a boundary value.",
                        ),
                    ],
                    command="python -m pytest tests -q",
                    framework="pytest",
                ),
                plan_review=ApprovalDecision(
                    interrupt_id=TEST_PLAN_INTERRUPT_ID,
                    decision_type="approve",
                    auto=True,
                    decided_by="test",
                ),
                patch_approval=ApprovalDecision(
                    interrupt_id=TEST_PATCH_INTERRUPT_ID,
                    decision_type="approve",
                    auto=True,
                    decided_by="test",
                ),
                command_approval=ApprovalDecision(
                    interrupt_id=TEST_COMMAND_INTERRUPT_ID,
                    decision_type="approve",
                    auto=True,
                    decided_by="test",
                ),
            )

        def select_patch_file_context(
            self,
            _context,
            *,
            stage,
            plan,
            target_path,
            workspace_tree,
            work_summary,
            completed_files,
            failed_attempts,
            feedback=None,
        ):
            raise AssertionError("per-file LLM context selection should not be called")

        def create_testing_file_patch_draft(
            self,
            _context,
            _plan,
            *,
            target_path,
            workspace_context,
            work_summary,
            completed_files,
            failed_attempts,
            feedback=None,
        ) -> TestingPatchDraft:
            if Path(target_path).as_posix().endswith("test_math_add.py"):
                assert "def add(left: int, right: int) -> int:" in workspace_context
                return _testing_draft(
                    "tests/test_math_add.py",
                    "from math_utils import add\n\n\ndef test_add_positive_numbers():\n    assert add(2, 3) == 5\n",
                    command="python -m pytest tests -q",
                )
            assert "test_add_positive_numbers" in workspace_context
            return _testing_draft(
                "tests/test_math_zero.py",
                "from math_utils import add\n\n\ndef test_add_zero_boundary():\n    assert add(0, 4) == 4\n",
                command="python -m pytest tests/test_math_zero.py -q",
            )

    monkeypatch.setattr(
        "codeagent.cli.executor.PlanGenerationService",
        IncrementalTestingPlanner,
    )

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    assert (project / "tests" / "test_math_add.py").exists()
    assert (project / "tests" / "test_math_zero.py").exists()
    stage_result = json.loads(
        (run_dir / "testing" / "stage_result.json").read_text(encoding="utf-8")
    )
    assert stage_result["status"] == "succeeded"
    test_command = json.loads(
        (run_dir / "testing" / "test_command.json").read_text(encoding="utf-8")
    )
    assert test_command["command"] == "python -m pytest tests -q"
    test_result = json.loads(
        (run_dir / "testing" / "test_result.json").read_text(encoding="utf-8")
    )
    assert test_result["total"] == 2
    file_patches = list((run_dir / "testing" / "file_patches").glob("*.patch.diff"))
    assert len(file_patches) == 2
    workflow_events = [
        json.loads(line)
        for line in (run_dir / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert not any(
        event.get("event_type") == "incremental_patch_context_requested"
        for event in workflow_events
    )
    assert (
        sum(
            1
            for event in workflow_events
            if event.get("event_type") == "incremental_stage_patch_context_built"
        )
        == 1
    )
    assert "test_add_positive_numbers" in (
        run_dir / "testing" / "applied_file_context.md"
    ).read_text(encoding="utf-8")


def test_implement_subcommand_classifies_stage_runtime_errors_separately_from_model(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = tmp_path / "requirements.md"
    requirements.write_text("Add a tiny feature.\n", encoding="utf-8")
    output_dir = tmp_path / "runs"
    _FakePlanGenerationService.implementation_plan = _implementation_plan(
        "feature.py",
        strategy="Create feature.py with a public constant.",
    )
    _FakePlanGenerationService.implementation_patch_draft = _implementation_draft(
        "feature.py",
        'VERSION = "1.0"\n',
    )
    monkeypatch.setattr(
        "codeagent.cli.executor.PlanGenerationService",
        _FakePlanGenerationService,
    )
    monkeypatch.setattr(
        "codeagent.cli.executor.ImplementationService",
        _FailingImplementationService,
    )

    result = runner.invoke(
        app,
        [
            "implement",
            "--project",
            str(project),
            "--requirements",
            str(requirements),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 1
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    stage_result = json.loads(
        (run_dir / "implementation" / "stage_result.json").read_text(encoding="utf-8")
    )
    assert stage_result["status"] == "failed"
    assert stage_result["error"]["category"] == "tool"
    assert "Implementation stage execution failed." in stage_result["summary"]
    assert "synthetic implementation service failure" in stage_result["error"]["message"]


def test_test_debug_repair_run_generates_repair_plan_and_fixes_project(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    (project / "gcd.py").write_text(
        "def gcd(a, b):\n"
        "    if b == 0:\n"
        "        return a\n"
        "    return gcd(a % b, b)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_gcd.py").write_text(
        "\n".join(
            [
                "import unittest",
                "from gcd import gcd",
                "",
                "class GcdTest(unittest.TestCase):",
                "    def test_gcd(self):",
                "        self.assertEqual(gcd(13, 13), 13)",
                "",
                "if __name__ == '__main__':",
                "    unittest.main()",
            ]
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "runs"
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [test, debug, repair]
project_path: {project.as_posix()}
output_dir: {output_dir.as_posix()}
test_framework: unittest
test_command:
  command: "python -m unittest discover -s tests"
""".strip(),
        encoding="utf-8",
    )
    _FakePlanGenerationService.repair_plan = _repair_plan(
        "gcd.py",
        command="python -m unittest discover -s tests",
        framework="unittest",
    )
    _FakePlanGenerationService.repair_patch_draft = _repair_draft(
        "gcd.py",
        (
            "def gcd(a, b):\n"
            "    while b:\n"
            "        a, b = b, a % b\n"
            "    return a\n"
        ),
        old_content=(
            "def gcd(a, b):\n"
            "    if b == 0:\n"
            "        return a\n"
            "    return gcd(a % b, b)\n"
        ),
        command="python -m unittest discover -s tests",
        framework="unittest",
    )
    monkeypatch.setattr(
        "codeagent.cli.executor.PlanGenerationService",
        _FakePlanGenerationService,
    )

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "[最终结果] 成功" in result.output
    repaired_source = (project / "gcd.py").read_text(encoding="utf-8")
    assert "while b:" in repaired_source
    assert "a, b = b, a % b" in repaired_source
    run_dir = next(path for path in output_dir.iterdir() if path.is_dir())
    repair_result = json.loads(
        (run_dir / "repair" / "stage_result.json").read_text(encoding="utf-8")
    )
    assert repair_result["status"] == "succeeded"
