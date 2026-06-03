from __future__ import annotations

import json
from pathlib import Path

from codeagent.agents.plan_generation import PlanGenerationService
from codeagent.config.schema import InputMaterial, Stage, TaskConfig
from codeagent.runtime.run_context import RunContext, create_run_context
from codeagent.stages.implementation_service import PATCH_INTERRUPT_ID
from codeagent.stages.repair_service import (
    REPAIR_COMMAND_INTERRUPT_ID,
    REPAIR_PATCH_INTERRUPT_ID,
)


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeModel:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> _Response:
        self.prompts.append(prompt)
        return _Response("```json\n" + json.dumps(self.response) + "\n```")


class _FakeFactory:
    def __init__(self, model: _FakeModel) -> None:
        self.model = model

    def create(self, _config):
        return self.model


def _context(
    tmp_path: Path,
    *,
    stages: list[Stage],
    input_materials: list[InputMaterial] | None = None,
    hidden_paths: list[Path] | None = None,
) -> RunContext:
    project = tmp_path / "case" / "workspace"
    project.mkdir(parents=True)
    config = TaskConfig(
        stages=stages,
        project_path=project,
        output_dir=tmp_path / "runs",
        mode="benchmark",
        test_framework="unittest",
        test_command={"command": "python -m unittest discover -s tests"},
        input_materials=input_materials or [],
        agent_visibility={
            "visible_paths": [tmp_path / "case" / "input", project],
            "hidden_paths": hidden_paths or [],
        },
        auto_approve_in_benchmark=True,
    )
    return create_run_context(config, output_root=tmp_path / "runs")


def test_plan_generation_builds_implementation_request_without_hidden_context(tmp_path) -> None:
    case_dir = tmp_path / "case"
    input_dir = case_dir / "input"
    input_dir.mkdir(parents=True)
    requirements = input_dir / "requirements.md"
    requirements.write_text("Implement add(left, right).\n", encoding="utf-8")
    hidden_dir = case_dir / "evaluation"
    hidden_dir.mkdir()
    (hidden_dir / "test_secret.py").write_text("SECRET_ORACLE = True\n", encoding="utf-8")
    context = _context(
        tmp_path,
        stages=[Stage.IMPLEMENT],
        input_materials=[
            InputMaterial(
                material_type="requirements",
                path=requirements,
                required=True,
            )
        ],
        hidden_paths=[hidden_dir],
    )
    (context.task_config.project_path / "solution.py").write_text(
        "def add(left, right):\n    pass\n",
        encoding="utf-8",
    )
    model = _FakeModel(
        {
            "requirements_summary": "Implement add.",
            "impact_summary": "Fill in solution.py.",
            "changes": [
                {
                    "path": "solution.py",
                    "old_content": "def add(left, right):\n    pass\n",
                    "new_content": "def add(left, right):\n    return left + right\n",
                    "rationale": "Satisfy visible requirements.",
                }
            ],
            "syntax_check_targets": ["solution.py"],
        }
    )

    request = PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
        context
    )

    prompt = model.prompts[0]
    assert "Implement add(left, right)." in prompt
    assert "solution.py" in prompt
    assert "SECRET_ORACLE" not in prompt
    assert "test_secret.py" not in prompt
    assert request.plan.changes[0].path.as_posix() == "solution.py"
    assert request.approval.interrupt_id == PATCH_INTERRUPT_ID
    assert request.approval.decision_type == "approve"
    assert request.approval.auto is True


def test_plan_generation_keeps_visible_context_inside_generated_run_roots(
    tmp_path,
) -> None:
    case_dir = tmp_path / "codeagent_runs" / "benchmark" / "case_workspaces" / "case_a"
    input_dir = case_dir / "input"
    project = case_dir / "workspace"
    input_dir.mkdir(parents=True)
    project.mkdir()
    requirements = input_dir / "requirements.md"
    requirements.write_text("Implement visible benchmark requirement.\n", encoding="utf-8")
    (project / "solution.py").write_text("def solve():\n    pass\n", encoding="utf-8")
    config = TaskConfig(
        stages=[Stage.IMPLEMENT],
        project_path=project,
        output_dir=tmp_path / "runs",
        mode="benchmark",
        input_materials=[
            InputMaterial(
                material_type="requirements",
                path=requirements,
                required=True,
            )
        ],
        agent_visibility={
            "visible_paths": [input_dir, project],
            "hidden_paths": [],
        },
        auto_approve_in_benchmark=True,
    )
    context = create_run_context(config, output_root=tmp_path / "runs")
    model = _FakeModel(
        {
            "requirements_summary": "Implement solve.",
            "impact_summary": "Update solution.py.",
            "changes": [
                {
                    "path": "solution.py",
                    "old_content": "def solve():\n    pass\n",
                    "new_content": "def solve():\n    return True\n",
                    "rationale": "Satisfy visible requirement.",
                }
            ],
            "syntax_check_targets": ["solution.py"],
        }
    )

    PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
        context
    )

    assert "Implement visible benchmark requirement." in model.prompts[0]
    assert "def solve()" in model.prompts[0]


