from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from codeagent.agents.plan_generation import (
    PlanGenerationError,
    PlanGenerationService,
    _is_retryable_model_error,
    _reduced_max_tokens_for_model_error,
)
from codeagent.config.schema import InputMaterial, Stage, TaskConfig
from codeagent.runtime.run_context import RunContext, create_run_context
from codeagent.stages.implementation_service import (
    PATCH_INTERRUPT_ID,
    ImplementationFileChange,
    ImplementationPlan,
)
from codeagent.stages.debugging_service import FaultLocalization
from codeagent.stages.repair_service import (
    REPAIR_COMMAND_INTERRUPT_ID,
    REPAIR_PATCH_INTERRUPT_ID,
)
from codeagent.stages.testing_service import (
    TEST_COMMAND_INTERRUPT_ID,
    TEST_PATCH_INTERRUPT_ID,
    TEST_PLAN_INTERRUPT_ID,
    TestFileChange,
    TestingPlan,
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
        return _Response("```json\n" + json.dumps(_normalize_fixture_response(self.response)) + "\n```")


def test_reduces_max_tokens_from_openrouter_affordability_error() -> None:
    error = RuntimeError(
        "Error code: 402 - {'error': {'message': 'This request requires more "
        "credits, or fewer max_tokens. You requested up to 16384 tokens, but "
        "can only afford 2924.'}}"
    )

    reduced = _reduced_max_tokens_for_model_error(error, current_max_tokens=16384)

    assert reduced == 2631


def test_insufficient_credits_model_error_is_not_retryable() -> None:
    error = RuntimeError("Error code: 402 - Insufficient credits.")

    assert _is_retryable_model_error(error) is False


def _normalize_fixture_response(response: dict) -> dict:
    if "failure_origin" in response and "repair_strategy" in response:
        return response
    if "requirements_summary" in response and "impact_summary" in response:
        return {
            "requirements_summary": response["requirements_summary"],
            "implementation_strategy": response["impact_summary"],
            "changes": [
                {
                    "path": change["path"],
                    "change_type": "modify",
                    "rationale": change.get("rationale") or "Fixture implementation change.",
                    "public_interfaces": [],
                    "acceptance_notes": [],
                }
                for change in response.get("changes", [])
            ],
            "acceptance_criteria": ["Visible requirements are satisfied."],
            "risk_notes": [],
        }
    if "target_summary" in response:
        return {
            "target_summary": response["target_summary"],
            "strategy": response["strategy"],
            "acceptance_criteria": response["acceptance_criteria"],
            "changes": [
                {
                    "path": change["path"],
                    "test_focus": change.get("rationale") or "Fixture test focus.",
                    "rationale": change.get("rationale") or "Fixture test change.",
                }
                for change in response.get("changes", [])
            ],
            "command": response["command"],
            "framework": response.get("framework", "pytest"),
        }
    if "root_cause" in response:
        normalized = {
            "root_cause": response["root_cause"],
            "strategy": response["strategy"],
            "changes": [
                {
                    "path": change["path"],
                    "change_type": "modify",
                    "rationale": change.get("rationale") or "Fixture repair change.",
                    "expected_effect": "Visible regression tests pass after repair.",
                }
                for change in response.get("changes", [])
            ],
            "verification_command": response["verification_command"],
            "framework": response.get("framework", "pytest"),
        }
        if "failure_origin" in response:
            normalized["failure_origin"] = response["failure_origin"]
        if "test_repair_allowed" in response:
            normalized["test_repair_allowed"] = response["test_repair_allowed"]
        if "test_repair_rationale" in response:
            normalized["test_repair_rationale"] = response["test_repair_rationale"]
        return normalized
    return response


class _SequenceModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> _Response:
        self.prompts.append(prompt)
        return _Response(self.responses.pop(0))


class _FailingModel:
    def __init__(self, message: str) -> None:
        self.message = message
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> _Response:
        self.prompts.append(prompt)
        raise RuntimeError(self.message)


class _FakeFactory:
    def __init__(self, model) -> None:
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
    assert "Simplified Chinese" in prompt
    assert "简体中文" in prompt
    assert "do not translate identifiers or source code" in prompt
    assert request.plan.changes[0].path.as_posix() == "solution.py"
    assert request.approval.interrupt_id == PATCH_INTERRUPT_ID
    assert request.approval.decision_type == "approve"


def test_plan_generation_writes_llm_call_bundle_and_trace_indexes(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.IMPLEMENT])
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

    PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
        context
    )

    call_dirs = sorted((context.stage_dirs[Stage.IMPLEMENT] / "llm_calls").iterdir())
    assert len(call_dirs) == 1
    call_dir = call_dirs[0]
    assert (call_dir / "request.json").exists()
    assert (call_dir / "prompt.full.txt").exists()
    assert (call_dir / "prompt.manifest.json").exists()
    assert (call_dir / "call_summary.md").exists()
    assert (call_dir / "attempt_01" / "prompt.full.txt").exists()
    assert (call_dir / "attempt_01" / "prompt.manifest.json").exists()
    assert (call_dir / "attempt_01" / "response.raw.txt").exists()
    assert (call_dir / "attempt_01" / "response.parsed.json").exists()
    assert (call_dir / "attempt_01" / "validation.json").exists()

    request = json.loads((call_dir / "request.json").read_text(encoding="utf-8"))
    assert request["schema"] == "ImplementationPlan"
    assert request["generation_kind"] == "plan_generation"
    validation = json.loads(
        (call_dir / "attempt_01" / "validation.json").read_text(encoding="utf-8")
    )
    assert validation["status"] == "valid"

    events = [
        json.loads(line)
        for line in (context.run_dir / "workflow_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    prompt_event = next(event for event in events if event["event_type"] == "llm_prompt")
    response_event = next(
        event for event in events if event["event_type"] == "llm_response"
    )
    output_event = next(
        event for event in events if event["event_type"] == "llm_structured_output"
    )
    assert prompt_event["call_id"] == call_dir.name
    assert "prompt" not in prompt_event
    assert (context.run_dir / prompt_event["prompt_path"]).exists()
    assert (context.run_dir / prompt_event["prompt_manifest_path"]).exists()
    assert "response" not in response_event
    assert (context.run_dir / response_event["response_path"]).exists()
    assert "output" not in output_event
    assert (context.run_dir / output_event["output_path"]).exists()
    assert (context.run_dir / output_event["validation_path"]).exists()

    legacy_audit = context.stage_dirs[Stage.IMPLEMENT] / "plan_generation_attempts.json"
    assert legacy_audit.exists()


def test_plan_generation_builds_single_file_patch_from_fenced_code(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.IMPLEMENT])
    target = context.task_config.project_path / "solution.py"
    old_content = "def add(left, right):\n    pass\n"
    target.write_text(old_content, encoding="utf-8")
    plan = ImplementationPlan(
        requirements_summary="实现加法。",
        implementation_strategy="补全 solution.py。",
        changes=[
            ImplementationFileChange(
                path=Path("solution.py"),
                change_type="modify",
                rationale="满足可见需求。",
            )
        ],
        acceptance_criteria=["add 返回两数之和。"],
    )
    model = _SequenceModel(
        [
            (
                "补全目标文件。\n\n"
                "```python\n"
                "def add(left, right):\n"
                "    return left + right\n"
                "```\n"
            )
        ]
    )

    draft = PlanGenerationService(
        model_factory=_FakeFactory(model)
    ).create_implementation_file_patch_draft(
        context,
        plan,
        target_path=Path("solution.py"),
        workspace_context="### project/solution.py\n" + old_content,
        work_summary="",
        completed_files=[],
        failed_attempts=[],
    )

    assert "do not return JSON" in model.prompts[0]
    assert "exactly one fenced code block" in model.prompts[0]
    assert draft.plan_summary == "补全目标文件。"
    assert draft.changes[0].path.as_posix() == "solution.py"
    assert draft.changes[0].old_content == old_content
    assert (
        draft.changes[0].new_content
        == "def add(left, right):\n    return left + right\n"
    )
    assert draft.syntax_check_targets == [Path("solution.py")]

    call_dirs = sorted((context.stage_dirs[Stage.IMPLEMENT] / "llm_calls").iterdir())
    call_dir = call_dirs[0]
    request_path = call_dir / "request.json"
    parsed_path = call_dir / "attempt_01" / "response.parsed.json"
    if os.name == "nt":
        request_path = Path("\\\\?\\" + str(request_path.resolve()))
        parsed_path = Path("\\\\?\\" + str(parsed_path.resolve()))
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert request["generation_kind"] == "single_file_patch_generation"
    assert request["schema"] == "ImplementationPatchDraft"
    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    assert parsed["changes"][0]["new_content"].endswith("return left + right\n")


def test_plan_generation_retries_single_file_patch_without_fenced_code(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.IMPLEMENT])
    plan = ImplementationPlan(
        requirements_summary="实现加法。",
        implementation_strategy="创建 solution.py。",
        changes=[
            ImplementationFileChange(
                path=Path("solution.py"),
                change_type="add",
                rationale="满足可见需求。",
            )
        ],
        acceptance_criteria=["add 返回两数之和。"],
    )
    model = _SequenceModel(
        [
            "def add(left, right):\n    return left + right\n",
            "```python\ndef add(left, right):\n    return left + right\n```\n",
        ]
    )

    draft = PlanGenerationService(
        model_factory=_FakeFactory(model)
    ).create_implementation_file_patch_draft(
        context,
        plan,
        target_path=Path("solution.py"),
        workspace_context="(empty)",
        work_summary="",
        completed_files=[],
        failed_attempts=[],
    )

    assert len(model.prompts) == 2
    assert "Previous response failed fenced-code validation" in model.prompts[1]
    assert draft.changes[0].old_content is None
    assert (
        draft.changes[0].new_content
        == "def add(left, right):\n    return left + right\n"
    )

    audit = json.loads(
        (
            context.stage_dirs[Stage.IMPLEMENT]
            / "patch_generation_solution.py_attempts.json"
        ).read_text(encoding="utf-8")
    )
    assert audit["attempts"][0]["status"] == "invalid"
    assert audit["attempts"][1]["status"] == "valid"


