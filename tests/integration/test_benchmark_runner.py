from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from codeagent.benchmark.case_loader import CaseLoader
from codeagent.benchmark.evaluator import CaseEvaluator
from codeagent.benchmark.runner import BenchmarkRunner
from codeagent.cli.app import app


runner = CliRunner()


def _write_unittest_case(
    root: Path,
    case_id: str,
    *,
    command: str = "python -m unittest discover -s {{CASE_DIR}}/workspace/tests",
    hidden_paths: str = "oracle_tests",
) -> Path:
    case_dir = root / case_id
    tests_dir = case_dir / "workspace" / "tests"
    tests_dir.mkdir(parents=True)
    (case_dir / "workspace" / "math_utils.py").write_text(
        "def add(left, right):\n    return left + right\n",
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
    (case_dir / "oracle_tests").mkdir()
    (case_dir / "task_config.yaml").write_text(
        f"""
case_id: {case_id}
stages: [test]
project_path: workspace
language: python
test_framework: unittest
test_command:
  command: "{command}"
  timeout_seconds: 10
agent_visibility:
  visible_paths: [workspace]
  hidden_paths: [{hidden_paths}]
""".strip(),
        encoding="utf-8",
    )
    return case_dir


def _benchmark_config(tmp_path: Path, case_id: str) -> Path:
    config = tmp_path / "benchmark.yaml"
    config.write_text(
        f"""
schema_version: 1
name: m23_fixture
default_output_dir: runs
cases:
  - case_id: {case_id}
    config: cases/{case_id}/task_config.yaml
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    return config


def _benchmark_config_for_cases(tmp_path: Path, case_ids: list[str]) -> Path:
    config = tmp_path / "benchmark.yaml"
    case_lines = "\n".join(
        [
            f"  - case_id: {case_id}\n"
            f"    config: cases/{case_id}/task_config.yaml\n"
            "    enabled: true"
            for case_id in case_ids
        ]
    )
    config.write_text(
        f"""
schema_version: 1
name: m23_fixture
default_output_dir: runs
cases:
{case_lines}
""".strip(),
        encoding="utf-8",
    )
    return config


def test_case_loader_resolves_enabled_cases_without_reading_hidden_files(tmp_path) -> None:
    _write_unittest_case(tmp_path / "cases", "case_pass")
    config_path = _benchmark_config(tmp_path, "case_pass")

    loaded = CaseLoader().load(config_path)

    assert loaded.config.name == "m23_fixture"
    assert [case.case_id for case in loaded.enabled_cases] == ["case_pass"]
    assert loaded.enabled_cases[0].source_case_dir == tmp_path / "cases" / "case_pass"


def test_prepare_case_workspace_copies_case_and_keeps_original_reusable(tmp_path) -> None:
    case_dir = _write_unittest_case(tmp_path / "cases", "case_copy")
    config_path = _benchmark_config(tmp_path, "case_copy")
    loaded = CaseLoader().load(config_path)
    benchmark_run_dir = tmp_path / "runs" / "benchmark_run"

    context = BenchmarkRunner().prepare_case_workspace(
        loaded.enabled_cases[0],
        benchmark_run_dir=benchmark_run_dir,
        benchmark_config=loaded.config,
    )
    copied_marker = context.run_case_dir / "workspace" / "copied.txt"
    copied_marker.write_text("copy only\n", encoding="utf-8")

    assert context.run_case_dir != case_dir
    assert context.run_case_dir.is_dir()
    assert copied_marker.exists()
    assert not (case_dir / "workspace" / "copied.txt").exists()
    assert context.task_config.project_path == context.run_case_dir / "workspace"
    assert "{{CASE_DIR}}" not in context.task_config.test_command.command
    assert context.task_config.test_command.command == "python -m unittest discover -s tests"


def test_prepare_case_workspace_normalizes_project_relative_test_paths(tmp_path) -> None:
    _write_unittest_case(
        tmp_path / "cases",
        "case_project_relative_command",
        command="python -m unittest discover -s workspace/tests",
    )
    config_path = _benchmark_config(tmp_path, "case_project_relative_command")
    loaded = CaseLoader().load(config_path)
    benchmark_run_dir = tmp_path / "runs" / "benchmark_run"

    context = BenchmarkRunner().prepare_case_workspace(
        loaded.enabled_cases[0],
        benchmark_run_dir=benchmark_run_dir,
        benchmark_config=loaded.config,
    )

    assert context.task_config.project_path == context.run_case_dir / "workspace"
    assert context.task_config.test_command.command == "python -m unittest discover -s tests"


def test_benchmark_runner_executes_clean_copy_and_writes_aggregate_reports(tmp_path) -> None:
    case_dir = _write_unittest_case(tmp_path / "cases", "case_success")
    config_path = _benchmark_config(tmp_path, "case_success")

    result = BenchmarkRunner().run_config(config_path)

    assert result.total_cases == 1
    assert result.success_cases == 1
    assert result.success_rate == 1.0
    case_result = result.cases[0]
    assert case_result.case_id == "case_success"
    assert case_result.success is True
    assert case_result.run_case_dir != case_dir
    assert (case_result.run_dir / "final_report.md").exists()
    assert (result.benchmark_run_dir / "benchmark_result.json").exists()
    assert (result.benchmark_run_dir / "benchmark_report.md").exists()
    decisions = [
        json.loads(line)
        for line in (case_result.run_dir / "decision_trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert any(
        event["type"] == "human_decision"
        and event["decision_type"] == "approve"
        and event["auto"] is True
        for event in decisions
    )
    assert not (case_dir / "codeagent_runs").exists()


def test_benchmark_runner_runs_hidden_oracle_without_exposing_it_to_agent(
    tmp_path,
) -> None:
    case_dir = _write_unittest_case(
        tmp_path / "cases",
        "case_hidden_oracle",
        command="python -m unittest discover -s oracle_tests",
    )
    oracle_test = case_dir / "oracle_tests" / "test_oracle.py"
    oracle_test.write_text(
        "\n".join(
            [
                "import sys",
                "import unittest",
                "from pathlib import Path",
                "",
                "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'workspace'))",
                "from math_utils import add",
                "",
                "class OracleMathTest(unittest.TestCase):",
                "    def test_addition(self):",
                "        self.assertEqual(add(2, 5), 7)",
                "",
                "if __name__ == '__main__':",
                "    unittest.main()",
            ]
        ),
        encoding="utf-8",
    )
    config_path = _benchmark_config(tmp_path, "case_hidden_oracle")

    result = BenchmarkRunner().run_config(config_path)

    assert result.total_cases == 1
    case_result = result.cases[0]
    assert case_result.success is True
    assert case_result.oracle_success is True
    assert case_result.oracle_command == "python -m unittest discover -s oracle_tests"
    assert case_result.run_dir is not None
    task_config = (case_result.run_dir / "task_config.yaml").read_text(encoding="utf-8")
    assert "python -m unittest discover -s oracle_tests" not in task_config
    assert not (case_dir / "codeagent_runs").exists()
    assert not (case_dir / "case_runs").exists()


def test_prepare_case_workspace_preserves_nested_hidden_paths_in_copied_case(
    tmp_path,
) -> None:
    case_dir = _write_unittest_case(
        tmp_path / "cases",
        "case_nested_hidden",
        command="python -m unittest discover -s {{CASE_DIR}}/workspace/private_tests",
        hidden_paths="workspace/private_tests",
    )
    (case_dir / "workspace" / "private_tests").mkdir()
    (case_dir / "workspace" / "private_tests" / "test_hidden_oracle.py").write_text(
        "raise AssertionError('hidden oracle must not be compiled by agent smoke')\n",
        encoding="utf-8",
    )
    config_path = _benchmark_config(tmp_path, "case_nested_hidden")
    loaded = CaseLoader().load(config_path)
    benchmark_run_dir = tmp_path / "runs" / "benchmark_run"

    context = BenchmarkRunner().prepare_case_workspace(
        loaded.enabled_cases[0],
        benchmark_run_dir=benchmark_run_dir,
        benchmark_config=loaded.config,
    )

    assert (
        benchmark_run_dir
        / "case_workspaces"
        / "case_nested_hidden"
        / "workspace"
        / "private_tests"
    ) in context.hidden_paths
    assert context.oracle_command is not None
    assert "workspace/private_tests" in context.oracle_command.replace("\\", "/")
    assert "private_tests" not in context.task_config.test_command.command
    assert "test_hidden_oracle.py" not in context.task_config.test_command.command


def test_benchmark_runner_records_prepare_failure_and_continues_later_cases(
    tmp_path,
) -> None:
    bad_case = tmp_path / "cases" / "case_bad"
    bad_case.mkdir(parents=True)
    (bad_case / "task_config.yaml").write_text(
        """
case_id: case_bad
stages: [test]
project_path: missing_workspace
test_command:
  command: "python -m unittest discover -s tests"
""".strip(),
        encoding="utf-8",
    )
    _write_unittest_case(tmp_path / "cases", "case_good")
    config_path = _benchmark_config_for_cases(tmp_path, ["case_bad", "case_good"])

    result = BenchmarkRunner().run_config(config_path)

    assert result.total_cases == 2
    assert result.success_cases == 1
    bad_result, good_result = result.cases
    assert bad_result.case_id == "case_bad"
    assert bad_result.success is False
    assert "benchmark case preparation failed" in bad_result.failure_reason
    assert good_result.case_id == "case_good"
    assert good_result.success is True


def test_evaluator_requires_final_report_artifact(tmp_path) -> None:
    _write_unittest_case(tmp_path / "cases", "case_missing_report")
    config_path = _benchmark_config(tmp_path, "case_missing_report")
    loaded = CaseLoader().load(config_path)
    context = BenchmarkRunner().prepare_case_workspace(
        loaded.enabled_cases[0],
        benchmark_run_dir=tmp_path / "runs" / "benchmark_run",
        benchmark_config=loaded.config,
    )

    evaluation = CaseEvaluator().evaluate(
        context=context,
        run_dir=tmp_path / "missing_run",
        final_status="succeeded",
    )

    assert evaluation.success is False
    assert "final_report.md" in evaluation.failure_reason


def test_benchmark_cli_runs_config_and_prints_summary(tmp_path) -> None:
    _write_unittest_case(tmp_path / "cases", "case_cli")
    config_path = _benchmark_config(tmp_path, "case_cli")

    result = runner.invoke(app, ["benchmark", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Benchmark completed" in result.output
    assert "success_rate=1.00" in result.output