def test_plan_generation_honors_configured_context_budget(tmp_path) -> None:
    case_dir = tmp_path / "case"
    input_dir = case_dir / "input"
    project = case_dir / "workspace"
    input_dir.mkdir(parents=True)
    project.mkdir()
    requirements = input_dir / "requirements.md"
    requirements.write_text("A" * 200, encoding="utf-8")
    config = TaskConfig(
        stages=[Stage.IMPLEMENT],
        project_path=project,
        output_dir=tmp_path / "runs",
        mode="benchmark",
        input_materials=[
            InputMaterial(
                material_type="requirements",
                path=requirements,
                required=True,
            )
        ],
        agent_visibility={
            "visible_paths": [input_dir, project],
            "hidden_paths": [],
        },
        auto_approve_in_benchmark=True,
    )
    context = create_run_context(config, output_root=tmp_path / "runs")
    model = _FakeModel(
        {
            "requirements_summary": "Budgeted implementation.",
            "impact_summary": "Create solution.py.",
            "changes": [
                {
                    "path": "solution.py",
                    "old_content": None,
                    "new_content": "VALUE = True\n",
                    "rationale": "Satisfy visible requirement.",
                }
            ],
            "syntax_check_targets": ["solution.py"],
        }
    )

    PlanGenerationService(
        model_factory=_FakeFactory(model),
        max_context_chars=40,
    ).create_implementation_request(context)

    assert "A" * 80 not in model.prompts[0]
    assert "[truncated]" in model.prompts[0]


def test_plan_generation_builds_repair_request_with_failure_logs(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.TEST, Stage.DEBUG, Stage.REPAIR])
    (context.task_config.project_path / "gcd.py").write_text(
        "def gcd(a, b):\n    return gcd(a % b, b)\n",
        encoding="utf-8",
    )
    logs_dir = context.stage_dirs[Stage.TEST] / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "testing_cli_command.stderr.log").write_text(
        "RecursionError: maximum recursion depth exceeded\n",
        encoding="utf-8",
    )
    model = _FakeModel(
        {
            "root_cause": "The recursive call does not reduce the second argument.",
            "strategy": "Use Euclid recursion gcd(b, a % b).",
            "changes": [
                {
                    "path": "gcd.py",
                    "old_content": "def gcd(a, b):\n    return gcd(a % b, b)\n",
                    "new_content": (
                        "def gcd(a, b):\n"
                        "    if b == 0:\n"
                        "        return a\n"
                        "    return gcd(b, a % b)\n"
                    ),
                    "rationale": "Ensure each recursive call reduces b.",
                }
            ],
            "verification_command": "python -m unittest discover -s tests",
            "framework": "unittest",
        }
    )

    request = PlanGenerationService(model_factory=_FakeFactory(model)).create_repair_request(
        context
    )

    prompt = model.prompts[0]
    assert "RecursionError" in prompt
    assert "gcd.py" in prompt
    assert request.plan.changes[0].path.as_posix() == "gcd.py"
    assert request.patch_approval.interrupt_id == REPAIR_PATCH_INTERRUPT_ID
    assert request.command_approval.interrupt_id == REPAIR_COMMAND_INTERRUPT_ID
    assert request.patch_approval.auto is True
    assert request.command_approval.auto is True


def test_plan_generation_normalizes_model_paths_to_project_root_relative(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.TEST, Stage.DEBUG, Stage.REPAIR])
    (context.task_config.project_path / "calc.py").write_text(
        "def add(left, right):\n    return left - right\n",
        encoding="utf-8",
    )
    model = _FakeModel(
        {
            "root_cause": "The implementation subtracts instead of adding.",
            "strategy": "Change the operator in calc.py.",
            "changes": [
                {
                    "path": "workspace/calc.py",
                    "old_content": "def add(left, right):\n    return left - right\n",
                    "new_content": "def add(left, right):\n    return left + right\n",
                    "rationale": "Match the add function contract.",
                }
            ],
            "verification_command": "python -m unittest discover -s tests",
            "framework": "unittest",
        }
    )

    request = PlanGenerationService(model_factory=_FakeFactory(model)).create_repair_request(
        context
    )

    assert request.plan.changes[0].path.as_posix() == "calc.py"