def test_plan_generation_builds_testing_file_patch_from_fenced_code(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.TEST])
    plan = TestingPlan(
        target_summary="验证加法。",
        strategy="生成一个可见 pytest 文件。",
        acceptance_criteria=["覆盖基础加法。"],
        changes=[
            TestFileChange(
                path=Path("tests/test_solution.py"),
                test_focus="基础加法行为。",
                rationale="覆盖可见需求。",
            )
        ],
        command="python -m pytest tests -q",
        framework="pytest",
    )
    model = _SequenceModel(
        [
            (
                "```python\n"
                "from solution import add\n\n"
                "def test_add_visible():\n"
                "    assert add(2, 3) == 5\n"
                "```\n"
            )
        ]
    )

    draft = PlanGenerationService(
        model_factory=_FakeFactory(model)
    ).create_testing_file_patch_draft(
        context,
        plan,
        target_path=Path("tests/test_solution.py"),
        workspace_context="(empty)",
        work_summary="",
        completed_files=[],
        failed_attempts=[],
    )

    assert draft.command == "python -m pytest tests -q"
    assert draft.framework == "pytest"
    assert draft.changes[0].path.as_posix() == "tests/test_solution.py"
    assert "def test_add_visible" in (draft.changes[0].new_content or "")


def test_plan_generation_rejects_implementation_test_artifacts(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.IMPLEMENT])
    model = _FakeModel(
        {
            "requirements_summary": "Implement a feature.",
            "impact_summary": "Incorrectly tries to add tests during implementation.",
            "changes": [
                {
                    "path": "tests/test_feature.py",
                    "old_content": None,
                    "new_content": "def test_feature():\n    assert True\n",
                    "rationale": "This belongs in the testing stage.",
                }
            ],
            "syntax_check_targets": [],
        }
    )

    with pytest.raises(PlanGenerationError, match="test artifact"):
        PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
            context
        )


