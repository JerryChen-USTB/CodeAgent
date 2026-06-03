"""Benchmark runner with clean per-case workspaces."""

from __future__ import annotations

import hashlib
import shlex
import shutil
from datetime import datetime, timezone
from pathlib import Path

from codeagent.benchmark.case_loader import CaseLoader
from codeagent.benchmark.evaluator import CaseEvaluator
from codeagent.benchmark.report import write_benchmark_reports
from codeagent.benchmark.schemas import (
    BenchmarkCase,
    BenchmarkResult,
    CaseEvaluation,
    CaseExecutionContext,
)
from codeagent.cli.executor import execute_task_config
from codeagent.cli.progress import ProgressReporter
from codeagent.config.schema import BenchmarkConfig
from codeagent.config.loader import load_task_config


HIDDEN_BENCHMARK_NAMES = {"evaluation", "oracle_tests", "expected_result.json"}


class BenchmarkRunner:
    def __init__(
        self,
        *,
        loader: CaseLoader | None = None,
        evaluator: CaseEvaluator | None = None,
        reporter: ProgressReporter | None = None,
    ) -> None:
        self.loader = loader or CaseLoader()
        self.evaluator = evaluator or CaseEvaluator()
        self.reporter = reporter or ProgressReporter()

    def run_config(self, path: str | Path) -> BenchmarkResult:
        loaded = self.loader.load(path)
        return self.run_all(loaded.config, loaded.enabled_cases)

    def run_all(
        self,
        config: BenchmarkConfig,
        cases: list[BenchmarkCase],
    ) -> BenchmarkResult:
        benchmark_id = config.benchmark_id or config.name or "benchmark"
        benchmark_run_dir = _create_benchmark_run_dir(
            config.output_dir or config.default_output_dir,
            benchmark_id=benchmark_id,
        )
        evaluations: list[CaseEvaluation] = []
        for case in cases:
            try:
                context = self.prepare_case_workspace(
                    case,
                    benchmark_run_dir=benchmark_run_dir,
                    benchmark_config=config,
                )
            except Exception as exc:  # pragma: no cover - defensive per-case isolation
                evaluations.append(
                    CaseEvaluation(
                        case_id=case.case_id,
                        success=False,
                        score=0.0,
                        final_status="failed",
                        run_dir=None,
                        run_case_dir=benchmark_run_dir / "case_workspaces" / case.case_id,
                        failure_reason=f"benchmark case preparation failed: {exc}",
                    )
                )
                continue
            try:
                cli_result = execute_task_config(
                    context.task_config,
                    reporter=self.reporter,
                )
                evaluations.append(
                    self.evaluator.evaluate(
                        context=context,
                        run_dir=cli_result.run_dir,
                        final_status=cli_result.final_status,
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive per-case isolation
                evaluations.append(
                    CaseEvaluation(
                        case_id=context.case_id,
                        success=False,
                        score=0.0,
                        final_status="failed",
                        run_dir=None,
                        run_case_dir=context.run_case_dir,
                        failure_reason=f"benchmark case execution failed: {exc}",
                    )
                )
        success_cases = sum(1 for evaluation in evaluations if evaluation.success)
        total_cases = len(evaluations)
        result = BenchmarkResult(
            benchmark_id=benchmark_id,
            benchmark_run_dir=benchmark_run_dir,
            total_cases=total_cases,
            success_cases=success_cases,
            failed_cases=total_cases - success_cases,
            success_rate=(success_cases / total_cases) if total_cases else 0.0,
            cases=evaluations,
        )
        write_benchmark_reports(result)
        return result

    def prepare_case_workspace(
        self,
        case: BenchmarkCase,
        *,
        benchmark_run_dir: Path,
        benchmark_config: BenchmarkConfig,
    ) -> CaseExecutionContext:
        run_case_dir = benchmark_run_dir / "case_workspaces" / case.case_id
        if run_case_dir.exists():
            raise FileExistsError(f"benchmark run case directory already exists: {run_case_dir}")
        shutil.copytree(case.source_case_dir, run_case_dir)
        relative_config = case.config_path.resolve().relative_to(
            case.source_case_dir.resolve()
        )
        copied_config_path = run_case_dir / relative_config
        task_config = load_task_config(copied_config_path)
        task_config.mode = "benchmark"
        task_config.output_dir = benchmark_run_dir / "case_runs" / case.case_id
        task_config.runtime.auto_approve_in_benchmark = True
        task_config.auto_approve_in_benchmark = True
        visible_paths = _case_visibility_paths(
            task_config.agent_visibility.visible_paths,
            benchmark_config.default_agent_visible_paths,
            run_case_dir=run_case_dir,
            source_case_dir=case.source_case_dir,
        )
        hidden_paths = _case_visibility_paths(
            task_config.agent_visibility.hidden_paths,
            benchmark_config.default_hidden_paths,
            run_case_dir=run_case_dir,
            source_case_dir=case.source_case_dir,
        )
        task_config.agent_visibility.visible_paths = visible_paths
        task_config.agent_visibility.hidden_paths = hidden_paths
        command = task_config.test_command.command.replace(
            "{{CASE_DIR}}",
            run_case_dir.as_posix(),
        )
        oracle_command: str | None = None
        if _hidden_command_error(command, hidden_paths=hidden_paths, cwd=run_case_dir):
            oracle_command = command
            task_config.test_command.command = _agent_safe_test_command(
                task_config.project_path,
                hidden_paths=hidden_paths,
            )
        else:
            task_config.test_command.command = command
        return CaseExecutionContext(
            case_id=case.case_id,
            source_case_dir=case.source_case_dir,
            run_case_dir=run_case_dir,
            copied_config_path=copied_config_path,
            task_config=task_config,
            visible_paths=visible_paths,
            hidden_paths=hidden_paths,
            oracle_logs_dir=(
                benchmark_run_dir / "oracle_logs" / case.case_id
                if oracle_command
                else None
            ),
            oracle_command=oracle_command,
            oracle_framework=task_config.test_framework,
        )


def _create_benchmark_run_dir(output_root: Path, *, benchmark_id: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S_%f")
        digest = hashlib.sha256(f"{benchmark_id}|{stamp}".encode("utf-8")).hexdigest()[:6]
        path = output_root / f"{stamp}_{benchmark_id}_{digest}"
        try:
            path.mkdir(parents=True)
        except FileExistsError:
            continue
        return path
    raise RuntimeError("Unable to create a unique benchmark run directory.")


def _case_visibility_paths(
    task_paths: list[Path],
    default_paths: list[Path],
    *,
    run_case_dir: Path,
    source_case_dir: Path,
) -> list[Path]:
    resolved: list[Path] = []
    for path in [*task_paths, *default_paths]:
        mapped = _map_case_path(path, run_case_dir=run_case_dir, source_case_dir=source_case_dir)
        if mapped not in resolved:
            resolved.append(mapped)
    return resolved


def _map_case_path(path: Path, *, run_case_dir: Path, source_case_dir: Path) -> Path:
    resolved = path.resolve()
    run_root = run_case_dir.resolve()
    if _is_relative_to(resolved, run_root):
        return resolved
    try:
        relative = resolved.relative_to(source_case_dir.resolve())
    except ValueError:
        relative = Path(path.name)
    return (run_root / relative).resolve()


def _hidden_command_error(
    command: str,
    *,
    hidden_paths: list[Path],
    cwd: Path | None = None,
) -> str | None:
    lowered = command.lower()
    for hidden_name in HIDDEN_BENCHMARK_NAMES:
        if hidden_name in lowered:
            return f"hidden benchmark path referenced by command: {hidden_name}"
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        token_path = Path(token.strip("\"'"))
        if not _looks_like_path(token_path):
            continue
        try:
            resolved = (
                token_path.resolve()
                if token_path.is_absolute() or cwd is None
                else (cwd / token_path).resolve()
            )
        except OSError:
            continue
        if any(_is_relative_to(resolved, hidden.resolve()) for hidden in hidden_paths):
            return f"hidden benchmark path referenced by command: {token}"
    return None


def _agent_safe_test_command(project_path: Path, *, hidden_paths: list[Path]) -> str:
    hidden_roots = [path.resolve() for path in hidden_paths]
    python_files = [
        path
        for path in sorted(project_path.rglob("*.py"))
        if "__pycache__" not in path.parts
        and not any(_is_relative_to(path.resolve(), hidden) for hidden in hidden_roots)
    ]
    if not python_files:
        smoke_file = project_path / "__codeagent_benchmark_smoke.py"
        smoke_file.write_text("# benchmark smoke file for visible workflow tests\n", encoding="utf-8")
        python_files = [smoke_file]
    relative_files = [
        _quote_command_path(path.relative_to(project_path).as_posix())
        for path in python_files
    ]
    return "python -m py_compile " + " ".join(relative_files)


def _quote_command_path(path: str) -> str:
    if any(char.isspace() for char in path):
        return f'"{path}"'
    return path


def _looks_like_path(path: Path) -> bool:
    text = path.as_posix()
    return "/" in text or "\\" in text or path.suffix != ""


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
