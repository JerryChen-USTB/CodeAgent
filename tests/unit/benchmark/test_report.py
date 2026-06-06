from __future__ import annotations

import json
import os
from pathlib import Path

from codeagent.benchmark.report import write_benchmark_reports
from codeagent.benchmark.schemas import BenchmarkResult, CaseEvaluation


def _long_readable_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    return Path("\\\\?\\" + str(path.resolve()))


def test_write_benchmark_reports_supports_long_windows_run_dir(tmp_path) -> None:
    benchmark_run_dir = tmp_path / "benchmark_run"
    while len(str(benchmark_run_dir / "benchmark_result.json")) < 285:
        benchmark_run_dir = benchmark_run_dir / "deep_segment_for_windows_path_limit"
    result = BenchmarkResult(
        benchmark_id="long_path_benchmark",
        benchmark_run_dir=benchmark_run_dir,
        total_cases=1,
        success_cases=1,
        failed_cases=0,
        success_rate=1.0,
        cases=[
            CaseEvaluation(
                case_id="case_pass",
                success=True,
                score=1.0,
                final_status="succeeded",
                run_dir=benchmark_run_dir / "case_runs" / "case_pass",
                run_case_dir=benchmark_run_dir / "case_workspaces" / "case_pass",
                source_unchanged=True,
            )
        ],
    )

    json_path, markdown_path = write_benchmark_reports(result)

    readable_run_dir = _long_readable_path(benchmark_run_dir)
    assert json_path == benchmark_run_dir / "benchmark_result.json"
    assert markdown_path == benchmark_run_dir / "benchmark_report.md"
    payload = json.loads(
        (readable_run_dir / "benchmark_result.json").read_text(encoding="utf-8")
    )
    assert payload["success_rate"] == 1.0
    assert (readable_run_dir / "benchmark_report.md").exists()