def test_plan_generation_builds_testing_request_without_hidden_context(tmp_path) -> None:
    case_dir = tmp_path / "case"
    input_dir = case_dir / "input"
    input_dir.mkdir(parents=True)
    requirements = input_dir / "requirements.md"
    requirements.write_text("Implement add(left, right).\n", encoding="utf-8")
    hidden_dir = case_dir / "oracle_tests"
    hidden_dir.mkdir()
    (hidden_dir / "test_secret.py").write_text("SECRET_ORACLE = True\n", encoding="utf-8")
    context = _context(
        tmp_path,
        stages=[Stage.TEST],
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
        "def add(left, right):\n    return left + right\n",
        encoding="utf-8",
    )
    model = _FakeModel(
        {
            "target_summary": "Verify add helper.",
            "strategy": "Generate visible tests for normal arithmetic behavior.",
            "acceptance_criteria": ["add(2, 3) returns 5"],
            "changes": [
                {
                    "path": "tests/test_solution.py",
                    "old_content": None,
                    "new_content": (
                        "from solution import add\n\n"
                        "def test_add_visible():\n"
                        "    assert add(2, 3) == 5\n"
                    ),
                    "rationale": "Exercise the visible public requirement.",
                }
            ],
            "command": "python -m pytest tests -q",
            "framework": "pytest",
        }
    )

    request = PlanGenerationService(model_factory=_FakeFactory(model)).create_testing_request(
        context
    )

    prompt = model.prompts[0]
    assert "Implement add(left, right)." in prompt
    assert "solution.py" in prompt
    assert "SECRET_ORACLE" not in prompt
    assert "test_secret.py" not in prompt
    assert request.plan.changes[0].path.as_posix() == "tests/test_solution.py"
    assert request.plan_review.interrupt_id == TEST_PLAN_INTERRUPT_ID
    assert request.patch_approval.interrupt_id == TEST_PATCH_INTERRUPT_ID
    assert request.command_approval.interrupt_id == TEST_COMMAND_INTERRUPT_ID
    assert request.plan_review.auto is True


