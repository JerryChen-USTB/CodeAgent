from __future__ import annotations

import json

import pytest

from codeagent.config.loader import load_benchmark_config, load_task_config
from codeagent.config.schema import Stage


def test_load_yaml_task_config_with_defaults_and_paths(tmp_path) -> None:
    project = tmp_path / "workspace"
    project.mkdir()
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    requirements = input_dir / "requirements.md"
    requirements.write_text("Build a tiny feature.", encoding="utf-8")
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
task_id: demo
stages: [implementation, testing]
language: python
project_path: workspace
input_materials:
  - material_type: requirements
    path: input/requirements.md
    required: true
""".strip(),
        encoding="utf-8",
    )

    config = load_task_config(config_path)

    assert config.task_id == "demo"
    assert config.stages == [Stage.IMPLEMENT, Stage.TEST]
    assert config.project_path == project
    assert config.input_materials[0].path == requirements
    assert config.model.model_name == "google/gemini-3.5-flash"
    assert config.model.api_key_env == "OPENROUTER_API_KEY"
    assert config.runtime.max_repair_attempts == 3
    assert config.test_command.command == "pytest -q"


def test_load_json_task_config(tmp_path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    config_path = tmp_path / "task.json"
    config_path.write_text(
        json.dumps(
            {
                "task_id": "json-demo",
                "stages": ["debugging", "repair"],
                "project_path": "repo",
                "test_command": "pytest -q",
            }
        ),
        encoding="utf-8",
    )

    config = load_task_config(config_path)

    assert config.task_id == "json-demo"
    assert config.stages == [Stage.DEBUG, Stage.REPAIR]


def test_benchmark_style_task_config_loads_nested_fields(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "bug_report.md").write_text("Bug details.", encoding="utf-8")
    config_path = tmp_path / "task_config.yaml"
    config_path.write_text(
        """
case_id: quixbugs_demo
stages: [testing, debugging, repair]
language: python
project:
  path: workspace
  test_framework: unittest
input_materials:
  - material_type: bug_report
    path: input/bug_report.md
    required: true
test_command:
  command: "python -m unittest discover -s workspace/tests"
  timeout_seconds: 10
agent_visibility:
  visible_paths: [input, workspace]
  hidden_paths: [expected_result.json]
""".strip(),
        encoding="utf-8",
    )

    config = load_task_config(config_path)

    assert config.case_id == "quixbugs_demo"
    assert config.project_path == workspace
    assert config.test_framework == "unittest"
    assert config.test_command.timeout_seconds == 10
    assert config.agent_visibility.hidden_paths == [tmp_path / "expected_result.json"]
    assert "evaluation" not in config.model_dump()
    assert not config.model_extra


@pytest.mark.parametrize(
    ("stage_value", "expected"),
    [("null", "至少需要选择一个阶段"), ("implement", "stages 必须是阶段列表")],
)
def test_malformed_stage_config_is_rejected_cleanly(
    tmp_path, stage_value: str, expected: str
) -> None:
    project = tmp_path / "workspace"
    project.mkdir()
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: {stage_value}
project_path: workspace
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=expected):
        load_task_config(config_path)


def test_missing_required_material_path_is_rejected(tmp_path) -> None:
    project = tmp_path / "workspace"
    project.mkdir()
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
stages: [implement]
project_path: workspace
input_materials:
  - material_type: requirements
    path: input/missing.md
    required: true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="必需输入材料不存在"):
        load_task_config(config_path)


def test_missing_project_path_is_rejected(tmp_path) -> None:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        """
stages: [implement]
project_path: missing
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="project_path"):
        load_task_config(config_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("language", "javascript"), ("test_framework", "jest")],
)
def test_unsupported_language_or_framework_is_rejected(tmp_path, field, value) -> None:
    project = tmp_path / "workspace"
    project.mkdir()
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"""
stages: [implement]
project_path: workspace
{field}: {value}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_task_config(config_path)


def test_load_benchmark_config_resolves_case_configs(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "demo"
    case_dir.mkdir(parents=True)
    task_config = case_dir / "task_config.yaml"
    task_config.write_text("stages: [implement]\nproject_path: .\n", encoding="utf-8")
    benchmark_path = tmp_path / "benchmark.yaml"
    benchmark_path.write_text(
        """
name: demo_benchmark
default_output_dir: codeagent_runs/benchmarks
cases:
  - case_id: demo
    config: cases/demo/task_config.yaml
    enabled: true
""".strip(),
        encoding="utf-8",
    )

    benchmark = load_benchmark_config(benchmark_path)

    assert benchmark.name == "demo_benchmark"
    assert benchmark.cases[0].config == task_config


def test_load_json_benchmark_config(tmp_path) -> None:
    case_dir = tmp_path / "cases" / "demo"
    case_dir.mkdir(parents=True)
    task_config = case_dir / "task_config.yaml"
    task_config.write_text("stages: [implement]\nproject_path: .\n", encoding="utf-8")
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text(
        json.dumps(
            {
                "name": "json_benchmark",
                "cases": [
                    {
                        "case_id": "demo",
                        "config": "cases/demo/task_config.yaml",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    benchmark = load_benchmark_config(benchmark_path)

    assert benchmark.name == "json_benchmark"
    assert benchmark.cases[0].config == task_config
