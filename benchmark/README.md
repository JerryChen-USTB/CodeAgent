# CodeAgent Benchmark

This directory contains benchmark inputs derived from `dataset/` and reshaped for the software-engineering agent described in the SRS. The main config is `benchmark.yaml`.

## Case Layout

Each case directory contains:

- `task_config.yaml`: case-level task configuration.
- `input/`: visible requirements, bug reports, failing-test notes, or logs.
- `workspace/`: the project skeleton or buggy project that the agent may edit.
- `evaluation/`: oracle tests used by a benchmark runner. Function-level implementation cases should hide this directory from the agent.
- `expected_result.json`: success criteria and answer-isolation notes.

## Enabled Cases

- `humaneval_000_has_close_elements`
- `humaneval_001_separate_paren_groups`
- `mbpp_002_similar_elements`
- `mbpp_003_is_not_prime`
- `quixbugs_gcd`
- `quixbugs_find_in_sorted`

## Optional Cases

- `bugsinpy_black_001`: workspace can be prepared with `powershell -ExecutionPolicy Bypass -File scripts/prepare_bugsinpy_wsl_conda.ps1 -CaseDir benchmark/cases/bugsinpy_black_001`; disabled by default because it needs WSL and the `codeagent-bugsinpy-py383` conda environment. The prepare/test steps call the official BugsInPy `bugsinpy-checkout`, `bugsinpy-compile`, and `bugsinpy-test` scripts.

SWE-bench Lite is not converted yet because it needs a dedicated harness, repository checkout, and Docker-style evaluation environment.