def test_plan_generation_normalizes_testing_command_wrapper_prefix(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.TEST])
    model = _FakeModel(
        {
            "target_summary": "Verify generated package.",
            "strategy": "Run generated tests from the project root.",
            "acceptance_criteria": ["Generated tests are collected."],
            "changes": [
                {
                    "path": "project/tests/test_generated.py",
                    "old_content": None,
                    "new_content": "def test_generated():\n    assert True\n",
                    "rationale": "Exercise visible behavior.",
                }
            ],
            "command": "cd project && python -m pytest project/tests/test_generated.py -v",
            "framework": "pytest",
        }
    )

    request = PlanGenerationService(model_factory=_FakeFactory(model)).create_testing_request(
        context
    )

    assert request.plan.changes[0].path.as_posix() == "tests/test_generated.py"
    assert request.plan.command == "python -m pytest tests/test_generated.py -v"


def test_plan_generation_rejects_oversized_testing_plan(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.TEST])
    model = _FakeModel(
        {
            "target_summary": "Verify too many files.",
            "strategy": "Incorrectly plans a broad generated suite.",
            "acceptance_criteria": ["Generated tests stay readable."],
            "changes": [
                {
                    "path": f"tests/test_generated_{index}.py",
                    "rationale": "Too many generated files.",
                }
                for index in range(7)
            ],
            "command": "python -m pytest tests -q",
            "framework": "pytest",
        }
    )

    with pytest.raises(PlanGenerationError, match="testing plan is too large"):
        PlanGenerationService(model_factory=_FakeFactory(model)).create_testing_request(
            context
        )


def test_plan_generation_allows_two_testing_files_with_split_rationale(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.TEST])
    model = _FakeModel(
        {
            "target_summary": "Verify CLI and unit behavior.",
            "strategy": "Split subprocess CLI tests from compact unit tests for readability.",
            "acceptance_criteria": ["Generated tests stay readable."],
            "changes": [
                {
                    "path": "tests/test_app.py",
                    "rationale": "Unit tests cover public behavior.",
                },
                {
                    "path": "tests/test_cli.py",
                    "rationale": "Separate subprocess CLI tests from unit tests.",
                },
            ],
            "command": "python -m pytest tests -q",
            "framework": "pytest",
        }
    )

    request = PlanGenerationService(model_factory=_FakeFactory(model)).create_testing_request(
        context
    )

    assert [change.path.as_posix() for change in request.plan.changes] == [
        "tests/test_app.py",
        "tests/test_cli.py",
    ]


def test_plan_generation_rejects_two_testing_files_without_split_rationale(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.TEST])
    model = _FakeModel(
        {
            "target_summary": "Verify behavior.",
            "strategy": "Generate visible tests.",
            "acceptance_criteria": ["Generated tests stay readable."],
            "changes": [
                {
                    "path": "tests/test_app.py",
                    "rationale": "Cover app behavior.",
                },
                {
                    "path": "tests/test_cli.py",
                    "rationale": "Cover CLI behavior.",
                },
            ],
            "command": "python -m pytest tests -q",
            "framework": "pytest",
        }
    )

    with pytest.raises(PlanGenerationError, match="does not explain why"):
        PlanGenerationService(model_factory=_FakeFactory(model)).create_testing_request(
            context
        )


