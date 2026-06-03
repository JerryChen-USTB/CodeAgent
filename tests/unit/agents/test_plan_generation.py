from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from codeagent.agents.plan_generation import PlanGenerationError, PlanGenerationService
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


class _SequenceModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> _Response:
        self.prompts.append(prompt)
        return _Response(self.responses.pop(0))


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


def test_plan_generation_prompt_requires_sqlite_connections_to_be_closed(
    tmp_path,
) -> None:
    case_dir = tmp_path / "case"
    input_dir = case_dir / "input"
    project = case_dir / "workspace"
    input_dir.mkdir(parents=True)
    project.mkdir()
    requirements = input_dir / "requirements.md"
    requirements.write_text(
        "Build a Flask API with SQLite persistence.\n",
        encoding="utf-8",
    )
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
            "requirements_summary": "Build SQLite API.",
            "impact_summary": "Add package.",
            "changes": [
                {
                    "path": "meeting_room_booking/__init__.py",
                    "old_content": None,
                    "new_content": "VALUE = True\n",
                    "rationale": "Satisfy visible requirement.",
                }
            ],
            "syntax_check_targets": ["meeting_room_booking/__init__.py"],
        }
    )

    PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
        context
    )

    prompt = model.prompts[0]
    assert "SQLite" in prompt
    assert "close" in prompt
    assert "context manager alone does not close sqlite3.Connection" in prompt


def test_plan_generation_strips_duplicate_workspace_prefix_for_empty_project_root(
    tmp_path,
) -> None:
    case_dir = tmp_path / "case"
    input_dir = case_dir / "input"
    project = case_dir / "workspace"
    input_dir.mkdir(parents=True)
    project.mkdir()
    requirements = input_dir / "requirements.md"
    requirements.write_text("Create meeting_room_booking package.\n", encoding="utf-8")
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
            "requirements_summary": "Create package.",
            "impact_summary": "Add Flask app package.",
            "changes": [
                {
                    "path": "workspace/meeting_room_booking/app.py",
                    "old_content": None,
                    "new_content": "def create_app():\n    return None\n",
                    "rationale": "Satisfy visible package requirement.",
                }
            ],
            "syntax_check_targets": ["workspace/meeting_room_booking/app.py"],
        }
    )

    request = PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
        context
    )

    assert request.plan.changes[0].path.as_posix() == "meeting_room_booking/app.py"
    assert request.plan.syntax_check_targets[0].as_posix() == "meeting_room_booking/app.py"


def test_plan_generation_preserves_existing_directory_named_like_project_root(
    tmp_path,
) -> None:
    case_dir = tmp_path / "case"
    input_dir = case_dir / "input"
    project = case_dir / "workspace"
    package_dir = project / "workspace"
    input_dir.mkdir(parents=True)
    package_dir.mkdir(parents=True)
    requirements = input_dir / "requirements.md"
    requirements.write_text("Update the nested workspace package.\n", encoding="utf-8")
    (package_dir / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
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
            "requirements_summary": "Update package file.",
            "impact_summary": "Modify the existing nested workspace package.",
            "changes": [
                {
                    "path": "workspace/pkg.py",
                    "old_content": "VALUE = 1\n",
                    "new_content": "VALUE = 2\n",
                    "rationale": "Satisfy visible package requirement.",
                }
            ],
            "syntax_check_targets": ["workspace/pkg.py"],
        }
    )

    request = PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
        context
    )

    assert request.plan.changes[0].path.as_posix() == "workspace/pkg.py"
    assert request.plan.syntax_check_targets[0].as_posix() == "workspace/pkg.py"


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


def test_plan_generation_repair_prompt_discovers_shortened_shell_logs(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.TEST, Stage.DEBUG, Stage.REPAIR])
    (context.task_config.project_path / "find_in_sorted.py").write_text(
        "def find_in_sorted(arr, x):\n    return find_in_sorted(arr, x)\n",
        encoding="utf-8",
    )
    logs_dir = context.stage_dirs[Stage.TEST] / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "cmd-8cfce45a271f.stdout.log").write_text("", encoding="utf-8")
    (logs_dir / "cmd-8cfce45a271f.stderr.log").write_text(
        "RecursionError: maximum recursion depth exceeded\n",
        encoding="utf-8",
    )
    model = _FakeModel(
        {
            "root_cause": "The function recurses without changing bounds.",
            "strategy": "Implement binary search with narrowing bounds.",
            "changes": [
                {
                    "path": "find_in_sorted.py",
                    "old_content": "def find_in_sorted(arr, x):\n    return find_in_sorted(arr, x)\n",
                    "new_content": (
                        "def find_in_sorted(arr, x):\n"
                        "    low, high = 0, len(arr) - 1\n"
                        "    while low <= high:\n"
                        "        mid = (low + high) // 2\n"
                        "        if arr[mid] == x:\n"
                        "            return mid\n"
                        "        if arr[mid] < x:\n"
                        "            low = mid + 1\n"
                        "        else:\n"
                        "            high = mid - 1\n"
                        "    return -1\n"
                    ),
                    "rationale": "Avoid unbounded recursion.",
                }
            ],
            "verification_command": "python -m unittest discover -s tests",
            "framework": "unittest",
        }
    )

    PlanGenerationService(model_factory=_FakeFactory(model)).create_repair_request(
        context
    )

    assert "RecursionError: maximum recursion depth exceeded" in model.prompts[0]


