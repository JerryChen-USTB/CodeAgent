"""Benchmark aggregate report persistence."""

from __future__ import annotations

import json
from pathlib import Path

from codeagent import filesystem as fs
from codeagent.benchmark.schemas import BenchmarkResult


def write_benchmark_reports(result: BenchmarkResult) -> tuple[Path, Path]:
    fs.mkdir(result.benchmark_run_dir)
    json_path = result.benchmark_run_dir / "benchmark_result.json"
    markdown_path = result.benchmark_run_dir / "benchmark_report.md"
    fs.write_text(
        json_path,
        json.dumps(result.to_json_dict(), indent=2, ensure_ascii=False),
    )
    fs.write_text(markdown_path, _render_markdown(result))
    return json_path, markdown_path


def _render_markdown(result: BenchmarkResult) -> str:
    lines = [
        "# Benchmark Report",
        "",
        f"- Benchmark ID: {result.benchmark_id}",
        f"- Total cases: {result.total_cases}",
        f"- Success cases: {result.success_cases}",
        f"- Failed cases: {result.failed_cases}",
        f"- Blocked cases: {result.blocked_cases}",
        f"- Success rate: {result.success_rate:.2f}",
        "",
        (
            "| case_id | success | final_status | oracle_success | source_unchanged | score | "
            "run_dir | failure_reason |"
        ),
        "|---|---|---|---|---|---:|---|---|",
    ]
    for case in result.cases:
        lines.append(
            "| "
            f"{_cell(case.case_id)} | "
            f"{case.success} | "
            f"{_cell(case.final_status)} | "
            f"{case.oracle_success if case.oracle_success is not None else '-'} | "
            f"{case.source_unchanged if case.source_unchanged is not None else '-'} | "
            f"{case.score:.2f} | "
            f"{_cell(case.run_dir.as_posix() if case.run_dir else '-')} | "
            f"{_cell(case.failure_reason or '-')} |"
        )
    if result.blockers:
        lines.extend(
            [
                "",
                "## Blockers",
                "",
                "| case_id | final_status | reason |",
                "|---|---|---|",
            ]
        )
        for case in result.blockers:
            lines.append(
                "| "
                f"{_cell(case.case_id)} | "
                f"{_cell(case.final_status)} | "
                f"{_cell(case.failure_reason or '-')} |"
            )
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")