def test_plan_generation_rejects_multi_file_testing_plan_narrow_command(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.TEST])
    model = _FakeModel(
        {
            "target_summary": "Verify CLI and unit behavior.",
            "strategy": "Split subprocess CLI tests from compact unit tests.",
            "acceptance_criteria": ["Generated tests stay readable."],
            "changes": [
                {
                    "path": "tests/test_app.py",
                    "rationale": "Unit tests cover public behavior.",
                },
                {
                    "path": "tests/test_cli.py",
                    "rationale": "Separate subprocess CLI tests from unit tests.",
                },
            ],
            "command": "python -m pytest tests/test_app.py -q",
            "framework": "pytest",
        }
    )

    with pytest.raises(PlanGenerationError, match="too narrow"):
        PlanGenerationService(model_factory=_FakeFactory(model)).create_testing_request(
            context
        )


def test_plan_generation_rejects_oversized_testing_patch(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.TEST])
    plan = TestingPlan(
        target_summary="Verify generated behavior.",
        strategy="Generate a compact test file.",
        acceptance_criteria=["Generated tests are readable."],
        changes=[
            TestFileChange(
                path="tests/test_generated.py",
                test_focus="Representative behavior.",
                rationale="Keep generated tests compact.",
            )
        ],
        command="python -m pytest tests/test_generated.py -q",
        framework="pytest",
    )
    too_many_tests = "\n\n".join(
        f"def test_case_{index}():\n    assert True" for index in range(81)
    )
    model = _FakeModel(
        {
            "plan_summary": "Too many tests.",
            "changes": [
                {
                    "path": "tests/test_generated.py",
                    "old_content": None,
                    "new_content": too_many_tests,
                    "rationale": "This should be rejected as too large.",
                }
            ],
            "command": "python -m pytest tests/test_generated.py -q",
            "framework": "pytest",
        }
    )

    with pytest.raises(PlanGenerationError, match="testing patch"):
        PlanGenerationService(model_factory=_FakeFactory(model)).create_testing_patch_draft(
            context,
            plan,
        )


def test_plan_generation_allows_more_than_fifteen_tests_in_one_file(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.TEST])
    plan = TestingPlan(
        target_summary="Verify generated behavior.",
        strategy="Generate one readable test file with representative coverage.",
        acceptance_criteria=["Generated tests stay within the total suite budget."],
        changes=[
            TestFileChange(
                path="tests/test_generated.py",
                test_focus="Representative behavior.",
                rationale="One file is still readable for this suite.",
            )
        ],
        command="python -m pytest -q",
        framework="pytest",
    )
    nineteen_tests = "\n\n".join(
        f"def test_case_{index}():\n    assert True" for index in range(19)
    )
    model = _FakeModel(
        {
            "plan_summary": "Nineteen tests in one visible test file.",
            "changes": [
                {
                    "path": "tests/test_generated.py",
                    "old_content": None,
                    "new_content": nineteen_tests,
                    "rationale": "This should be accepted because the per-file cap was removed.",
                }
            ],
            "command": "python -m pytest -q",
            "framework": "pytest",
        }
    )

    draft = PlanGenerationService(
        model_factory=_FakeFactory(model)
    ).create_testing_patch_draft(context, plan)

    assert len(draft.changes) == 1
    assert draft.changes[0].new_content.count("def test_case_") == 19


def test_plan_generation_rejects_testing_patch_with_too_many_files(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.TEST])
    plan = TestingPlan(
        target_summary="Verify generated behavior.",
        strategy="Generate a compact test suite.",
        acceptance_criteria=["Generated tests are readable."],
        changes=[
            TestFileChange(
                path="tests/test_generated.py",
                test_focus="Representative behavior.",
                rationale="Keep generated tests compact.",
            )
        ],
        command="python -m pytest tests -q",
        framework="pytest",
    )
    model = _FakeModel(
        {
            "plan_summary": "Too many test files.",
            "changes": [
                {
                    "path": f"tests/test_generated_{index}.py",
                    "old_content": None,
                    "new_content": "def test_generated():\n    assert True\n",
                    "rationale": "This should be rejected as too many files.",
                }
                for index in range(3)
            ],
            "command": "python -m pytest tests -q",
            "framework": "pytest",
        }
    )

    with pytest.raises(PlanGenerationError, match="too many files"):
        PlanGenerationService(model_factory=_FakeFactory(model)).create_testing_patch_draft(
            context,
            plan,
        )


