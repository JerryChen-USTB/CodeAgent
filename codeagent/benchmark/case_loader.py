"""Load benchmark configs without reading hidden oracle material."""

from __future__ import annotations

from pathlib import Path

from codeagent.benchmark.schemas import BenchmarkCase, LoadedBenchmark
from codeagent.config.loader import load_benchmark_config


class CaseLoader:
    """Load benchmark case metadata from the top-level benchmark config."""

    def load(self, path: str | Path) -> LoadedBenchmark:
        config_path = Path(path).resolve()
        config = load_benchmark_config(config_path)
        cases = [
            BenchmarkCase(
                case_id=case.case_id,
                config_path=case.config,
                source_case_dir=case.config.parent,
                enabled=case.enabled,
                difficulty=case.difficulty,
                project_type=case.project_type,
                note=case.note,
            )
            for case in config.cases
        ]
        return LoadedBenchmark(config_path=config_path, config=config, cases=cases)
