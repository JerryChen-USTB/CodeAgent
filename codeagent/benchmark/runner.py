"""Benchmark runner with clean per-case workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from codeagent import filesystem as fs
from codeagent.benchmark.case_loader import CaseLoader
from codeagent.benchmark.environment import (
    BugsInPyEnvironmentDetector,
    EnvironmentStatus,
)
from codeagent.benchmark.evaluator import CaseEvaluator
from codeagent.benchmark.report import write_benchmark_reports
from codeagent.benchmark.schemas import (
    BenchmarkCase,
    BenchmarkResult,
    CaseEvaluation,
    CaseExecutionContext,
)
from codeagent.cli.executor import execute_task_config
from codeagent.config import defaults
from codeagent.cli.progress import ProgressReporter
from codeagent.config.schema import BenchmarkConfig
from codeagent.config.loader import load_task_config


HIDDEN_BENCHMARK_NAMES = {"evaluation", "oracle_tests", "expected_result.json"}
PrepareExecutorResult = tuple[int, Path, Path, str]


class BenchmarkRunner:
    def __init__(
        self,
        *,
        loader: CaseLoader | None = None,
        evaluator: CaseEvaluator | None = None,
        reporter: ProgressReporter | None = None,
        environment_detectors: dict[str, object] | None = None,
        prepare_executor=None,
    ) -> None:
        self.loader = loader or CaseLoader()
        self.evaluator = evaluator or CaseEvaluator()
        self.reporter = reporter or ProgressReporter()
        self.environment_detectors = environment_detectors or {
            "bugsinpy_wsl_conda": BugsInPyEnvironmentDetector()
        }
        self.prepare_executor = prepare_executor or _run_prepare_command

    def run_config(self, path: str | Path) -> BenchmarkResult:
        loaded = self.loader.load(path)
        return self.run_all(loaded.config, loaded.enabled_cases, blocked_cases=loaded.blocked_cases)

    def run_all(
        self,
        config: BenchmarkConfig,
        cases: list[BenchmarkCase],
        *,
        blocked_cases: list[BenchmarkCase] | None = None,
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
                blocked = self._environment_blocker(context)
                if blocked is not None:
                    evaluations.append(blocked)
                    continue
                prepare_failed = self._prepare_case(context, benchmark_run_dir)
                if prepare_failed is not None:
                    evaluations.append(prepare_failed)
                    continue
                cli_result = execute_task_config(
                    context.task_config,
                    reporter=self.reporter,
                )
                evaluation = self.evaluator.evaluate(
                    context=context,
                    run_dir=cli_result.run_dir,
                    final_status=cli_result.final_status,
                )
                evaluations.append(_attach_source_snapshot(evaluation, context))
            except Exception as exc:  # pragma: no cover - defensive per-case isolation
                evaluation = CaseEvaluation(
                    case_id=context.case_id,
                    success=False,
                    score=0.0,
                    final_status="failed",
                    run_dir=None,
                    run_case_dir=context.run_case_dir,
                    failure_reason=f"benchmark case execution failed: {exc}",
                )
                evaluations.append(_attach_source_snapshot(evaluation, context))
        disabled_blockers = [
            _blocked_case_evaluation(case, benchmark_run_dir=benchmark_run_dir)
            for case in blocked_cases or []
        ]
        blocked_evaluations = [
            evaluation for evaluation in evaluations if evaluation.final_status == "blocked"
        ]
        blockers = [*disabled_blockers, *blocked_evaluations]
        success_cases = sum(1 for evaluation in evaluations if evaluation.success)
        total_cases = len(evaluations)
        result = BenchmarkResult(
            benchmark_id=benchmark_id,
            benchmark_run_dir=benchmark_run_dir,
            total_cases=total_cases,
            success_cases=success_cases,
            failed_cases=total_cases - success_cases - len(blocked_evaluations),
            blocked_cases=len(blockers),
            success_rate=(success_cases / total_cases) if total_cases else 0.0,
            cases=evaluations,
            blockers=blockers,
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
        if fs.exists(run_case_dir):
            raise FileExistsError(f"benchmark run case directory already exists: {run_case_dir}")
        source_snapshot_before = _snapshot_case_dir(case.source_case_dir)
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
        oracle_timeout_seconds = task_config.test_command.timeout_seconds
        if _hidden_command_error(command, hidden_paths=hidden_paths, cwd=run_case_dir):
            oracle_command = command
            task_config.test_command.command = _agent_safe_test_command(
                task_config.project_path,
                hidden_paths=hidden_paths,
            )
            task_config.test_command.timeout_seconds = max(
                task_config.test_command.timeout_seconds,
                task_config.runtime.command_timeout_seconds,
                defaults.DEFAULT_COMMAND_TIMEOUT_SECONDS,
            )
        else:
            task_config.test_command.command = _normalize_project_relative_command(
                command,
                project_path=task_config.project_path,
                run_case_dir=run_case_dir,
            )
        if task_config.prepare_command is not None:
            task_config.prepare_command.command = task_config.prepare_command.command.replace(
                "{{CASE_DIR}}",
                run_case_dir.as_posix(),
            )
        return CaseExecutionContext(
            case_id=case.case_id,
            source_case_dir=case.source_case_dir,
            run_case_dir=run_case_dir,
            copied_config_path=copied_config_path,
            task_config=task_config,
            visible_paths=visible_paths,
            hidden_paths=hidden_paths,
            source_snapshot_before=source_snapshot_before,
            oracle_logs_dir=(
                benchmark_run_dir / "oracle_logs" / case.case_id
                if oracle_command
                else None
            ),
            oracle_command=oracle_command,
            oracle_framework=task_config.test_framework,
            oracle_timeout_seconds=oracle_timeout_seconds if oracle_command else None,
        )

    def _prepare_case(
        self,
        context: CaseExecutionContext,
        benchmark_run_dir: Path,
    ) -> CaseEvaluation | None:
        command = (
            context.task_config.prepare_command.command
            if context.task_config.prepare_command is not None
            else None
        )
        if not command:
            return None
        timeout_seconds = context.task_config.prepare_command.timeout_seconds
        logs_dir = benchmark_run_dir / "prepare_logs" / context.case_id
        allowed_error = _prepare_command_error(command, run_case_dir=context.run_case_dir)
        if allowed_error:
            return _attach_source_snapshot(
                CaseEvaluation(
                    case_id=context.case_id,
                    success=False,
                    score=0.0,
                    final_status="failed",
                    run_dir=None,
                    run_case_dir=context.run_case_dir,
                    failure_reason=allowed_error,
                ),
                context,
            )
        exit_code, stdout_log, stderr_log, error = self.prepare_executor(
            command,
            cwd=Path.cwd(),
            logs_dir=logs_dir,
            timeout_seconds=timeout_seconds,
        )
        if exit_code == 0:
            return None
        reason = f"BugsInPy prepare command failed with exit_code={exit_code}"
        if error:
            reason += f": {error}"
        reason += f"; stdout_log={stdout_log.as_posix()}; stderr_log={stderr_log.as_posix()}"
        return _attach_source_snapshot(
            CaseEvaluation(
                case_id=context.case_id,
                success=False,
                score=0.0,
                final_status="failed",
                run_dir=None,
                run_case_dir=context.run_case_dir,
                failure_reason=reason,
            ),
            context,
        )

    def _environment_blocker(
        self,
        context: CaseExecutionContext,
    ) -> CaseEvaluation | None:
        detector_key = _environment_detector_key(context.task_config)
        if detector_key is None:
            return None
        detector = self.environment_detectors.get(detector_key)
        if detector is None:
            status = EnvironmentStatus(
                name=detector_key,
                available=False,
                blockers=[f"environment detector is not configured: {detector_key}"],
            )
        else:
            status = detector.detect()  # type: ignore[attr-defined]
        if status.available:
            return None
        reason = "; ".join(status.blockers) or f"environment is unavailable: {status.name}"
        evaluation = CaseEvaluation(
            case_id=context.case_id,
            success=False,
            score=0.0,
            final_status="blocked",
            run_dir=None,
            run_case_dir=context.run_case_dir,
            failure_reason=reason,
        )
        return _attach_source_snapshot(evaluation, context)


def _create_benchmark_run_dir(output_root: Path, *, benchmark_id: str) -> Path:
    fs.mkdir(output_root)
    for _ in range(20):
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S_%f")
        digest = hashlib.sha256(f"{benchmark_id}|{stamp}".encode("utf-8")).hexdigest()[:6]
        path = output_root / f"{stamp}_{benchmark_id}_{digest}"
        try:
            fs.mkdir(path, exist_ok=False)
        except FileExistsError:
            continue
        return path
    raise RuntimeError("Unable to create a unique benchmark run directory.")


def _run_prepare_command(
    command: str,
    *,
    cwd: Path,
    logs_dir: Path,
    timeout_seconds: int,
) -> PrepareExecutorResult:
    fs.mkdir(logs_dir)
    stdout_log = logs_dir / "prepare.stdout.log"
    stderr_log = logs_dir / "prepare.stderr.log"
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        fs.write_text(stdout_log, completed.stdout)
        fs.write_text(stderr_log, completed.stderr)
        return completed.returncode, stdout_log, stderr_log, ""
    except subprocess.TimeoutExpired as exc:
        stdout = _coerce_process_output(exc.stdout)
        stderr = _coerce_process_output(exc.stderr)
        stderr = f"{stderr}\nCommand timed out after {timeout_seconds} seconds.".strip()
        fs.write_text(stdout_log, stdout)
        fs.write_text(stderr_log, stderr + "\n")
        return 124, stdout_log, stderr_log, "prepare command timed out"


def _blocked_case_evaluation(
    case: BenchmarkCase,
    *,
    benchmark_run_dir: Path,
) -> CaseEvaluation:
    reason = "disabled optional benchmark case"
    if case.note:
        reason += f": {case.note}"
    return CaseEvaluation(
        case_id=case.case_id,
        success=False,
        score=0.0,
        final_status="blocked",
        run_dir=None,
        run_case_dir=benchmark_run_dir / "case_workspaces" / case.case_id,
        failure_reason=reason,
    )


def _environment_detector_key(task_config) -> str | None:
    environment = getattr(task_config, "execution_environment", None)
    recommended = getattr(environment, "recommended", None)
    if recommended == "wsl_conda":
        return "bugsinpy_wsl_conda"
    return None


def _prepare_command_error(command: str, *, run_case_dir: Path) -> str | None:
    lowered = command.lower().replace("\\", "/")
    if any(separator in lowered for separator in (";", "&&", "||", "|", "`n", "`r")):
        return "prepare command is not allowed: shell chaining is not allowed"
    if "prepare_bugsinpy_wsl_conda.ps1" not in lowered:
        return "prepare command is not allowed: expected prepare_bugsinpy_wsl_conda.ps1"
    if "-casedir" not in lowered:
        return "prepare command is not allowed: missing -CaseDir"
    expected = run_case_dir.as_posix().lower()
    if expected not in lowered:
        return "prepare command is not allowed: -CaseDir must reference the copied case"
    return None


def _attach_source_snapshot(
    evaluation: CaseEvaluation,
    context: CaseExecutionContext,
) -> CaseEvaluation:
    source_snapshot_after = _snapshot_case_dir(context.source_case_dir)
    source_snapshot_before = context.source_snapshot_before
    source_unchanged = (
        source_snapshot_before == source_snapshot_after
        if source_snapshot_before is not None
        else None
    )
    if source_unchanged is False:
        evaluation = replace(
            evaluation,
            success=False,
            score=0.0,
            failure_reason=_append_reason(
                evaluation.failure_reason,
                "source case changed during benchmark run",
            ),
        )
    return replace(
        evaluation,
        source_snapshot_before=source_snapshot_before,
        source_snapshot_after=source_snapshot_after,
        source_unchanged=source_unchanged,
    )


def _snapshot_case_dir(path: Path) -> str:
    root = path.resolve()
    entries: list[dict[str, object]] = []
    for candidate in sorted(root.rglob("*")):
        if not fs.is_file(candidate):
            continue
        stat = candidate.stat()
        relative = candidate.relative_to(root).as_posix()
        entries.append(
            {
                "path": relative,
                "size": stat.st_size,
                "sha256": hashlib.sha256(fs.read_bytes(candidate)).hexdigest(),
            }
        )
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _append_reason(existing: str, reason: str) -> str:
    return f"{existing}; {reason}" if existing else reason


def _coerce_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


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
        fs.write_text(smoke_file, "# benchmark smoke file for visible workflow tests\n")
        python_files = [smoke_file]
    relative_files = [
        _quote_command_path(path.relative_to(project_path).as_posix())
        for path in python_files
    ]
    return "python -m py_compile " + " ".join(relative_files)


def _normalize_project_relative_command(
    command: str,
    *,
    project_path: Path,
    run_case_dir: Path,
) -> str:
    try:
        tokens = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return command
    changed = False
    normalized_tokens: list[str] = []
    for token in tokens:
        normalized = _normalize_command_token(
            token,
            project_path=project_path,
            run_case_dir=run_case_dir,
        )
        if normalized != token:
            changed = True
        normalized_tokens.append(normalized)
    if not changed:
        return command
    return " ".join(_quote_command_path(token) for token in normalized_tokens)


def _normalize_command_token(
    token: str,
    *,
    project_path: Path,
    run_case_dir: Path,
) -> str:
    token = _strip_outer_quotes(token)
    if "=" in token and token.startswith("-"):
        option, value = token.split("=", 1)
        normalized_value = _normalize_path_value(
            value,
            project_path=project_path,
            run_case_dir=run_case_dir,
        )
        return f"{option}={normalized_value}"
    return _normalize_path_value(
        token,
        project_path=project_path,
        run_case_dir=run_case_dir,
    )


def _normalize_path_value(
    value: str,
    *,
    project_path: Path,
    run_case_dir: Path,
) -> str:
    unquoted = _strip_outer_quotes(value)
    path = Path(unquoted)
    if not _looks_like_path(path):
        return unquoted
    project_root = project_path.resolve()
    case_root = run_case_dir.resolve()
    candidate = path.resolve() if path.is_absolute() else (case_root / path).resolve()
    if _is_relative_to(candidate, project_root) and fs.exists(candidate):
        return candidate.relative_to(project_root).as_posix()
    if path.is_absolute():
        return unquoted
    parts = path.parts
    if not parts or parts[0] != project_root.name:
        return unquoted
    stripped = Path(*parts[1:]) if len(parts) > 1 else Path(".")
    stripped_candidate = (project_root / stripped).resolve()
    if fs.exists(stripped_candidate) or fs.exists(stripped_candidate.parent):
        return stripped.as_posix()
    return unquoted


def _strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


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