def test_plan_generation_testing_patch_prompt_forbids_nested_project_cwd(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.TEST])
    plan = TestingPlan(
        target_summary="Verify CLI behavior.",
        strategy="Generate subprocess CLI tests.",
        acceptance_criteria=["CLI subprocess tests use the real project root."],
        changes=[
            TestFileChange(
                path="tests/test_cli.py",
                test_focus="Run CLI through subprocess.",
                rationale="Exercise the public command line entry point.",
            )
        ],
        command="python -m pytest tests/test_cli.py -q",
        framework="pytest",
    )
    model = _FakeModel(
        {
            "plan_summary": "Concrete CLI subprocess tests.",
            "changes": [
                {
                    "path": "tests/test_cli.py",
                    "old_content": None,
                    "new_content": "def test_cli_subprocess():\n    assert True\n",
                    "rationale": "Exercise CLI behavior.",
                }
            ],
            "command": "python -m pytest tests/test_cli.py -q",
            "framework": "pytest",
        }
    )

    draft = PlanGenerationService(model_factory=_FakeFactory(model)).create_testing_patch_draft(
        context,
        plan,
    )

    prompt = model.prompts[0]
    assert "cwd must be an existing directory" in prompt
    assert "parent.parent / 'project'" in prompt
    assert "parents[1] / 'workspace'" in prompt
    assert "Simplified Chinese" in prompt
    assert "简体中文" in prompt
    assert draft.changes[0].path.as_posix() == "tests/test_cli.py"


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
    assert not hasattr(request.plan, "syntax_check_targets")


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
    assert not hasattr(request.plan, "syntax_check_targets")


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


def test_plan_generation_repair_context_prioritizes_failure_and_current_source(
    tmp_path,
) -> None:
    case_dir = tmp_path / "case"
    input_dir = case_dir / "input"
    project = case_dir / "workspace"
    input_dir.mkdir(parents=True)
    project.mkdir()
    requirements = input_dir / "requirements.md"
    requirements.write_text(
        "Initial requirement says workspace starts empty.\n"
        + ("A" * 8000)
        + "\nHUGE_INPUT_TRAILER\n",
        encoding="utf-8",
    )
    (project / "todo_manager.py").write_text(
        "CURRENT_SOURCE_MARKER = True\n",
        encoding="utf-8",
    )
    config = TaskConfig(
        stages=[Stage.TEST, Stage.DEBUG, Stage.REPAIR],
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
    debugging_dir = context.stage_dirs[Stage.DEBUG]
    debugging_dir.mkdir(parents=True, exist_ok=True)
    (debugging_dir / "root_cause.md").write_text(
        "REAL_FAILURE_MARKER: stdout decode failed on Windows.\n",
        encoding="utf-8",
    )
    model = _FakeModel(
        {
            "root_cause": "The current source has an encoding issue.",
            "strategy": "Modify the current product file only.",
            "changes": [
                {
                    "path": "todo_manager.py",
                    "rationale": "Repair the visible current source file.",
                }
            ],
            "verification_command": "python -m pytest -q",
            "framework": "pytest",
        }
    )

    PlanGenerationService(
        model_factory=_FakeFactory(model),
        max_context_chars=2200,
    ).create_repair_request(context)

    prompt = model.prompts[0]
    assert "REAL_FAILURE_MARKER" in prompt
    assert "CURRENT_SOURCE_MARKER" in prompt
    assert "HUGE_INPUT_TRAILER" not in prompt
    assert "Do not say the workspace is empty" in prompt


def test_plan_generation_debugging_analysis_prioritizes_latest_repair_failure(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.TEST, Stage.DEBUG, Stage.REPAIR])
    (context.task_config.project_path / "app.py").write_text(
        "def main():\n    return None\n",
        encoding="utf-8",
    )
    repair_dir = context.stage_dirs[Stage.REPAIR]
    repair_dir.mkdir(parents=True, exist_ok=True)
    (repair_dir / "after_test.log").write_text(
        "NEW_REPAIR_FAILURE: NameError: name 'null' is not defined\n",
        encoding="utf-8",
    )
    testing_dir = context.stage_dirs[Stage.TEST]
    testing_dir.mkdir(parents=True, exist_ok=True)
    (testing_dir / "test_report.md").write_text(
        "OLD_TESTING_FAILURE: initial assertion failure\n",
        encoding="utf-8",
    )
    model = _FakeModel(
        {
            "failure_origin": "generated_test_code",
            "confidence": "high",
            "candidates": [
                {
                    "path": "tests/test_app.py",
                    "kind": "test_code",
                    "confidence": "high",
                    "evidence": ["null appears in visible generated test code"],
                    "rationale": "The latest repair failure is a generated test bug.",
                }
            ],
            "evidence": ["NEW_REPAIR_FAILURE mentions null"],
            "root_cause": "生成测试中误用了 Python 不存在的 null。",
            "repair_strategy": "把可见测试中的 null 修成 None 或正确断言值。",
            "test_repair_allowed": True,
            "test_repair_rationale": "错误位于可见生成测试代码。",
            "recommended_verification_command": "python -m pytest -q",
            "framework": "pytest",
        }
    )

    PlanGenerationService(
        model_factory=_FakeFactory(model),
        max_context_chars=3000,
    ).create_debugging_analysis(
        context,
        failure_summary="Latest failure summary.",
        static_localization=FaultLocalization(
            failing_tests=["tests/test_app.py::test_app"],
            candidates=[],
            confidence="low",
            reproduction_status="reproduced",
        ),
    )

    prompt = model.prompts[0]
    assert "DebuggingAnalysis" in prompt
    assert "NEW_REPAIR_FAILURE" in prompt
    if "OLD_TESTING_FAILURE" in prompt:
        assert prompt.index("NEW_REPAIR_FAILURE") < prompt.index("OLD_TESTING_FAILURE")


