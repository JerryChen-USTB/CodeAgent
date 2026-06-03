# CodeAgent

CodeAgent is a local CLI software-engineering agent for Python projects. It uses
LangGraph and LangChain to run the workflow `implement -> test -> debug -> repair`,
stores every run as auditable artifacts, and can execute isolated benchmark suites.

## Setup

```powershell
python -m pip install -e .
python -m codeagent --help
python -m pytest -q
```

CodeAgent targets Python 3.11+ and uses OpenRouter through an OpenAI-compatible
LangChain client. The current temporary cost-control default model is
`anthropic/claude-sonnet-4.6`.

## OpenRouter Key

Set the API key in the environment. Do not put the key in configs, docs, or Git.

```powershell
[Environment]::SetEnvironmentVariable("OPENROUTER_API_KEY", "<your-key>", "User")
```

The runtime records only the environment variable name. Secret values are not
printed, logged, or written to reports.

## CLI

```powershell
python -m codeagent --help
python -m codeagent run --config examples/task.yaml
python -m codeagent implement --project ./repo --requirements requirements.md
python -m codeagent test --project ./repo --test-cmd "pytest -q"
python -m codeagent debug --project ./repo --test-cmd "pytest -q"
python -m codeagent repair --project ./repo --test-cmd "pytest -q"
python -m codeagent benchmark --config benchmark/benchmark.yaml
python -m codeagent benchmark --config benchmark/selfbuilt/selfbuilt_benchmark.yaml
python -m codeagent resume --run-id <run_id>
```

Task configs define stages, project path, input materials, test command, visibility
rules, model settings, and approval/runtime policy. In benchmark mode, patch and
test approvals are auto-approved but still written to the decision trace.

## Run Outputs

Each normal run writes a directory under `codeagent_runs/<run_id>/` unless an
output directory is configured. Important files include:

- `metadata.json`
- `task_config.yaml`
- `transcript.jsonl`
- `decision_trace.jsonl`
- `artifacts_index.json`
- `checkpoints.sqlite`
- per-stage reports, patches, logs, and `stage_result.json`
- `final_report.md`

Benchmark runs write aggregate `benchmark_result.json` and `benchmark_report.md`
plus clean per-case workspaces and oracle logs under the benchmark output root.
Original benchmark case templates are copied before execution and are checked for
unchanged source snapshots.

## Resume

`resume --run-id <run_id>` reloads the saved normalized config and checkpoint when
available. Completed runs can be inspected through their artifacts even when there
is no pending interrupt. Approval decisions are recorded so side effects are not
silently repeated.

## Benchmarks

Public benchmark:

```powershell
python -m codeagent benchmark --config benchmark/benchmark.yaml
```

Self-built benchmark:

```powershell
python -m codeagent benchmark --config benchmark/selfbuilt/selfbuilt_benchmark.yaml
```

The BugsInPy case is environment-gated. If WSL/conda/Python 3.8.3 readiness is
missing, the benchmark records an explicit blocker instead of silently skipping it.

## Troubleshooting

- Missing API key: set `OPENROUTER_API_KEY` at process or user level.
- Unsafe shell command: use an allowed `pytest`, `unittest`, or `py_compile`
  command, or adjust the task config.
- Hidden benchmark paths: do not reference `oracle_tests`, `evaluation`, or
  `expected_result.json` in agent-visible configs.
- Windows path length: CodeAgent uses long-path-aware artifact writes for run
  outputs, patch artifacts, reports, and benchmark logs.

Developer milestone reports are in `docs/dev_reports/`. The implementation plan
and running log are in `docs/codex/plans.md`.