def test_plan_generation_writes_redacted_attempt_audit_for_malformed_responses(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.IMPLEMENT])
    model = _SequenceModel(
        [
            "not json sk-or-should-not-leak",
            '{"requirements_summary": "missing required fields"}',
            '{"requirements_summary": "still missing required fields"}',
        ]
    )

    with pytest.raises(PlanGenerationError, match="Failed to generate valid"):
        PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
            context
        )

    audit_path = (
        context.stage_dirs[Stage.IMPLEMENT] / "plan_generation_attempts.json"
    )
    audit_text = audit_path.read_text(encoding="utf-8")
    audit = json.loads(audit_text)
    assert audit["schema"] == "ImplementationPlan"
    assert audit["attempts"][0]["status"] == "invalid"
    assert audit["attempts"][1]["status"] == "invalid"
    assert "sk-or-should-not-leak" not in audit_text
    assert "<redacted>" in audit_text
    assert "prompt_sha256" in audit


def test_plan_generation_writes_attempt_audit_under_long_windows_paths(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.IMPLEMENT])
    long_dir = context.run_dir
    while len(str(long_dir / "plan_generation_attempts.json")) < 285:
        long_dir = long_dir / "deep_segment_for_windows_path_limit"
    context.stage_dirs[Stage.IMPLEMENT] = long_dir
    model = _SequenceModel(
        [
            "not json sk-or-should-not-leak",
            '{"requirements_summary": "missing required fields"}',
            '{"requirements_summary": "still missing required fields"}',
        ]
    )

    with pytest.raises(PlanGenerationError, match="Failed to generate valid"):
        PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
            context
        )

    audit_path = long_dir / "plan_generation_attempts.json"
    readable_audit_path = (
        Path("\\\\?\\" + str(audit_path.resolve())) if os.name == "nt" else audit_path
    )
    assert readable_audit_path.exists()
    audit_text = readable_audit_path.read_text(encoding="utf-8")
    assert "sk-or-should-not-leak" not in audit_text
    assert "<redacted>" in audit_text


def test_plan_generation_rejects_hidden_plan_targets_before_patch_stage(
    tmp_path,
) -> None:
    context = _context(
        tmp_path,
        stages=[Stage.IMPLEMENT],
        hidden_paths=[tmp_path / "case" / "workspace" / "pkg" / "oracle_tests"],
    )
    model = _FakeModel(
        {
            "requirements_summary": "Implement visible behavior.",
            "impact_summary": "Attempt to change a hidden oracle target.",
            "changes": [
                {
                    "path": "pkg/oracle_tests/test_hidden.py",
                    "old_content": None,
                    "new_content": "TOKEN = 'sk-or-should-not-leak'\n",
                    "rationale": "This must be rejected before patching.",
                }
            ],
            "syntax_check_targets": ["pkg/oracle_tests/test_hidden.py"],
        }
    )

    with pytest.raises(PlanGenerationError, match="Failed to generate valid"):
        PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
            context
        )

    audit_text = (
        context.stage_dirs[Stage.IMPLEMENT] / "plan_generation_attempts.json"
    ).read_text(encoding="utf-8")
    assert "oracle_tests" in audit_text
    assert "generated plan targets hidden benchmark material" in audit_text
    assert "sk-or-should-not-leak" not in audit_text
    assert "<redacted>" in audit_text


def test_plan_generation_rejects_sensitive_plan_targets_before_patch_stage(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.IMPLEMENT])
    model = _FakeModel(
        {
            "requirements_summary": "Implement visible behavior.",
            "impact_summary": "Attempt to change a sensitive target.",
            "changes": [
                {
                    "path": ".env",
                    "old_content": None,
                    "new_content": "OPENROUTER_API_KEY=sk-or-should-not-leak\n",
                    "rationale": "This must be rejected before patching.",
                }
            ],
            "syntax_check_targets": [".env"],
        }
    )

    with pytest.raises(PlanGenerationError, match="Failed to generate valid"):
        PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
            context
        )

    audit_text = (
        context.stage_dirs[Stage.IMPLEMENT] / "plan_generation_attempts.json"
    ).read_text(encoding="utf-8")
    assert "generated plan targets sensitive or generated path: .env" in audit_text
    assert "sk-or-should-not-leak" not in audit_text
    assert "<redacted>" in audit_text


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