def test_plan_generation_repair_plan_can_authorize_visible_test_repair(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.TEST, Stage.DEBUG, Stage.REPAIR])
    tests_dir = context.task_config.project_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_generated.py").write_text(
        "def test_bad_literal():\n    assert null is None\n",
        encoding="utf-8",
    )
    debugging_dir = context.stage_dirs[Stage.DEBUG]
    debugging_dir.mkdir(parents=True, exist_ok=True)
    (debugging_dir / "llm_debug_analysis.json").write_text(
        json.dumps(
            {
                "failure_origin": "generated_test_code",
                "test_repair_allowed": True,
                "test_repair_rationale": "NameError null is in visible generated tests.",
            }
        ),
        encoding="utf-8",
    )
    model = _FakeModel(
        {
            "root_cause": "生成测试使用了 Python 中不存在的 null。",
            "strategy": "只修复可见生成测试中的错误字面量。",
            "changes": [
                {
                    "path": "tests/test_generated.py",
                    "rationale": "修复可见生成测试自身错误。",
                }
            ],
            "verification_command": "python -m pytest -q",
            "framework": "pytest",
            "failure_origin": "generated_test_code",
            "test_repair_allowed": True,
            "test_repair_rationale": "NameError null is in visible generated tests.",
        }
    )

    request = PlanGenerationService(model_factory=_FakeFactory(model)).create_repair_request(
        context
    )

    assert request.plan.failure_origin == "generated_test_code"
    assert request.plan.test_repair_allowed is True
    assert request.plan.changes[0].path.as_posix() == "tests/test_generated.py"
    assert "test_repair_allowed=true" in model.prompts[0]


def test_plan_generation_rejects_overbroad_repair_plan_when_source_exists(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.TEST, Stage.DEBUG, Stage.REPAIR])
    (context.task_config.project_path / "app.py").write_text(
        "def main():\n    return 0\n",
        encoding="utf-8",
    )
    model = _FakeModel(
        {
            "root_cause": "A single failure was treated like a full rewrite.",
            "strategy": "Incorrectly list every product file.",
            "changes": [
                {
                    "path": f"pkg/file_{index}.py",
                    "rationale": "Overbroad repair target.",
                }
                for index in range(5)
            ],
            "verification_command": "python -m pytest -q",
            "framework": "pytest",
        }
    )

    with pytest.raises(PlanGenerationError, match="repair plan is too broad"):
        PlanGenerationService(model_factory=_FakeFactory(model)).create_repair_request(
            context
        )


