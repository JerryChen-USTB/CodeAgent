from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from codeagent.cli.app import app
from codeagent.cli.executor import _failure_logs_from_config
from codeagent.config.schema import InputMaterial, Stage, TaskConfig
from codeagent.runtime.run_context import create_run_context
from codeagent.stages.implementation_service import (
    ImplementationFileChange,
    ImplementationPlan,
    ImplementationRequest,
    PATCH_INTERRUPT_ID,
    PLAN_INTERRUPT_ID,
)
from codeagent.stages.repair_service import (
    REPAIR_COMMAND_INTERRUPT_ID,
    REPAIR_PATCH_INTERRUPT_ID,
    RepairFileChange,
    RepairPlan,
    RepairRequest,
)
from codeagent.stages.testing_service import (
    TEST_COMMAND_INTERRUPT_ID,
    TEST_PATCH_INTERRUPT_ID,
    TEST_PLAN_INTERRUPT_ID,
    TestFileChange,
    TestingPlan,
    TestingRequest,
)
from codeagent.tools.hitl import ApprovalDecision


runner = CliRunner()


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
    repair_plan: RepairPlan | None = None
    testing_plan: TestingPlan | None = None

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

    def create_repair_request(self, _context) -> RepairRequest:
        assert self.repair_plan is not None
        return RepairRequest(
            plan=self.repair_plan,
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

    def create_testing_request(self, context) -> TestingRequest:
        plan = self.testing_plan or _generated_testing_plan_for_context(context)
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


def _generated_testing_plan_for_context(context) -> TestingPlan:
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
    return TestingPlan(
        target_summary="Generated visible regression tests.",
        strategy="Add a smoke test so the testing stage executes real discovered tests.",
        acceptance_criteria=["At least one generated visible test is collected and executed."],
        changes=[
            TestFileChange(
                path="tests/test_codeagent_generated.py",
                old_content=None,
                new_content=content,
                rationale="Exercise the testing pipeline without hidden benchmark material.",
            )
        ],
        command=command,
        framework=framework,
    )


class _FailingImplementationService:
    def __init__(self, *, run_context) -> None:
        self.run_context = run_context

    def run(self, _request) -> None:
        raise RuntimeError("synthetic implementation service failure")


class _ManualApprovalPlanGenerationService:
    implementation_plan: ImplementationPlan | None = None

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
    _FakePlanGenerationService.implementation_plan = ImplementationPlan(
        requirements_summary="Add a version constant.",
        impact_summary="Create feature.py with a public constant.",
        changes=[
            ImplementationFileChange(
                path="feature.py",
                old_content=None,
                new_content='VERSION = "1.0"\n',
                rationale="Required by the visible requirements.",
            )
        ],
        syntax_check_targets=["feature.py"],
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
    _ManualApprovalPlanGenerationService.implementation_plan = ImplementationPlan(
        requirements_summary="Add a version constant.",
        impact_summary="Create feature.py with a public constant.",
        changes=[
            ImplementationFileChange(
                path="feature.py",
                old_content=None,
                new_content='VERSION = "1.0"\n',
                rationale="Required by the visible requirements.",
            )
        ],
        syntax_check_targets=["feature.py"],
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
    _ManualApprovalPlanGenerationService.implementation_plan = ImplementationPlan(
        requirements_summary="Add a version constant.",
        impact_summary="Create feature.py with a public constant.",
        changes=[
            ImplementationFileChange(
                path="feature.py",
                old_content=None,
                new_content='VERSION = "1.0"\n',
                rationale="Required by the visible requirements.",
            )
        ],
        syntax_check_targets=["feature.py"],
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

        def create_testing_request(self, _context, *, feedback: str | None = None):
            self.calls.append(feedback)
            if feedback:
                content = (
                    "from math_utils import add\n\n"
                    "def test_add_positive_numbers():\n"
                    "    assert add(2, 3) == 5\n\n"
                    "def test_add_zero_boundary():\n"
                    "    assert add(0, 4) == 4\n"
                )
            else:
                content = "def test_placeholder():\n    assert True\n"
            return TestingRequest(
                plan=TestingPlan(
                    target_summary="Exercise math_utils.add.",
                    strategy="Generate visible pytest tests.",
                    acceptance_criteria=["Generated tests must be collected."],
                    changes=[
                        TestFileChange(
                            path="tests/test_math_utils.py",
                            old_content=None,
                            new_content=content,
                            rationale="Visible Agent self-test.",
                        )
                    ],
                    command="python -m pytest tests/test_math_utils.py -q",
                    framework="pytest",
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


def test_implement_subcommand_classifies_stage_runtime_errors_separately_from_model(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = tmp_path / "requirements.md"
    requirements.write_text("Add a tiny feature.\n", encoding="utf-8")
    output_dir = tmp_path / "runs"
    _FakePlanGenerationService.implementation_plan = ImplementationPlan(
        requirements_summary="Add a version constant.",
        impact_summary="Create feature.py with a public constant.",
        changes=[
            ImplementationFileChange(
                path="feature.py",
                old_content=None,
                new_content='VERSION = "1.0"\n',
                rationale="Required by the visible requirements.",
            )
        ],
        syntax_check_targets=["feature.py"],
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
    _FakePlanGenerationService.repair_plan = RepairPlan(
        root_cause="Recursive gcd call does not reduce the second argument.",
        strategy="Use Euclid's recursive step gcd(b, a % b).",
        changes=[
            RepairFileChange(
                path="gcd.py",
                old_content=(
                    "def gcd(a, b):\n"
                    "    if b == 0:\n"
                    "        return a\n"
                    "    return gcd(a % b, b)\n"
                ),
                new_content=(
                    "def gcd(a, b):\n"
                    "    while b:\n"
                    "        a, b = b, a % b\n"
                    "    return a\n"
                ),
                rationale="Iterative Euclid update makes progress until b is zero.",
            )
        ],
        verification_command="python -m unittest discover -s tests",
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
