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
python -m codeagent benchmark --config benchmark/selfbuilt/meeting_room_demo_benchmark.yaml
python -m codeagent benchmark --config benchmark/selfbuilt/selfbuilt_benchmark.yaml
python -m codeagent resume --run-id <run_id>
```

Task configs define stages, project path, input materials, test command, visibility
rules, model settings, and approval/runtime policy. In benchmark mode, patch and
test approvals are auto-approved but still written to the decision trace.

`python -m codeagent wizard` opens a Chinese semi-interactive form. In a real
terminal, stage selection and input-material selection use arrow-key choices and
multi-select controls. After the user confirms the form, CodeAgent starts the
agent run immediately while still saving the normalized `task_config.yaml` for
audit and reproduction.

The wizard also lets the user choose the approval mode. The default is manual
approval, which prompts before applying implementation patches, testing plans,
testing patches, and test commands. If the user chooses automatic approval, the
run continues without prompts and records `decision_source=user_configured_auto`.
Benchmark auto-approval records `decision_source=benchmark_auto`.

## Run Outputs

Each normal run writes a directory under `codeagent_runs/<run_id>/` unless an
output directory is configured. Important files include:

- `metadata.json`
- `task_config.yaml`
- `transcript.jsonl`
- `decision_trace.jsonl`
- `workflow.log`
- `workflow_events.jsonl`
- `artifacts_index.json`
- `checkpoints.sqlite`
- per-stage reports, patches, logs, and `stage_result.json`
- `final_report.md`

`decision_trace.jsonl` is the compact approval and routing audit trail. New runs
also write `workflow.log`, a chronological human-readable trace containing stage
and node transitions, LLM prompts/responses after redaction, structured plans,
approval requests/results, patch application, command execution, and final stage
summaries. `workflow_events.jsonl` stores the same trace as machine-readable
events.

In manual approval mode, plan review prompts are intentionally limited to two
choices: approve the plan, or provide feedback and ask the Agent to regenerate
the plan. Reject/cancel style decisions are reserved for side-effect approvals
such as applying patches and running commands.

Benchmark runs write aggregate `benchmark_result.json` and `benchmark_report.md`
plus clean per-case workspaces and oracle logs under the benchmark output root.
Original benchmark case templates are copied before execution and are checked for
unchanged source snapshots. Each benchmark case records both Agent-visible
self-tests (`agent_test_success`, `agent_test_total`, `agent_test_command`) and
runner-only hidden oracle evaluation (`oracle_success`). A zero-test Agent
self-test is treated as a failed verification, not as success.

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
python -m codeagent benchmark --config benchmark/selfbuilt/meeting_room_demo_benchmark.yaml
python -m codeagent benchmark --config benchmark/selfbuilt/selfbuilt_benchmark.yaml
```

`meeting_room_demo_benchmark.yaml` runs only the meeting-room Flask API case and
is the recommended low-cost live demo. The full `selfbuilt_benchmark.yaml` runs
all five self-built cases and should be reserved for final acceptance or explicit
regression runs because it costs more time and tokens.

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
and running log are in `docs/codex/plans.md`. Post-milestone optimization work is
tracked in `docs/optimization/优化任务看板.md`.