def test_plan_generation_repair_uses_latest_agent_self_test_command(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.TEST, Stage.DEBUG, Stage.REPAIR])
    (context.task_config.project_path / "calc.py").write_text(
        "def add(left, right):\n    return left - right\n",
        encoding="utf-8",
    )
    testing_dir = context.stage_dirs[Stage.TEST]
    testing_dir.mkdir(parents=True, exist_ok=True)
    (testing_dir / "test_command.json").write_text(
        json.dumps(
            {
                "command": "python -m pytest tests/test_generated_calc.py -q",
                "executed": True,
            }
        ),
        encoding="utf-8",
    )
    (testing_dir / "test_result.json").write_text(
        json.dumps(
            {
                "success": False,
                "passed": 0,
                "failed": 1,
                "errors": 0,
                "skipped": 0,
                "total": 1,
                "error_summary": "assert -1 == 3",
            }
        ),
        encoding="utf-8",
    )
    model = _FakeModel(
        {
            "root_cause": "add subtracts instead of adding.",
            "strategy": "Replace subtraction with addition.",
            "changes": [
                {
                    "path": "workspace/calc.py",
                    "old_content": "def add(left, right):\n    return left - right\n",
                    "new_content": "def add(left, right):\n    return left + right\n",
                    "rationale": "Match generated self-test expectation.",
                }
            ],
            "verification_command": (
                "cd workspace && python -m pytest "
                "workspace/tests/test_generated_calc.py -q"
            ),
            "framework": "pytest",
        }
    )

    request = PlanGenerationService(model_factory=_FakeFactory(model)).create_repair_request(
        context
    )

    prompt = model.prompts[0]
    assert "python -m pytest tests/test_generated_calc.py -q" in prompt
    assert "assert -1 == 3" in prompt
    assert request.plan.changes[0].path.as_posix() == "calc.py"
    assert (
        request.plan.verification_command
        == "python -m pytest tests/test_generated_calc.py -q"
    )


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


def test_plan_generation_records_schema_retry_reason(tmp_path) -> None:
    context = _context(tmp_path, stages=[Stage.IMPLEMENT])
    model = _SequenceModel(
        [
            "not json",
            json.dumps(
                {
                    "requirements_summary": "Implement visible behavior.",
                    "implementation_strategy": "Create solution.py.",
                    "changes": [
                        {
                            "path": "solution.py",
                            "change_type": "add",
                            "rationale": "Add the requested implementation.",
                            "public_interfaces": [],
                            "acceptance_notes": [],
                        }
                    ],
                    "acceptance_criteria": ["solution.py is planned."],
                    "risk_notes": [],
                }
            ),
        ]
    )

    PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
        context
    )

    events = [
        json.loads(line)
        for line in (context.run_dir / "workflow_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    retry_events = [
        event for event in events if event["event_type"] == "llm_retry_scheduled"
    ]
    assert retry_events
    assert retry_events[0]["schema"] == "ImplementationPlan"
    assert "model response did not contain a JSON object" in retry_events[0]["reason"]


def test_plan_generation_redacts_provider_account_links_from_model_errors(
    tmp_path,
) -> None:
    context = _context(tmp_path, stages=[Stage.IMPLEMENT])
    model = _FailingModel(
        "Error code: 402. Visit "
        "https://openrouter.ai/workspaces/default/keys/deadbeefcafebabe "
        "for user_3AcDhKqoZ79KPBpEz1UYGiuadXx with sk-or-should-not-leak"
    )

    with pytest.raises(PlanGenerationError) as exc_info:
        PlanGenerationService(model_factory=_FakeFactory(model)).create_implementation_request(
            context
        )

    audit_text = (
        context.stage_dirs[Stage.IMPLEMENT] / "plan_generation_attempts.json"
    ).read_text(encoding="utf-8")
    combined = f"{exc_info.value}\n{audit_text}"
    assert "deadbeefcafebabe" not in combined
    assert "user_3AcDhKqoZ79KPBpEz1UYGiuadXx" not in combined
    assert "sk-or-should-not-leak" not in combined
    assert "https://openrouter.ai/workspaces/<redacted>" in combined
    assert "user_<redacted>" in combined
    assert "<redacted>" in combined


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
    assert "old_content" not in audit_text
    assert "new_content" not in audit_text


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
    assert "old_content" not in audit_text
    assert "new_content" not in audit_text


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
