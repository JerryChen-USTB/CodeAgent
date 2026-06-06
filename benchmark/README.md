# CodeAgent Benchmark

This directory contains benchmark inputs derived from `dataset/` and reshaped for the software-engineering agent described in the SRS. The main config is `benchmark.yaml`.

## Output Directory Convention

Benchmark source material stays under `benchmark/`; generated run artifacts now
go under the repository-level `codeagent_runs/benchmarks/` directory:

```text
codeagent_runs/
  benchmarks/
    public/
      <timestamp>_<benchmark_id>_<hash>/
        benchmark_report.md
        benchmark_result.json
        case_workspaces/
        case_runs/
        oracle_logs/
    selfbuilt/
      <timestamp>_<benchmark_id>_<hash>/
```

Do not put new benchmark outputs under `benchmark/codeagent_runs/` or
`benchmark/selfbuilt/codeagent_runs/`. Those locations may still exist as
historical ignored artifacts from earlier validation runs, but new runs should
use the centralized output roots configured in the benchmark YAML files.

## Case Layout

Each case directory contains:

- `task_config.yaml`: case-level task configuration.
- `input/`: visible requirements, bug reports, failing-test notes, or logs.
- `workspace/`: the project skeleton or buggy project that the agent may edit.
- `evaluation/`: oracle tests used by a benchmark runner. Function-level implementation cases should hide this directory from the agent.
- `expected_result.json`: success criteria and answer-isolation notes.

## Case Reuse Rule

Benchmark runs must not edit the original case directories. A runner should copy
the entire selected case to a clean per-run workspace first, then allow the agent
and test command to operate only on that copy. Hidden paths such as `evaluation/`
and `expected_result.json` remain hidden from the agent in the copy and are used
only by the runner for scoring. This keeps the source cases reusable across
repeated benchmark runs.

If a `task_config.yaml` command needs the case directory, use the
`{{CASE_DIR}}` placeholder. The benchmark runner must replace it with the clean
copied case directory before execution.

## Enabled Cases

- `humaneval_000_has_close_elements`
- `humaneval_001_separate_paren_groups`
- `mbpp_002_similar_elements`
- `mbpp_003_is_not_prime`
- `quixbugs_gcd`
- `quixbugs_find_in_sorted`

## Optional Cases

- `bugsinpy_black_001`: disabled by default because it needs WSL and the `codeagent-bugsinpy-py383` conda environment. To run it, first copy the original case to a clean `<copied_case_dir>`, then run the prepare/test wrappers with `-CaseDir <copied_case_dir>`. The prepare/test steps call the official BugsInPy `bugsinpy-checkout`, `bugsinpy-compile`, and `bugsinpy-test` scripts against the copied case.

SWE-bench Lite is not converted yet because it needs a dedicated harness, repository checkout, and Docker-style evaluation environment.

## Change Log

| Date | Change | Reason |
|---|---|---|
| 2026-06-03 | Added case reuse rule requiring clean per-run copies and `{{CASE_DIR}}` command substitution. | Keep original benchmark cases reusable and prevent accidental pollution. |
