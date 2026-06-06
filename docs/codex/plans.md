# CodeAgent Implementation Plan

> Status: approved for implementation on 2026-06-03; execution in progress.
> Source of truth: `docs/codex/plans.md` is the only milestone authority for implementation status, scope, verification, and running notes.

## 1. Document Review

### 1.1 已阅读文件清单

- `docs/codex/prompt.md`
- `docs/题目设计.md`
- `docs/analysis/《基于大语言模型的软件工程智能体》需求规格说明书_v0.1.md`
- `docs/design/README_设计文档包索引.md`
- `docs/design/00_系统设计方案总览.md`
- `docs/design/01_系统架构设计.md`
- `docs/design/02_模块划分与职责设计.md`
- `docs/design/03_数据流与状态模型设计.md`
- `docs/design/04_LangGraph工作流设计.md`
- `docs/design/05_核心类与接口设计.md`
- `docs/design/06_工具调用与HITL设计.md`
- `docs/design/07_错误处理与重试设计.md`
- `docs/design/08_关键技术选型与配置设计.md`
- `docs/design/09_运行产物与可复现设计.md`
- `docs/design/10_Benchmark与扩展预留设计.md`
- `docs/test/benchmark数据集汇报.md`
- `docs/test/benchmark样例整理报告.md`
- `docs/test/BugsInPy_Conda运行指南.md`
- `docs/test/自建benchmark案例设计报告.md`
- `benchmark/README.md`
- `benchmark/benchmark.yaml`
- `benchmark/index.json`
- `benchmark/cases/**/task_config.yaml`
- `benchmark/cases/**/expected_result.json` path/schema only; contents are runner-only hidden success criteria and must not be used as Agent context.
- `benchmark/cases/**/input/*.md`
- `benchmark/selfbuilt/README.md`
- `benchmark/selfbuilt/selfbuilt_benchmark.yaml`
- `benchmark/selfbuilt/cases/**/case.yaml`
- `benchmark/selfbuilt/cases/**/input/*.md`
- Official overview spot-checks: LangChain Python overview and LangGraph Python overview. Implementation milestones must revisit the exact official subpages for interrupts, streaming, persistence/checkpoint, subgraphs, tools, structured output, and human-in-the-loop before coding those parts.

Note: `benchmark/**/evaluation`, `benchmark/selfbuilt/**/oracle_tests`, and `benchmark/cases/**/expected_result.json` are treated as hidden/runner-only evaluation material. Only their paths, roles, and isolation rules may be referenced; hidden test details or expected answers must not be supplied to the evaluated Agent.

### 1.2 核心需求摘要

- Build a local Python CLI software-engineering agent named `codeagent`.
- Cover the continuous workflow `implement -> test -> debug -> repair`, while allowing legal single-stage or contiguous-stage subsets.
- Use LangGraph for the main workflow, four stage subgraphs, conditional routing, streaming, interrupt/HITL, checkpoint, and resume.
- Use LangChain for model calls, tools, structured outputs, and tool-level HITL guardrails.
- Default model access is OpenRouter OpenAI-compatible, temporary cost-control model `google/gemini-3.5-flash`, with credentials resolved only from `OPENROUTER_API_KEY` or another explicitly configured environment variable; local secret files are forbidden inputs and must not be read.
- Enforce patch-first for project source/test modifications. Side-effect operations require approval except benchmark-mode auto-approval with decision trace.
- Persist every run under `codeagent_runs/<run_id>/`, including metadata, normalized task config, transcript, decision trace, artifacts index, stage artifacts, `stage_result.json`, and `final_report.md`.
- Provide benchmark execution, per-case isolation, success-rate aggregation, and reports.

### 1.3 设计基线摘要

- Architecture: CLI layer, config layer, LangGraph workflow layer, stage services, LangChain model/tool layer, context tools, reports/artifacts, benchmark runner, error handling.
- Data model: Pydantic domain objects for config/results/artifacts; TypedDict-like `AgentState` for checkpoint-safe graph state.
- Workflow: main graph routes between `ImplementationSubgraph`, `TestingSubgraph`, `DebuggingSubgraph`, and `RepairSubgraph`.
- HITL: workflow-level approvals for test plans, implementation/test/repair patches, and test or reproduction commands; tool-level HITL as a safety net for side-effect tools.
- Recovery: `run_id` equals LangGraph `thread_id`; SQLite checkpoint in the run directory; resume can continue pending interrupts or report existing artifacts if full recovery fails.
- Reporting: all claims must be backed by files, logs, JSON/JSONL records, or command output.

### 1.4 P0/P1 优先级摘要

- P0: CLI help/wizard/run and stage subcommands, stage validation, path validation, output directory initialization, main graph plus stage subgraphs, file scanning/read/search, patch-first, HITL approval for side effects, shell/test execution, result parsing, stage/final reports, secret handling, benchmark config and at least 5 cases.
- P1: richer non-interactive modes, YAML/JSON reproducibility, SQLite resume robustness, decision trace completeness, artifact index, multi-round repair, context truncation/summarization, benchmark category stats, failure aggregation, developer reports.
- Project override from `prompt.md`: treat SQLite checkpoint/resume, OpenRouter model integration, decision trace, and benchmark reporting as required implementation targets even where the SRS labels related items as P1/P2.

### 1.5 范围内与范围外

Scope in:

- Python 3.11+ local CLI.
- Python project support with pytest as the primary framework.
- Configured benchmark command execution, including current benchmark cases that use `python -m unittest discover`.
- LangGraph/LangChain orchestration, OpenRouter-compatible model access, patch-first file changes, HITL, reports, and benchmarks.

Scope boundaries for this implementation pass:

- IDE plugin, LSP, Web front end, and mobile integration.
- Full multi-language support; Java is adapter-reserved only.
- Wrapping MetaGPT, ChatDev, AutoGPT, or any existing software-engineering-agent system.
- Production sandboxing, Dockerized SWE-bench harness, and broad SWE-bench Lite support in the first implementation pass.

### 1.6 文档间冲突与处理策略

| Conflict | Resolution |
|---|---|
| Course prompt asks for IDE integration, while SRS/design scope is CLI only. | Follow the project-specific SRS/design and current user prompt: CLI is the implementation target. Document the IDE gap in final reports and optionally provide a VSCode task wrapper after P0 if approved. |
| SRS treats resume/checkpoint partly as P1/P2, but `prompt.md` requires SQLite checkpoint/resume. | Treat SQLite checkpoint/resume as required for project completion, with phased delivery: interrupt-point resume first, broader resume hardening later. |
| SRS and prompt emphasize pytest, but current benchmark task configs use `unittest` commands. | Implement pytest as primary parser/adapter and a generic configured-command runner with unittest summary fallback for benchmark compatibility. |
| Design docs say Git is not a runtime dependency, while course process mentions Git use. | Runtime must not require Git. Development process may use Git, but patch/artifact records remain the product mechanism. |
| Benchmark design says 6 public cases, prompt additionally requires 5 self-built cases. | Plan both: public HumanEval/MBPP/QuixBugs first, optional BugsInPy blocker path, then all 5 self-built cases. |
| Secret handling requirement conflicted with initial repository state: `.gitignore` did not list `Software Engineering Project.txt`, `.env`, or `.env.*`; the secret file appeared as untracked. | Resolved in M02 by updating `.gitignore` before business-code work and verifying `git check-ignore` for secret/run-output paths. |

## 2. Architecture Baseline

### 2.1 系统层次

| Layer | Responsibility | Planned package area |
|---|---|---|
| CLI 接入层 | Typer/Rich commands, wizard, approvals, progress rendering, resume entrypoint | `codeagent/cli` |
| 配置与运行初始化 | YAML/JSON parsing, stage/path validation, run_id, output directories | `codeagent/config`, `codeagent/runtime` |
| LangGraph 工作流层 | Main graph, four subgraphs, routing, checkpoint, streaming | `codeagent/workflow` |
| Agent/Prompt 层 | Planner/Coder/TestDesigner/TestWriter/Debugger/Repairer/Verifier prompts and schemas | `codeagent/agents` |
| LangChain 模型/工具层 | OpenRouter model factory, tool registry, permission policy, tool-level HITL | `codeagent/models`, `codeagent/tools` |
| 项目上下文层 | Scan/read/search/truncate/sensitive filtering | `codeagent/context` |
| 产物与报告层 | Transcript, decision trace, artifact index, reports, stage results | `codeagent/reports` |
| Benchmark 层 | Case loading, isolated workspaces, evaluation, aggregation | `codeagent/benchmark` |
| 错误处理层 | Error classification, retry, failure reports, degradation | `codeagent/errors` |

### 2.2 LangGraph 主图与四阶段子图

- Main graph loads config, validates stages, initializes run context, scans project, routes selected stages, records route decisions, and writes final reports.
- Implementation subgraph reads requirements/project context, generates implementation plan, proposes patch, validates/approves/applies patch, runs light syntax checks, and writes reports.
- Testing subgraph analyzes test targets, generates `test_plan.md`, pauses for review, proposes test patch, approves/applies, approves command, runs tests, parses results, and routes on pass/fail.
- Debugging subgraph collects failure logs, optionally reproduces failure, searches code, writes fault localization, root cause, repair plan, and debug report.
- Repair subgraph generates final repair plan, repair patch, risk check, approval, patch application, regression test, and repair report; failures loop back through debug until `max_repair_attempts`.

### 2.3 LangChain 模型与工具层

- `ModelClientFactory` creates an OpenAI-compatible chat model with `base_url=https://openrouter.ai/api/v1`, `api_key_env=OPENROUTER_API_KEY`, low temperature, timeout, and bounded retries.
- `PromptRegistry` stores maintainable system/task prompts for all agent roles, including patch-first, hidden-oracle isolation, no secret access, schema output, and audit-summary rules.
- `ToolRegistry` exposes stage-scoped tools: scan, read, search, log read, propose patch, validate patch, apply patch, run shell, parse test output, write report, record artifact.
- `ToolPermissionPolicy` classifies tools as allow/ask/deny. Side-effect tools are ask by default; benchmark auto-approval is explicit and logged.

### 2.4 patch-first + HITL

- Agent nodes never directly write project source or tests.
- Every source/test change is a unified diff saved under the run directory, validated for path scope and sensitive files, summarized, approved, then applied by an idempotent side-effect node.
- Approval decisions support approve, edit, reject, respond, and cancel.
- `operation_id` makes `apply_patch`, `run_shell`, `write_report`, and artifact recording safe across resume.

### 2.5 SQLite checkpoint/resume

- Each run creates `codeagent_runs/<run_id>/checkpoints.sqlite`.
- LangGraph `thread_id` equals `run_id`.
- `resume --run-id <run_id>` reloads normalized `task_config.yaml`, checkpoint state, and pending interrupt payload when available.
- If checkpoint recovery fails, the CLI must present a read-only summary from `artifacts_index.json`, `stage_result.json`, and `final_report.md`.

### 2.6 日志与报告

- Required root artifacts: `metadata.json`, `task_config.yaml`, `transcript.jsonl`, `decision_trace.jsonl`, `artifacts_index.json`, `checkpoints.sqlite`, and `final_report.md`.
- Each stage writes its own report, logs, patch if applicable, changed-files record if applicable, and `stage_result.json`.
- Final report is generated from stage results, artifact index, and decision trace only; it must not ask the model to invent missing outcomes.

### 2.7 Benchmark runner

- Reads `benchmark/benchmark.yaml` and `benchmark/selfbuilt/selfbuilt_benchmark.yaml`.
- Copies each original case directory to a clean per-run workspace before testing. The original benchmark case is treated as reusable read-only source material; the evaluated Agent may only modify the copied workspace.
- Exposes only configured visible paths inside the copied case, hides `evaluation`, `expected_result.json`, and `oracle_tests` where configured.
- Runs enabled public cases first: HumanEval, MBPP, QuixBugs.
- Treats BugsInPy as optional/blocked unless WSL + conda + prepared workspace are available.
- Runs self-built cases after the public suite and writes `benchmark_result.json` plus `benchmark_report.md`.

## 3. Milestone Plan

### M01 Repository Audit, Planning Gate, and Requirement Trace

- Scope: finalize document review, create `plans.md`, record conflicts, compliance mapping, and the no-business-code gate.
- Key files/modules: `plans.md`; source docs under `docs/`, `benchmark/`.
- Acceptance criteria: plan contains at least 20 milestones, risk register, architecture baseline, test strategy, compliance matrix, benchmark plan, and running notes.
- Verification commands: `Test-Path plans.md`; `rg -n "### M[0-9][0-9]" plans.md`; manual review by user.
- Unit/integration tests to add: none before approval.
- Risks and mitigations: incomplete planning could cause rework; mitigate with user review before coding.
- Status: done; user approved implementation on 2026-06-03.

### M02 Secret Hygiene and Repository Safety Preflight

- Scope: update ignore rules for local secrets, verify no secret content is printed or tracked, define secret loader rules.
- Key files/modules: `.gitignore`, future `codeagent/config/secrets.py`, tests for secret redaction.
- Acceptance criteria: `Software Engineering Project.txt`, `.env`, `.env.*`, and local secret patterns are ignored; metadata records only env var names.
- Verification commands: `git status --short`; `rg -n "OPENROUTER|api_key|Software Engineering Project" .gitignore plans.md docs README.md`.
- Unit/integration tests to add: secret loader returns value without logging; report writer redacts key-like strings.
- Risks and mitigations: accidental key leak; mitigate by redaction tests and never echoing the secret file.
- Status: done.

### M03 Python Package Scaffold and Dependency Baseline

- Scope: create the foundational Python package, `pyproject.toml`, dependency groups, test folders, examples folder.
- Key files/modules: `pyproject.toml`, `codeagent/__init__.py`, `codeagent/__main__.py`, `tests/`.
- Acceptance criteria: package imports, `python -m pytest -q` runs, dependencies include LangGraph/LangChain/OpenAI-compatible support, Typer/Rich, Pydantic, PyYAML.
- Verification commands: `python -m pytest -q`; `python -m codeagent --help`.
- Unit/integration tests to add: import smoke test, package metadata test.
- Risks and mitigations: dependency API drift; verify official docs and pin working versions.
- Status: done.

### M04 CLI Foundation and Help Contract

- Scope: implement `codeagent --help`, `wizard`, `run`, `implement`, `test`, `debug`, `repair`, `benchmark`, and `resume` command skeletons.
- Key files/modules: `codeagent/cli/app.py`, `codeagent/cli/wizard.py`, `codeagent/cli/progress.py`.
- Acceptance criteria: help text lists commands, parameters, examples, and error messages are friendly.
- Verification commands: `codeagent --help`; `python -m codeagent --help`; `codeagent run --help`; `codeagent benchmark --help`.
- Unit/integration tests to add: Typer CliRunner help tests and invalid-argument tests.
- Risks and mitigations: CLI semantics drift from SRS; mitigate by snapshot tests against required commands.
- Status: done.

### M05 Config Models and Stage Validation

- Scope: implement Pydantic models for `ModelConfig`, `InputMaterial`, `TaskConfig`, runtime, permissions, benchmark config, and stage continuity validation.
- Key files/modules: `codeagent/config/schema.py`, `loader.py`, `validators.py`, `defaults.py`.
- Acceptance criteria: valid configs load; invalid stages, missing required paths, unsupported language/framework, and path errors are rejected.
- Verification commands: `python -m pytest tests/unit/config -q`.
- Unit/integration tests to add: legal stage subsets, illegal non-contiguous subsets, YAML/JSON loading, default values, path validation.
- Risks and mitigations: benchmark configs use `implementation/testing` while prompt commands use `implement/test`; support aliases with normalized internal enum.
- Status: done.

### M06 Run Context, Output Directories, and Artifact Index

- Scope: create `run_id`, output tree, metadata, normalized task config, transcript, decision trace, artifact index, and stage directories.
- Key files/modules: `codeagent/runtime/run_context.py`, `codeagent/reports/artifact_store.py`, `codeagent/reports/transcript.py`.
- Acceptance criteria: every run has a unique directory and required files; existing runs are not overwritten.
- Verification commands: `python -m pytest tests/unit/runtime -q`.
- Unit/integration tests to add: run_id uniqueness, full directory tree, artifact index record/find/write, metadata redaction.
- Risks and mitigations: path collisions or accidental overwrite; use timestamp plus hash and fail-safe creation.
- Status: done.

### M07 Project Context Tools and Sensitive Filtering

- Scope: scan Python projects, summarize structure, read files safely, search code, truncate long content, skip sensitive/generated directories.
- Key files/modules: `codeagent/context/scanner.py`, `file_reader.py`, `code_search.py`, `sensitive_filter.py`.
- Acceptance criteria: scanner identifies source/test/config/dependency files and records skipped sensitive patterns without reading secret contents.
- Verification commands: `python -m pytest tests/unit/context -q`.
- Unit/integration tests to add: project scan, read small/large files, search matches, denied secret reads, `.git`/venv/build skip.
- Risks and mitigations: hidden benchmark leakage; enforce visible-path allowlists in benchmark mode.
- Status: done.

### M08 PatchService and Patch Risk Checks

- Scope: create, parse, validate, summarize, and apply unified diffs without relying on Git; implement risk checks.
- Key files/modules: `codeagent/tools/patch_tools.py`, `codeagent/services/patch_service.py`.
- Acceptance criteria: valid patches apply exactly once; out-of-root and sensitive-file patches are blocked; test deletion/skip/hardcoding patterns are flagged.
- Verification commands: `python -m pytest tests/unit/tools/test_patch_service.py -q`.
- Unit/integration tests to add: add/modify/delete diff parsing, path traversal rejection, idempotent apply, risk report for suspicious changes.
- Risks and mitigations: patch parser edge cases; use focused fixtures and keep initial patch format conservative.
- Status: done.

### M09 ShellRunner and Test Command Execution

- Scope: execute approved commands with timeout, cwd, stdout/stderr capture, exit code, duration, and operation records.
- Key files/modules: `codeagent/tools/shell_tools.py`, `codeagent/runtime/commands.py`.
- Acceptance criteria: approved pytest/unittest commands run; blocked commands are denied; logs are saved to run directory.
- Verification commands: `python -m pytest tests/unit/tools/test_shell_runner.py -q`.
- Unit/integration tests to add: success, failure, timeout, stderr capture, command policy, benchmark auto-approval record.
- Risks and mitigations: dangerous command execution; start with allowlist and explicit approval payload.
- Status: done.

### M10 Pytest and Generic Test Result Parsing

- Scope: parse pytest output and current benchmark unittest output into a common `TestResult`.
- Key files/modules: `codeagent/tools/pytest_tools.py`, `codeagent/adapters/pytest_adapter.py`, `codeagent/adapters/unittest_adapter.py`.
- Acceptance criteria: parser extracts passed/failed/errors/skipped, failing tests, error summary, and low-confidence fallback when format is unknown.
- Verification commands: `python -m pytest tests/unit/tools/test_test_result_parser.py -q`.
- Unit/integration tests to add: pytest pass/fail/error, unittest pass/fail, timeout log, malformed output fallback.
- Risks and mitigations: output format variance; persist raw logs and include parser confidence.
- Status: done.

### M11 ToolRegistry, Permission Policy, and Tool-Level HITL

- Scope: register read/search/patch/shell/report/artifact tools, classify permissions, and add tool-level HITL interception.
- Key files/modules: `codeagent/tools/registry.py`, `permissions.py`, `hitl.py`.
- Acceptance criteria: stage tools are scoped; readonly tools run automatically; side-effect tools require approval unless benchmark mode auto-approval is configured.
- Verification commands: `python -m pytest tests/unit/tools/test_permissions.py -q`.
- Unit/integration tests to add: allow/ask/deny classification, edited tool call, reject/respond behavior, decision trace records.
- Risks and mitigations: model bypassing workflow approvals; keep side-effect tools guarded even if called directly.
- Status: done.

### M12 Model Factory and Prompt Registry

- Scope: implement OpenRouter-compatible model factory, safe API key resolution, structured-output retry wrappers, and centralized prompts.
- Key files/modules: `codeagent/models/factory.py`, `codeagent/agents/prompts.py`, `structured_outputs.py`.
- Acceptance criteria: model config validates; missing key produces clear error; prompts include patch-first, hidden-oracle, no-secret, tool-use, schema, and audit rules.
- Verification commands: `python -m pytest tests/unit/models tests/unit/agents -q`.
- Unit/integration tests to add: config mapping, missing key error, prompt snapshot tests, structured schema validation retry.
- Risks and mitigations: LangChain/OpenRouter API drift; re-check official docs immediately before implementation and record installed versions.
- Status: done.

### M13 AgentState, StageResult, ErrorRecord, and Report Schemas

- Scope: define checkpoint-safe state and Pydantic result objects for stages, tools, approvals, artifacts, tests, faults, and errors.
- Key files/modules: `codeagent/workflow/state.py`, `codeagent/reports/schemas.py`, `codeagent/errors/exceptions.py`.
- Acceptance criteria: schemas serialize to JSON, avoid large raw content in state, and preserve artifact paths/summary references.
- Verification commands: `python -m pytest tests/unit/workflow/test_state_schema.py -q`.
- Unit/integration tests to add: JSON roundtrip, Pydantic validation failure, checkpoint-safe path/string conversion.
- Risks and mitigations: non-serializable objects breaking checkpoint; keep state primitive and file-backed.
- Status: done.

### M14 Report Writers and Audit Logs

- Scope: write stage reports, `stage_result.json`, final report, transcript, decision trace, and artifact index.
- Key files/modules: `codeagent/reports/writer.py`, `templates/`, `decision_trace.py`.
- Acceptance criteria: all reports reference registered artifacts only; failed/cancelled stages include reason and next suggestion.
- Verification commands: `python -m pytest tests/unit/reports -q`.
- Unit/integration tests to add: template rendering, artifact reference validation, decision trace append, final report from mocked stage results.
- Risks and mitigations: reports overstating results; generate only from verified records, not fresh model prose.
- Status: done.

### M15 LangGraph Main Graph and Routing

- Scope: build main graph skeleton, stage selection routing, conditional edges, route decision logging, streaming event adapter.
- Key files/modules: `codeagent/workflow/factory.py`, `main_graph.py`, `routing.py`.
- Acceptance criteria: mocked stages route correctly for success/failure/cancelled; test failure enters debug when selected; repair failure loops until max attempts.
- Verification commands: `python -m pytest tests/unit/workflow/test_routing.py -q`.
- Unit/integration tests to add: route-after-implementation/testing/debugging/repair, selected-stage subsets, max repair attempts.
- Risks and mitigations: graph routing mismatch with SRS; use deterministic mocked subgraphs first.
- Status: done.

### M16 SQLite Checkpoint, Interrupt, and Resume

- Scope: add SQLite checkpointer, `thread_id=run_id`, interrupt payload persistence, and `resume --run-id`.
- Key files/modules: `codeagent/workflow/checkpoint.py`, `codeagent/cli/resume.py`.
- Acceptance criteria: a pending approval can be resumed; completed run displays final report; corrupted/missing checkpoint falls back to artifact summary.
- Verification commands: `python -m pytest tests/integration/test_resume.py -q`.
- Unit/integration tests to add: checkpoint file creation, interrupt/resume path, missing run_id, missing checkpoint fallback.
- Risks and mitigations: LangGraph checkpoint API changes; verify official persistence/interrupt docs and pin versions.
- Status: done.

### M17 ImplementationSubgraph

- Scope: requirement extraction, project impact analysis, implementation plan, code patch generation, patch approval/application, syntax check, implementation report.
- Key files/modules: `codeagent/workflow/subgraphs/implementation.py`, `codeagent/stages/implementation_service.py`.
- Acceptance criteria: on a small fixture, generates plan, patch, changed files, syntax log, implementation report, and stage result.
- Verification commands: `python -m pytest tests/integration/test_implementation_stage.py -q`.
- Unit/integration tests to add: plan schema, patch loop on validation failure, syntax-check failure path, cancelled approval path.
- Risks and mitigations: LLM patch quality; use schema validation, targeted context, and small fixtures before benchmark.
- Status: done.

### M18 TestingSubgraph

- Scope: test target analysis, test plan generation/review, test patch generation/review, command approval, execution, result parsing, report.
- Key files/modules: `codeagent/workflow/subgraphs/testing.py`, `codeagent/stages/testing_service.py`.
- Acceptance criteria: fixture project can approve test plan, apply test patch, run configured command, parse result, and route correctly.
- Verification commands: `python -m pytest tests/integration/test_testing_stage.py -q`.
- Unit/integration tests to add: test-plan review decisions, test patch restrictions, command edit/reject, pass/fail result routing.
- Risks and mitigations: hidden tests leakage in benchmark mode; expose only visible paths and deny reads of `evaluation` or `oracle_tests`.
- Status: done.

### M19 DebuggingSubgraph

- Scope: collect logs, reproduce when command is available, summarize failures, search source, fault localization, root cause, repair plan, debug report.
- Key files/modules: `codeagent/workflow/subgraphs/debugging.py`, `codeagent/stages/debugging_service.py`.
- Acceptance criteria: QuixBugs-like fixture produces failure summary, ranked suspects with evidence, root cause, repair plan, and stage result.
- Verification commands: `python -m pytest tests/integration/test_debugging_stage.py -q`.
- Unit/integration tests to add: reproduction approved/rejected, static-log fallback, fault-localization schema, low-confidence reporting.
- Risks and mitigations: speculative root cause; require tool evidence in structured output.
- Status: done.

### M20 RepairSubgraph and Multi-Round Repair Loop

- Scope: final repair plan, repair patch, risk check, approval/application, regression command, result parsing, loop back to debugging on failure.
- Key files/modules: `codeagent/workflow/subgraphs/repair.py`, `codeagent/stages/repair_service.py`, `codeagent/tools/risk_checker.py`.
- Acceptance criteria: buggy fixture is repaired and verified; repeated failure stops at `max_repair_attempts` with clear failure report.
- Verification commands: `python -m pytest tests/integration/test_repair_stage.py -q`.
- Unit/integration tests to add: risk checker, repair success, repair failure loop, max-attempt final failure.
- Risks and mitigations: overfitting patch; deny test deletion/skip/hardcoding and record risk decisions.
- Status: done.

### M21 Wizard, Streaming Progress, and Approval UI

- Scope: implement semi-interactive wizard, Rich progress rendering, approval prompts, streaming event display, cancellation handling.
- Key files/modules: `codeagent/cli/wizard.py`, `approval_console.py`, `progress.py`.
- Acceptance criteria: user can configure a task, review summary, approve/edit/reject/cancel approvals, and see stage/tool/test progress.
- Verification commands: `python -m pytest tests/integration/test_cli_wizard.py -q`.
- Unit/integration tests to add: scripted wizard input, approval decisions, cancellation final report.
- Risks and mitigations: flaky interactive tests; test controller logic separately from terminal rendering.
- Status: done.

### M22 Non-Interactive Run and Stage Subcommands

- Scope: wire `run --config`, `implement`, `test`, `debug`, and `repair` to normalized `TaskConfig` and graph execution.
- Key files/modules: `codeagent/cli/app.py`, `codeagent/config/cli_mapping.py`.
- Acceptance criteria: config mode and each stage command create run dirs, run legal stages, and reject illegal inputs.
- Verification commands: `codeagent run --config examples/task.yaml`; `python -m pytest tests/integration/test_cli_run.py -q`.
- Unit/integration tests to add: run config, stage subcommand mapping, invalid path, invalid stage order.
- Risks and mitigations: command/config divergence; use one loader normalization path for all commands.
- Status: done.

### M23 BenchmarkRunner, CaseLoader, Evaluator, and Aggregator

- Scope: load benchmark configs, copy each original case into a clean isolated run workspace, replace `{{CASE_DIR}}` command placeholders with the copied case directory, enforce visible/hidden paths, run workflow in benchmark mode, evaluate criteria, aggregate metrics.
- Key files/modules: `codeagent/benchmark/runner.py`, `case_loader.py`, `evaluator.py`, `metrics.py`, `report.py`.
- Acceptance criteria: enabled cases run only in clean copied case directories, original benchmark cases remain unchanged and reusable, `{{CASE_DIR}}` in case commands resolves to the run copy, auto-approvals are logged, result JSON/Markdown reports are generated.
- Verification commands: `codeagent benchmark --config benchmark/benchmark.yaml`; `python -m pytest tests/integration/test_benchmark_runner.py -q`.
- Unit/integration tests to add: case loading, hidden path enforcement, auto-approval trace, artifact-required evaluator, failure aggregation.
- Risks and mitigations: original benchmark pollution; always copy the entire case to a clean temp/run dir, run Agent/test commands against that copy, and never edit source benchmark directories.
- Status: done.

### M24 Public Benchmark Pass: HumanEval, MBPP, QuixBugs

- Scope: run 2 HumanEval cases, 2 MBPP cases, and 2 QuixBugs cases from `benchmark/benchmark.yaml`; drive implementation and repair with real OpenRouter LLM calls; iterate on CodeAgent architecture, prompts, schemas, validation, and reports until failures are either fixed or classified with evidence.
- Key files/modules: `codeagent/agents`, `codeagent/models`, `codeagent/cli/executor.py`, stage services, benchmark configs, and any runtime/report modules exposed by real benchmark failures.
- Acceptance criteria: enabled public cases report success or evidence-backed failure categories with logs; target is all enabled public cases passing; every failure must be investigated from run artifacts, produce a code or prompt improvement when actionable, and add a regression test when the failure is reproducible.
- Verification commands: `codeagent benchmark --config benchmark/benchmark.yaml`; targeted real LLM smoke for generated implementation/repair plans; `python -m pytest -q`.
- Unit/integration tests to add: LLM request/response schema tests, prompt hidden-context tests, CLI handler tests for generated implementation/repair requests, regression tests for each benchmark failure class, and at least one self-designed public-style micro-benchmark that exercises LLM implementation plus hidden oracle isolation.
- Risks and mitigations: model variability and hidden-answer leakage; use deterministic temperatures, schema validation, retries with validation feedback, hidden-path prompt tests, repeated isolated runs, and failure taxonomy rather than silent skips.
- Status: done.

### M24A LLM Orchestration Hardening and Benchmark Regression Pack

- Scope: harden the LLM-driven implementation/repair loop after the first public benchmark pass by adding richer context selection, redaction, retry diagnostics, structured failure classes, and a self-designed benchmark regression pack that covers implementation, repair, hidden oracle, nested hidden paths, and malformed model output.
- Key files/modules: `codeagent/agents/plan_generation.py`, `codeagent/agents/prompts.py`, `codeagent/models/structured_outputs.py`, `codeagent/benchmark/**`, `benchmark/selfbuilt/**` or new public-style regression cases.
- Acceptance criteria: LLM calls are auditable without secret leakage; malformed/partial model responses produce actionable errors; prompt context excludes hidden and sensitive files by test; generated plans cannot target hidden/sensitive paths; custom benchmark pack runs in clean copies, records source-case snapshot evidence, and reports stable outcomes.
- Verification commands: `python -m pytest tests/unit/agents tests/unit/models tests/integration/test_benchmark_runner.py -q`; run custom benchmark config; rerun `benchmark/benchmark.yaml`.
- Status: done.

### M25 BugsInPy Optional Path and Environment Detection

- Scope: detect WSL/conda/official BugsInPy readiness, run or clearly block `bugsinpy_black_001`, and document blockers.
- Key files/modules: `scripts/*bugsinpy*.ps1`, `codeagent/benchmark/environment.py`, BugsInPy case config.
- Acceptance criteria: if environment exists, run official prepare/test wrapper; if missing, benchmark report records blocker without silent skip.
- Verification commands: copy `benchmark/cases/bugsinpy_black_001` to a clean `<copied_case_dir>`, then run `powershell -ExecutionPolicy Bypass -File scripts/run_bugsinpy_wsl_conda.ps1 -CaseDir <copied_case_dir> -AllowTestFailure`.
- Unit/integration tests to add: environment detection unit tests and disabled-case reporting.
- Risks and mitigations: Windows/WSL filesystem and Python 3.8.3 complexity; keep optional, explicit, and well documented.
- Status: done.

### M26 Self-Built Benchmark Pass and Final Developer Docs

- Scope: run all 5 self-built cases, iterate failures, finalize README, developer reports, benchmark report, and demonstration notes.
- Key files/modules: `benchmark/selfbuilt/**`, `README.md`, `docs/dev_reports/`, benchmark reports.
- Acceptance criteria: each self-built case has isolated run output, success/failure reason, logs, patches, and aggregate report; README documents installation, API key, CLI, resume, and benchmark.
- Verification commands: `codeagent benchmark --config benchmark/selfbuilt/selfbuilt_benchmark.yaml`; `python -m pytest -q`; `codeagent --help`.
- Unit/integration tests to add: regression tests based on self-built failures and README command smoke tests.
- Risks and mitigations: large scope and external dependencies in Flask case; run easier CLI cases first, install generated dependencies only in isolated benchmark env.
- Status: done.

### M28 Agent Self-Test, Chinese Wizard, and Streaming UX Hardening

- Scope: replace direct configured-command testing with LLM-generated visible tests through `TestingService`; reject zero-test verification; upgrade `wizard` to a Chinese form that runs the Agent immediately; localize CLI progress output; emit stage-internal streaming progress; enrich benchmark reports with Agent self-test evidence.
- Key files/modules: `codeagent/agents/plan_generation.py`, `codeagent/cli/executor.py`, `codeagent/stages/testing_service.py`, `codeagent/cli/wizard.py`, `codeagent/cli/progress.py`, `codeagent/workflow/events.py`, `codeagent/benchmark/*`.
- Acceptance criteria: benchmark cases include nonzero Agent self-tests before hidden oracle evaluation; `0 passed`/0 collected tests fail; `codeagent wizard` uses Chinese selection/multi-select form and directly starts the run; progress output is Chinese and includes stage-internal LLM/tool/test status; README/demo/dev report document the new behavior.
- Verification commands: `python -m pytest tests/unit -q`; `python -m pytest tests/integration/test_testing_stage.py tests/integration/test_benchmark_runner.py tests/integration/test_cli_wizard.py tests/integration/test_cli_run.py -q`; `python -m pytest -q`; cost-controlled real LLM self-built validation with 1-2 selected cases. Full `benchmark/selfbuilt/selfbuilt_benchmark.yaml` should be reserved for explicit final acceptance because it is time/token expensive.
- Unit/integration tests to add: testing request schema generation, hidden oracle exclusion in testing prompt, zero-test failure, multi-mode stream event normalization, wizard direct-run scripted backend, benchmark self-test fields.
- Risks and mitigations: live LLM variability in generated tests; use strict schema validation, hidden-path checks, nonzero test enforcement, benchmark oracle separation, and fake model injection for deterministic CI tests.
- Status: done.

## 4. Risk Register

| ID | Risk | Impact | Likelihood | Mitigation | Owner/status |
|---|---|---:|---:|---|---|
| R01 | LangGraph API changes for checkpoint, interrupt, subgraphs, streaming | High | Medium | Re-read official docs before M15/M16, pin versions, isolate adapter code | pending |
| R02 | LangChain/OpenRouter OpenAI-compatible initialization changes | High | Medium | Re-read official model/tool docs, test factory with mocked env, record versions | pending |
| R03 | API key leakage through logs, metadata, git, or transcript | Critical | Medium | Ignore secret files, redact key-like strings, never print secret file, test reports | pending |
| R04 | Patch application corrupts user or benchmark workspace | High | Medium | Apply only approved unified diffs in copied workspace, path checks, operation_id | pending |
| R05 | HITL resume repeats side effects | High | Medium | Pure approval nodes, side-effect nodes after approval, idempotency checks | pending |
| R06 | pytest/unittest output parsing misses failure details | Medium | Medium | Raw logs always saved, parser confidence, fallback regex, fixture corpus | pending |
| R07 | Benchmark hidden tests or expected answers leak into Agent context | Critical | Medium | Visible/hidden path allowlists, read_file enforcement, benchmark isolation tests | pending |
| R08 | Long files/logs exceed context budget | Medium | High | Truncation, summaries, search-before-read, store raw logs on disk only | pending |
| R09 | BugsInPy environment is unavailable or brittle on Windows | Medium | High | Detect WSL/conda readiness, record blocker, keep optional until ready | pending |
| R10 | LLM generates broad or irrelevant patches | High | Medium | Prompt minimum diff, patch scope checker, approval summary, risk report | pending |
| R11 | Benchmark auto-approval executes unsafe shell | High | Low | Auto-approve only configured pytest/unittest/py_compile-like commands; deny others | pending |
| R12 | Reports claim success without executed verification | Critical | Low | Reports derive from command results/stage_result only; tests enforce no synthetic success | pending |
| R13 | Current benchmark uses unittest despite pytest-first design | Medium | High | Generic configured-command runner plus unittest parser fallback | pending |
| R14 | IDE requirement from course rubric remains unmet | Medium | Medium | Document CLI scope; optionally add VSCode task wrapper after P0 if approved | pending |
| R15 | Benchmark run pollutes original case and prevents reuse | High | Medium | Treat original case directories as read-only templates; copy each full case to a clean per-run workspace before Agent execution and final evaluation | pending |

## 5. Test Strategy

### 5.1 单元测试策略

- Config: valid/invalid stages, aliases, YAML/JSON, missing required fields, path validation, default model/runtime values.
- Context: scan/read/search, sensitive-file skip, visible-path enforcement, truncation.
- Tools: patch parsing/apply/idempotency, shell runner policy/timeout/logging, test-result parser, report writer.
- Workflow state: schema serialization, stage result, error record, human decision, artifact records.
- Prompt/schemas: prompt snapshot tests and structured-output validation.

### 5.2 集成测试策略

- Run context initialization creates full output tree and audit files.
- Mocked LangGraph graph routes through implement/test/debug/repair with deterministic fake stage outputs.
- Approval interrupt/resume path works without repeating side effects.
- Stage integration tests use tiny local Python fixtures before live model tests.
- Reports and artifact index are verified after each stage.

### 5.3 CLI 测试策略

- `codeagent --help` and subcommand help snapshots.
- `run --config` accepts valid example and rejects invalid paths/stage order.
- Stage subcommands normalize into the same `TaskConfig` loader.
- Wizard controller tested with scripted inputs; Rich rendering kept thin.
- Resume command tested for completed, pending-interrupt, missing-run, and missing-checkpoint states.

### 5.4 LangGraph 节点测试策略

- Unit-test route functions outside LangGraph.
- Use fake LLM/tool nodes for graph structure and conditional edges.
- Add interrupt tests for test plan, patch approval, and command approval.
- Add side-effect idempotency tests by replaying a resumed state.
- Keep live OpenRouter tests optional and manually gated to avoid cost and flakiness.

### 5.5 Benchmark 测试顺序

1. Internal smoke fixtures with no LLM dependency.
2. HumanEval and MBPP function implementation cases.
3. QuixBugs repair cases.
4. BugsInPy environment detection and optional run.
5. Five self-built cases from easiest to hardest.

Recommended core commands to maintain:

```bash
python -m pytest -q
python -m pytest tests/unit -q
python -m pytest tests/integration -q
codeagent --help
codeagent run --config examples/task.yaml
codeagent benchmark --config benchmark/benchmark.yaml
codeagent benchmark --config benchmark/selfbuilt/selfbuilt_benchmark.yaml
```

Optional quality commands after tooling exists:

```bash
ruff check .
python -m mypy codeagent tests
```

## 6. Compliance Matrix

### 6.1 SRS FR/NFR 到模块和测试的映射

| Requirement | Planned modules | Evidence/tests |
|---|---|---|
| FR-01 to FR-11 CLI/config | `cli`, `config`, `runtime` | CLI help tests, wizard tests, stage validation tests, run-dir tests |
| FR-12 to FR-19 workflow/HITL/checkpoint | `workflow`, `cli/approval`, `reports/decision_trace` | routing tests, interrupt/resume tests, decision trace assertions |
| FR-20 to FR-29 context/tools | `context`, `tools`, `reports` | scan/read/search tests, permission tests, truncation tests |
| FR-30 to FR-37 implementation | `workflow/subgraphs/implementation`, `stages/implementation_service` | implementation fixture integration, patch validation, report checks |
| FR-38 to FR-48 testing | `workflow/subgraphs/testing`, `tools/shell`, `tools/pytest_tools` | test-plan approval, test patch, command approval, parser tests |
| FR-49 to FR-56 debugging | `workflow/subgraphs/debugging`, `stages/debugging_service` | failure-log fixture, fault localization schema, debug report checks |
| FR-57 to FR-66 repair | `workflow/subgraphs/repair`, `tools/risk_checker` | repair fixture, risk check tests, multi-round loop tests |
| FR-67 to FR-72 logs/reproducibility | `reports`, `runtime`, `artifact_store` | artifact index tests, stage/final report tests, transcript/decision trace tests |
| FR-73 to FR-77 benchmark | `benchmark` | case loader/evaluator tests, public benchmark run, self-built benchmark run |
| FR-79 to FR-87 exceptions | `errors`, all CLI/stage modules | config/path/model/tool/pytest/patch/cancel failure tests |
| NFR-01 to NFR-05 usability | `cli`, `reports` | CLI output/help snapshots, error-message tests |
| NFR-06 to NFR-09 reliability | `workflow`, `runtime`, `reports` | checkpoint, partial artifact, failure-route tests |
| NFR-10 to NFR-14 security | `tools`, `context`, `reports`, `.gitignore` | approval, path restriction, sensitive skip, redaction tests |
| NFR-15 to NFR-18 performance/control | `tools`, `agents`, `config` | timeout, truncation, max tool-call/repair-attempt tests |
| NFR-19 to NFR-25 maintainability/compatibility | package layout, adapters | import boundaries, adapter unit tests, Python+pytest compatibility tests |

### 6.2 课程要求到实现产物的映射

| Course requirement | Planned artifact |
|---|---|
| 连续软件工程阶段 | LangGraph main graph and four subgraphs for implement/test/debug/repair |
| CLI 启动 | `codeagent` command and `python -m codeagent` |
| 非交互/半交互 | `run --config`, stage commands, `wizard` |
| 基准测试至少 5 个案例 | 6 public enabled cases plus 5 self-built cases |
| 成功率报告 | `benchmark_result.json` and `benchmark_report.md` |
| 技术报告材料 | README, `docs/dev_reports/Mxx_*.md`, final benchmark reports |
| 工具调用与错误处理说明 | Tool registry, permission policy, error classifier, retry records |
| 不基于现有软件工程智能体 | Direct LangGraph/LangChain orchestration, no MetaGPT/ChatDev/AutoGPT wrapper |
| 代码质量扫描 | Optional `ruff`/`mypy` milestones after scaffold |
| IDE 集成 | Explicitly out of CLI SRS scope; document as scope deviation or optional wrapper after P0 approval |

## 7. Benchmark Plan

### 7.1 Public benchmark

- Config: `benchmark/benchmark.yaml`.
- Reuse rule: every run copies the selected original case to a clean run workspace first. The Agent and test commands operate on the copy only; the original `benchmark/cases/<case_id>/` directory must remain reusable for later runs.
- Enabled cases:
  - `humaneval_000_has_close_elements`
  - `humaneval_001_separate_paren_groups`
  - `mbpp_002_similar_elements`
  - `mbpp_003_is_not_prime`
  - `quixbugs_gcd`
  - `quixbugs_find_in_sorted`
- Optional disabled case:
  - `bugsinpy_black_001`, requires WSL, conda env `codeagent-bugsinpy-py383`, official BugsInPy checkout/compile/test wrappers.
- Execution order:
  1. HumanEval/MBPP implementation-testing cases.
  2. QuixBugs test-debug-repair cases.
  3. BugsInPy detection or optional execution.
- Success rules:
  - Function cases: configured evaluation command exits 0, required entry point remains, evaluation files are not modified.
  - QuixBugs: workspace tests pass after repair, tests are not modified, repair patch is limited to buggy program unless justified.
  - BugsInPy: official wrapper passes or report records blocker.

### 7.2 Self-built benchmark

- Config: `benchmark/selfbuilt/selfbuilt_benchmark.yaml`.
- Reuse rule: every run copies the selected original self-built case to a clean run workspace first. The original empty `workspace/` and hidden `oracle_tests/` remain untouched so the case can be reused.
- Cases:
  - `01_todo_manager`: stdin/stdout-drivable TUI + JSON persistence.
  - `02_personal_ledger`: stdin/stdout-drivable TUI + JSON ledger persistence.
  - `03_student_gradebook`: stdin/stdout-drivable TUI + JSON gradebook persistence.
  - `04_library_lending`: standard-library Web UI + SQLite lending workflow.
  - `05_meeting_room_booking`: Flask Web UI + JSON API + SQLite; requires generated Flask dependency.
- Execution order: run from easiest to hardest after public suite passes.
- Hidden oracle policy: expose only `input/` and copied `workspace/`; runner alone uses `oracle_tests/`.
- Success rules: created package/API matches input materials and all hidden oracle tests pass in isolated copy.

### 7.3 Benchmark artifacts

Each benchmark run should write:

- Per-case run directory with task config, metadata, transcript, decision trace, patches, logs, stage results, and final report.
- Aggregate `benchmark_result.json`.
- Aggregate `benchmark_report.md` with total success rate, category success rate, case details, failures, blockers, and limitations.

## 8. Running Notes

Use this section as an append-only engineering log. Every completed small module or milestone must add:

- Completed content.
- Commands run.
- Result summary.
- Failures and fixes.
- Whether documents need adjustment.
- Next step.

### 2026-06-02 Planning Session

- Completed content: read `docs/codex/prompt.md`, course prompt, SRS, design package, benchmark docs/configs, public benchmark case configs/inputs, self-built case configs/inputs, and official LangChain/LangGraph overview pages.
- Commands run: `rg --files docs`; `rg --files benchmark`; `Get-Content -Raw -Encoding UTF8 ...`; `rg -n ... docs/analysis`; `rg -n ... docs/design`; `git status --short`; `Test-Path plans.md`.
- Result summary: identified required CLI four-stage LangGraph/LangChain agent, patch-first/HITL/checkpoint/reporting/benchmark constraints, 24+ implementation milestones, and key doc conflicts.
- Failures and fixes: initial PowerShell read of `prompt.md` produced mojibake; re-read with UTF-8 console/output encoding. Initial `rg` glob syntax failed on Windows; switched to `-g` filters.
- Document adjustment needed: after plan approval, first safety action should update `.gitignore` for local secret files before business code begins.
- Next step: wait for user review and approval of `plans.md`.

### 2026-06-03 Implementation Kickoff

- Completed content: read `docs/codex/implement.md` and `superpowers:subagent-driven-development`; recognized user approval to execute milestones continuously from M02 onward.
- Commands run: `Get-Content -Raw -Encoding UTF8 docs/codex/implement.md`; `Get-Content -Raw -Encoding UTF8 .../subagent-driven-development/SKILL.md`; `git status --short`; benchmark documentation search via `rg`.
- Result summary: `docs/codex/plans.md` is now the sole milestone source of truth. Added a benchmark reuse rule requiring each case to be copied into a clean per-run workspace before Agent/test execution.
- Failures and fixes: first broad documentation patch did not apply because one Mermaid diagram context differed; split into targeted patches.
- Document adjustment needed: related benchmark docs were updated to match the clean-copy reuse rule; spec review requested follow-up fixes for self-built manual-check and BugsInPy examples.
- Next step: fix spec-review findings, then complete M02 secret hygiene and repository safety preflight.

### 2026-06-03 M02 Safety Preflight

- Completed content: updated `.gitignore` with local secret and run-output patterns; synchronized benchmark clean-copy rule across plans, prompt, implement guide, SRS, design docs, benchmark README files, benchmark reports, and BugsInPy guide.
- Commands run: `git status --short`; `git check-ignore "Software Engineering Project.txt" ".env" ".env.local" "codeagent_runs/example"`; `rg -n "OPENROUTER|api_key|Software Engineering Project|\\.env" .gitignore docs benchmark --glob '!docs/_backups/**' --glob '!**/oracle_tests/**' --glob '!**/evaluation/**' --glob '!**/expected_result.json' --glob '!benchmark/**/workspace/**'`; strict secret-value regex search over docs/benchmark/.gitignore with the same hidden/workspace exclusions.
- Result summary: `git check-ignore` confirms secret and run-output paths are ignored. `git status --short` no longer lists `Software Engineering Project.txt`. Strict secret-value regex search returned no matches.
- Failures and fixes: an initial broad search included `oracle_tests` and `bugsinpy` workspace paths; repeated with explicit hidden/workspace exclusions. Spec reviewer found self-built README and BugsInPy examples still pointed at original case paths; fixed examples to use `<run_case_dir>` / `<copied_case_dir>`. Quality reviewer then found remaining BugsInPy original-path examples and ambiguous expected-result reading boundary; fixed M25 command, benchmark README, benchmark sample report, hidden-material wording, and BugsInPy case metadata to use `{{CASE_DIR}}`.
- Document adjustment needed: none known after quality-review fixes; re-running quality review.
- Quality review: subagent review returned PASS on 2026-06-03. Evidence: ignore patterns verified; `expected_result.json` documented as runner-only hidden material; clean-copy benchmark rule aligned across docs/config; BugsInPy commands use `{{CASE_DIR}}`; no visible command still runs directly against original case directories.
- Non-blocking follow-up: `docs/_backups/` contains local historical copies, is ignored by Git, and is intentionally excluded from current-doc validation; avoid treating backup content as live documentation.
- Next step: continue M03 Python package scaffold and dependency baseline.

### 2026-06-03 M03 Python Package Scaffold

- Completed content: created `pyproject.toml`, `README.md`, `examples/README.md`, `codeagent/__init__.py`, `codeagent/__main__.py`, `codeagent/cli/app.py`, and `tests/test_package_smoke.py`.
- Dependency decision: aligned package constraints with current LangChain/LangGraph v1 documentation and verified dry-run resolution for `langchain>=1.0,<2.0`, `langchain-openai>=1.0,<2.0`, `langgraph>=1.0,<2.0`, `langgraph-checkpoint-sqlite>=3.1,<4.0`, and `openai>=2.26,<3.0`.
- Commands run: `python -m pytest -q`; `python -m codeagent --help`; `python -m pip install -e . --dry-run`; `python -m pip check`.
- Result summary: `pytest` passed with 4 tests; `python -m codeagent --help` and `python -m codeagent --version` exited 0; editable install dry-run resolved successfully and would install CodeAgent plus v1 LangChain/LangGraph dependencies. `pip check` reports pre-existing global interpreter conflicts among unrelated installed packages and older LangChain integrations; no package install was performed during M03.
- Reviews: M03 spec review returned PASS. M03 quality review requested a stale `plans.md` status/log update; the follow-up verification also found and fixed a Typer `--version` callback issue by enabling `invoke_without_command`.
- Next step: continue M04 CLI foundation and help contract.

### 2026-06-03 M04 CLI Foundation

- Completed content: registered `wizard`, `run`, `implement`, `test`, `debug`, `repair`, `benchmark`, and `resume` command skeletons; added `ProgressReporter`; added root and command examples; added Typer CliRunner help and invalid-argument tests.
- Commands run: `python -m pytest -q`; `python -m codeagent --help`; `python -m codeagent run --help`; `python -m codeagent benchmark --help`; `codeagent --help`; `codeagent run --help`; `codeagent benchmark --help`; `python -m pip install -e .`.
- Result summary: all help commands exited 0; `pytest` passed with 8 tests; `run` without `--config` or `--project` returns a friendly validation error; benchmark skeleton reports clean per-run copy behavior and does not run original case directories.
- Installation note: editable install succeeded and made the `codeagent` console script available. The shared global interpreter emitted dependency conflict warnings for other pre-installed packages after adopting LangChain/LangGraph v1 dependencies; use a project virtual environment for future real runs and benchmarks.
- Reviews: M04 spec review initially failed on missing examples and over-promising help text; fixed with `Examples` and `Planned skeleton` wording. Spec re-review PASS. Quality review APPROVED with no P0/P1/P2 issues.
- Next step: continue M05 config models and stage validation.

### 2026-06-03 M05 Config Models and Stage Validation

- Completed content: added `codeagent/config/defaults.py`, `validators.py`, `schema.py`, `loader.py`, package exports, and config unit tests.
- Behavior implemented: canonical stages `implement/test/debug/repair`; aliases including `implementation/testing/debugging`; contiguous ordered stage validation; Pydantic model defaults for OpenRouter-compatible model config, runtime, permissions, task config, command config, visibility, benchmark config; YAML/JSON loading; relative path resolution; project path and required input path validation; benchmark config case path resolution.
- Commands run: `python -m pytest tests/unit/config -q`; `python -m pytest -q`; loader smoke checks against visible benchmark config files.
- Result summary: config tests passed with 32 tests; full suite passed with 40 tests. Visible benchmark configs load without reading hidden `oracle_tests/`, `evaluation/`, or `expected_result.json` contents.
- Reviews: M05 spec review returned PASS. Non-blocking JSON benchmark test suggestion was implemented. M05 quality review requested fixes for evaluator-only metadata retention and malformed stage errors; fixed by changing agent-facing `TaskConfig` to ignore unknown keys, adding hidden-metadata regression tests, and returning stable `ValueError` for `stages: null` / scalar stage inputs.
- Quality re-review: APPROVED; no P0/P1/P2 issues remain after hidden-metadata and malformed-stage fixes.
- Next step: continue M06 run context and artifact index.

### 2026-06-03 M06 Run Context and Artifact Index

- Completed content: added run initialization, `RunContext`, SQLite checkpoint placeholder, metadata writer, normalized task config writer, artifact store, and JSONL recorder.
- Behavior implemented: unique run IDs with timestamp/stage/hash, fail-safe directory creation without overwriting existing runs, required root files, stage directories, benchmark directory, metadata that records only `api_key_env`, artifact index create/load/record/find/find_by_stage/write, and timestamped transcript/decision trace append.
- Commands run: `python -m pytest tests/unit/runtime -q`; `python -m pytest -q`.
- Result summary: runtime tests passed with 12 tests; full suite passed with 52 tests. M06 smoke checks create run directories and required files without reading secrets or hidden benchmark contents.
- Reviews: M06 spec review initially failed because it did not confirm artifact/JSONL tests; added explicit artifact-index initialization and unknown secret-like field redaction tests, then passed spec re-review. M06 quality review requested fail-closed artifact path handling; fixed out-of-run absolute path and `..` traversal rejection with regression tests.
- Environment note: a manual smoke directory under ignored `codeagent_runs/_smoke_tmp` caused Windows directory enumeration/cleanup commands to time out. The path is ignored by Git and should be cleaned after the process/file lock clears; future smoke tests should use pytest-managed `tmp_path` or project venv temp dirs.
- Quality re-review: APPROVED; no P0/P1/P2 issues remain after fail-closed artifact path handling.
- Next step: continue M07 project context tools and sensitive filtering.

### 2026-06-03 M07 Project Context Tools and Sensitive Filtering

- Completed content: added `codeagent/context/sensitive_filter.py`, `file_reader.py`, `path_utils.py`, `code_search.py`, `scanner.py`, package exports, and context unit tests.
- Behavior implemented: Python project scanning for source/test/config/dependency files, skipped-path reporting for denied/generated paths, safe text reads with truncation, keyword search with result/file limits, `.env`/key/cert/token and generated directory filtering, benchmark `visible_roots`/`hidden_roots` allowlist enforcement, safe traversal over inaccessible sibling directories, denied-directory recursion pruning, and direct empty results for explicitly hidden search roots.
- Requirements/design alignment: reviewed SRS FR-20/FR-21/FR-22/FR-28, NFR-13, DR-02 and design docs for `scan_project`, `read_file`, `search_code`, sensitive filtering, and benchmark hidden path isolation.
- Commands run: `python -m pytest tests/unit/context -q`; `python -m pytest -q`; actual repository scanner/search smoke without reading secrets or hidden benchmark material.
- Result summary: context tests passed with 13 tests; full suite passed with 65 tests. Scanner/searcher skip inaccessible sibling directories instead of failing or losing already discovered files; denied directories are not recursively listed; explicit hidden search roots are not listed; visible benchmark search excludes hidden `evaluation/` paths.
- Reviews: M07 spec review initially failed because certificate files `.crt`/`.cer` were not denied; fixed suffix list and tests. Spec re-review PASS. Quality review initially requested replacing `sorted(root.rglob("*"))`; fixed with `safe_walk()`, denied-dir pruning, and hidden-root direct rejection. Quality re-review APPROVED with no P0/P1/P2 findings.
- Next step: continue M08 PatchService.

### 2026-06-03 M08 PatchService and Patch Risk Checks

- Completed content: added `codeagent/services/patch_service.py`, service exports, `codeagent/tools/patch_tools.py`, and focused patch service tests.
- Behavior implemented: create unified diffs from `FileChange`, parse file patches and hunks, validate path scope/sensitive/generated paths, reject duplicate patch targets and malformed hunk counts, summarize added/removed/changed files, apply add/modify/delete patches without Git, detect already-applied patches, preflight all planned writes/deletes before side effects, and rollback file writes on `OSError`.
- Risk checks implemented: high-risk findings for test file deletion, skip/xfail additions, hardcoded case branches, large patches over 10 files, and test assertion removal.
- Requirements/design alignment: reviewed SRS FR-23/FR-24/FR-26/FR-60/FR-64, NFR-10/NFR-12/NFR-13/NFR-14, and design docs for PatchService, patch-first, patch validation, HITL handoff, and benchmark forbidden patch patterns.
- Commands run: `python -m pytest tests/unit/tools/test_patch_service.py -q`; `python -m pytest -q`; `python -m codeagent --help`; `codeagent --help`.
- Result summary: patch service tests passed with 11 tests; full suite passed with 76 tests; both CLI help commands exited 0.
- Reviews: M08 spec review initially found two P2 issues: large patches were warnings rather than high-risk findings, and hunk header counts were not validated. Both were fixed and spec re-review PASS. M08 quality review then found two P1 issues and one P2 issue: multi-file partial apply risk, duplicate target acceptance, and assertion removal not flagged. All were fixed; quality re-review APPROVED with no P0/P1/P2 findings.
- Next step: continue M09 ShellRunner and test command execution.

### 2026-06-03 M09 ShellRunner and Test Command Execution

- Completed content: added command approval/result models in `codeagent/runtime/commands.py`, `ShellRunner` and command policy in `codeagent/tools/shell_tools.py`, runtime exports, and shell runner unit tests.
- Behavior implemented: approved pytest/unittest/py_compile commands run with `shell=False`; rejected or policy-denied commands fail closed; cwd must be a directory; stdout/stderr/exit code/duration/timeout are captured; full stdout/stderr logs and command operation JSON records are written; benchmark auto-approval is recorded.
- Safety implemented: allowlist covers direct `pytest` and `python -m pytest/unittest/py_compile`; path-like command arguments must resolve under cwd; cwd-external absolute paths and `..` traversal are denied; high-risk pytest options `--override-ini`, `-o`, `-o=...`, `-p`, `-p...`, and `--pyargs` are denied.
- Output control: full command output is saved to logs, while `ShellResult.stdout` / `stderr` return truncated previews with `*_truncated` and `*_original_chars` metadata to satisfy FR-28.
- Requirements/design alignment: reviewed SRS FR-25/FR-26/FR-27/FR-28, SH-01~SH-05, NFR-11/NFR-17, UC-03/UC-05, and design docs for ShellRunner, shell command approval, command restrictions, benchmark auto-approval, and logging.
- Commands run: `python -m pytest tests/unit/tools/test_shell_runner.py -q`; `python -m pytest -q`; `python -m codeagent --help`; `codeagent --help`.
- Result summary: shell runner tests passed with 10 tests; full suite passed with 86 tests; both CLI help commands exited 0.
- Reviews: M09 spec review initially found one P1 FR-28 gap: long stdout/stderr were returned in full. Fixed with truncated previews plus metadata and spec re-review PASS. M09 quality review found cwd-external path argument and pytest option bypasses; fixed path argument validation and high-risk pytest option denial. Quality re-review APPROVED with no P0/P1/P2 findings.
- Next step: continue M10 Pytest and generic test result parsing.

### 2026-06-03 M10 Pytest and Generic Test Result Parsing

- Completed content: added normalized test-result dataclasses, pytest output parser, unittest output parser, shell-result dispatch helper, adapter exports, and focused unit tests.
- Behavior implemented: parsers extract passed/failed/errors/skipped counts, total count, failing test identifiers, error summaries, timeout status, parser confidence, command text, exit code, and stdout/stderr log paths. Unknown output falls back to low-confidence results instead of fabricating counts.
- Benchmark compatibility: visible benchmark configs use `python -m unittest discover ...`; M10 includes a unittest parser for those command summaries while preserving hidden-material isolation. No `evaluation`, `oracle_tests`, or `expected_result.json` contents were read.
- Requirements/design alignment: reviewed SRS FR-46/FR-47/FR-48/FR-49, `TestResult`, SH-05, and design docs for `PytestResultParser`, `TestResult`, `parse_result(shell_result)`, pytest stdout parsing, and unittest/JUnit extension points.
- Commands run: `python -m pytest tests/unit/tools/test_test_result_parser.py -q`; `python -m py_compile codeagent/adapters/test_result.py codeagent/adapters/pytest_adapter.py codeagent/adapters/unittest_adapter.py codeagent/tools/pytest_tools.py`; `python -m pytest -q`; `python -m codeagent --help`; `codeagent --help`.
- Result summary: M10 parser tests passed with 8 tests; full suite passed with 94 tests; both CLI help commands exited 0; py_compile completed without errors.
- Failures and fixes: TDD red run first failed on missing `codeagent.adapters`; later metadata red test exposed missing `command`/`exit_code`/`log_paths`; quality review found a P1 loss of parse data when ShellResult previews are truncated. Fixed by preserving ShellResult metadata and reading full stdout/stderr log files when previews are truncated, with regression coverage.
- Reviews: M10 spec review PASS. M10 quality review initially requested the truncated-log fix; quality re-review APPROVED with no P0/P1/P2 findings.
- Next step: continue M11 ToolRegistry, Permission Policy, and Tool-Level HITL.

### 2026-06-03 M11 ToolRegistry, Permission Policy, and Tool-Level HITL

- Completed content: added `ToolRegistry`, `ToolSpec`, default stage-scoped tool registration, `ToolPermissionPolicy`, `ToolCallContext`, `PermissionDecision`, tool-level approval request/decision models, and `ToolHITLInterceptor`.
- Behavior implemented: default registry exposes tools by stage; unknown stages fail closed; readonly and patch-producing tools run automatically; output writes are allowed only under the run output directory; side-effect tools require approval unless benchmark auto-approval is configured and stage scope permits the tool.
- HITL implemented: side-effect calls without a decision create an approval request; approve/edit executes; reject/respond/cancel does not execute; decisions are appended to `decision_trace.jsonl`; benchmark auto-approval records an automatic approve decision.
- Safety fixes: direct side-effect calls are checked against stage scope before benchmark auto-approval; malformed output-write paths and NUL paths return `deny`; empty edited payloads are preserved instead of falling back to original args.
- Requirements/design alignment: reviewed SRS FR-15/FR-16/FR-23~FR-28, FR-68/FR-71/FR-82/FR-83, NFR-14/NFR-21, and design docs for `ToolRegistry`, `ToolPermissionPolicy`, workflow/tool HITL, decision trace, and benchmark auto-approval.
- Commands run: `python -m pytest tests/unit/tools/test_permissions.py -q`; `python -m pytest tests/unit/tools -q`; `python -m py_compile codeagent/tools/permissions.py codeagent/tools/registry.py codeagent/tools/hitl.py codeagent/tools/__init__.py`; `python -m pytest -q`; `python -m codeagent --help`; `codeagent --help`.
- Result summary: M11 permission tests passed with 13 tests; tool tests passed with 42 tests; full suite passed with 107 tests; py_compile completed without errors; both CLI help commands exited 0.
- Failures and fixes: spec review found stage-scope bypass under benchmark auto-approval; fixed with default-registry stage enforcement. Quality review found empty edit payload fallback and malformed path exceptions; fixed with regression tests. A parallel validation attempt made nested pytest exceed the shell-runner test timeout; sequential rerun passed, so future verification should not run nested pytest suites in parallel.
- Reviews: M11 spec review initially failed on stage-scope bypass, then PASS after fix. M11 quality review requested two fixes; quality re-review APPROVED with no P0/P1/P2 findings.
- Next step: continue M12 Model Factory and Prompt Registry.

### 2026-06-03 M12 Model Factory and Prompt Registry

- Completed content: added secure model secret resolution, OpenAI-compatible `ModelClientFactory`, structured output retry helper, centralized `PromptRegistry`, model/agent package exports, and focused model/agent unit tests.
- Behavior implemented: model config maps to `langchain_openai.ChatOpenAI` with model, base URL, API key, temperature, timeout, retries, and optional max tokens; missing or invalid API key env names produce redacted errors; secret records expose only env var plus `<redacted>`; structured output helper retries Pydantic validation; prompts cover required role, inputs, tools, schema, patch-first, hidden-oracle, no-secret, verification, failure behavior, and audit rules.
- Official docs and dependency check: reviewed LangChain structured output docs, LangChain/OpenRouter integration docs, and OpenRouter quickstart/API compatibility docs. Local versions: `langchain==1.3.2`, `langchain-openai==1.2.2`, `langgraph==1.2.2`, `openai==2.40.0`, `pydantic==2.10.6`; `langchain-openrouter` is not installed. Current implementation follows project design using `ChatOpenAI(base_url=...)`; dedicated `ChatOpenRouter` is a recorded future integration option.
- Requirements/design alignment: reviewed SRS AI-01~AI-05, FR-81, DR-01/DR-02, NFR-25, design `ModelClientFactory`, model access, prompt engineering constraints, and structured output schema guidance.
- Commands run: `python -m pytest tests/unit/models tests/unit/agents -q`; `python -m py_compile codeagent/models/__init__.py codeagent/models/secrets.py codeagent/models/factory.py codeagent/models/structured_outputs.py codeagent/agents/__init__.py codeagent/agents/prompts.py`; `python -m pytest -q`; `python -m codeagent --help`; `codeagent --help`.
- Result summary: M12 tests passed with 10 tests; full suite passed with 117 tests; py_compile completed without errors; both CLI help commands exited 0.
- Failures and fixes: TDD red run first failed on missing `codeagent.models` and `codeagent.agents`; quality review found prompt section under-specification and secret-like `api_key_env` error leakage. Fixed with prompt structure tests, env var name validation, and redacted secret records.
- Reviews: M12 spec review PASS. M12 quality review initially requested prompt and secret fixes; quality re-review APPROVED with no P0/P1/P2 findings.
- Next step: continue M13 AgentState, StageResult, ErrorRecord, and Report Schemas.

### 2026-06-03 M13 AgentState, StageResult, ErrorRecord, and Report Schemas

- Completed content: added checkpoint-safe workflow state helpers, structured error records, stage/tool/HITL/code-change/test/debug/repair report schemas, package exports, and focused state/schema unit tests.
- Behavior implemented: initial `AgentState` includes run metadata, current graph position, messages, todo list, context summary, artifact refs, stage results, pending interrupt, and error slot; `state_to_json_dict()` converts paths/tuples/Pydantic models to JSON-safe primitives and rejects unsupported objects, non-string dict keys, overlong strings, and non-finite floats.
- Report schema implemented: `StageResult`, `ToolCallRecord`, `HumanDecision` with `edited_payload`, `CodeChange`, `TestResultRecord`, `DebugResult`, `RepairResult`, and `ErrorRecord`; paths are normalized to POSIX strings and large summaries are bounded.
- Requirements/design alignment: reviewed SRS FR-13/FR-14, FR-67~FR-72, FR-81~FR-84, the SRS data-object table, and design docs 03/05/07/09 for TypedDict state, Pydantic persisted objects, stage_result, error handling, checkpoint recovery, and artifact-backed reports.
- Commands run: `python -m pytest tests/unit/workflow/test_state_schema.py -q`; `python -m pytest tests/unit/workflow/test_state_schema.py tests/unit/runtime/test_artifacts_and_logs.py tests/unit/tools/test_permissions.py -q`; `python -m compileall -q codeagent`; `python -m codeagent --help`; `python -m pytest -q`.
- Result summary: M13 state/schema tests passed with 13 tests; related validation passed with 29 tests; full suite passed with 130 tests; compileall and CLI help exited 0.
- Failures and fixes: TDD red run first failed on missing `codeagent.errors`. Spec review found missing `HumanDecision.edited_payload` and missing `blocked`/`skipped` tool statuses; fixed with regression tests. Quality review found non-standard JSON risk for `NaN`/`Infinity`; fixed with explicit non-finite float rejection and `allow_nan=False`.
- Reviews: M13 spec review initially requested HITL/tool-status fixes, then PASS after repair. M13 quality review initially requested non-finite float handling, then APPROVED with no P0/P1/P2 findings.
- Next step: continue M14 Report Writers and Audit Logs.

### 2026-06-03 M14 Report Writers and Audit Logs

- Completed content: added `ReportWriter`, `DecisionTraceWriter`, structured report reference errors, reports package exports, and report writer unit tests.
- Behavior implemented: writes `stage_result.json`, `stage_report.md`, `final_report.md`, transcript events, decision trace events, and artifact index entries; stage/final reports reject unregistered artifact ids; failed/cancelled stages require reason and next suggestion.
- Report safety implemented: final report is generated only from `StageResult`, `ArtifactStore`, and audit logs; failure/cancel details include error id, category, message, related artifacts, and next suggestion; Markdown table cells escape `|` and collapse newlines.
- Requirements/design alignment: reviewed SRS FR-67~FR-72, FR-83/FR-84, NFR-08, and design docs 03/07/09 for transcript, decision trace, artifact index, stage_result, final_report, failure reports, and reproducibility rules.
- Commands run: `python -m pytest tests/unit/reports -q`; `python -m pytest tests/unit/reports tests/unit/runtime tests/unit/workflow tests/unit/tools/test_shell_runner.py -q`; `python -m compileall -q codeagent/reports codeagent/runtime codeagent/workflow codeagent/tools`; `python -m codeagent --help`; `python -m pytest -q`.
- Result summary: report tests passed with 10 tests; related validation passed with 45 tests; full suite passed with 140 tests; compileall and CLI help exited 0.
- Failures and fixes: TDD red run first failed on missing `codeagent.reports.writer`. Full-suite validation exposed a brittle shell-runner nested-pytest timeout threshold; the failure-exit-code test timeout was widened while the dedicated timeout test still validates timeout behavior. Spec review found missing final-report failure validation/details; fixed with regression tests. Quality review found unescaped Markdown table cells; fixed with `_markdown_cell()` and regression tests for `|` and newline content.
- Reviews: M14 spec review initially requested final-report failure detail fixes, then PASS after repair. M14 quality review initially requested Markdown cell escaping, then APPROVED with no P0/P1/P2 findings.
- Next step: continue M15 LangGraph Main Graph and Routing.

### 2026-06-03 M15 LangGraph Main Graph and Routing

- Completed content: added `StageRouter`, `RouteDecision`, LangGraph main graph builder, `WorkflowFactory`, streaming event adapter, workflow exports, and focused routing/graph unit tests.
- Behavior implemented: entry route selects the first configured stage; stage route nodes append route decisions to `AgentState.decision_trace`; conditional edges route implementation/testing/debugging/repair to final success/failure/cancelled or the next selected stage; repair failures loop back to debugging until `max_repair_attempts`.
- Safety implemented: only `succeeded` stages may continue; `skipped`, `pending`, and `running` end as explicit failure instead of silently advancing; stage handlers returning unknown `AgentState` keys are rejected before LangGraph can drop them.
- Streaming implemented: `stream_workflow_events()` normalizes LangGraph updates into `node_completed`, `route_decision`, `stage_result`, `final_status`, and raw fallback events; repeated debug/repair loop stage results are preserved.
- Requirements/design alignment: reviewed SRS FR-13/FR-14/FR-17/FR-84/FR-87, NFR-09/NFR-18/NFR-22, and design docs 04/05/07 for main graph routing, route_after functions, testing failure to debug, repair retry loop, route decision logging, and streaming event shape.
- Commands run: `python -m pytest tests/unit/workflow/test_routing.py -q`; `python -m pytest tests/unit/workflow -q`; `python -m compileall -q codeagent/workflow`; `python -m codeagent --help`; `python -m pytest -q`.
- Result summary: M15 routing tests passed with 11 tests; workflow tests passed with 24 tests; full suite passed with 151 tests; compileall and CLI help exited 0.
- Failures and fixes: TDD red run first failed on missing `codeagent.workflow.factory`. Initial graph tests used an undeclared `visited` key and were corrected to use schema-declared `messages`. Spec review found incomplete statuses advancing and missing stream adapter; fixed with regression tests. Quality review found retry stage_result de-duplication and unknown state-key loss; fixed with event emission and handler-output validation tests.
- Reviews: M15 spec review initially requested incomplete-status and streaming fixes, then PASS after repair. M15 quality review initially requested retry event preservation and unknown-key validation, then APPROVED with no P0/P1/P2 findings.
- Next step: continue M16 SQLite Checkpoint, Interrupt, and Resume.

### 2026-06-03 M16 SQLite Checkpoint, Interrupt, and Resume

- Completed content: added `CheckpointManager`, resume inspection/render helpers, `resume_run_from_checkpoint()`, CLI `resume --output-root/--decision-json`, checkpointer injection for `WorkflowFactory`, and integration tests with real LangGraph interrupt/resume.
- Behavior implemented: `thread_id` is derived from `run_id`; SQLite saver is created with an explicit connection lifecycle; pending interrupt payloads are persisted as JSON-safe files; `Command(resume=...)` resumes a checkpointed interrupt and clears pending interrupt state.
- Recovery fallback implemented: `resume --run-id` detects missing/corrupt checkpoints and falls back to artifact index plus final report excerpt; initialized placeholder final reports are not misclassified as completed unless `final_report` is registered as an artifact.
- Robustness implemented: malformed `pending_interrupt.json` no longer crashes inspection; invalid `--decision-json` produces a stable CLI error without traceback.
- Requirements/design alignment: reviewed SRS FR-14/FR-15/FR-16/FR-19/FR-68/FR-70~FR-72/FR-83/FR-87, UC-07, and design docs 04/05/07/09 for SQLite checkpoint, `thread_id=run_id`, pending interrupt, resume, and artifact fallback.
- Commands run: `python -m pytest tests/integration/test_resume.py -q`; `python -m pytest tests/integration/test_resume.py tests/unit/runtime tests/unit/workflow tests/test_cli_contract.py -q`; `python -m compileall -q codeagent/workflow codeagent/cli codeagent/runtime`; `python -m codeagent --help`; `python -m pytest -q`; `python -m codeagent resume --run-id missing-run --output-root .`.
- Result summary: resume integration tests passed with 9 tests; related validation passed with 49 tests; full suite passed with 160 tests; compileall and CLI help exited 0; missing-run resume smoke exited 1 with the expected `not_found` summary.
- Failures and fixes: TDD red run first failed on missing `codeagent.workflow.checkpoint`. Spec review found placeholder final report completion misclassification and missing actual resume path; fixed with final-report artifact completion detection and real interrupt/resume tests. Quality review found malformed pending interrupt and invalid decision JSON crashes; fixed with stable fallback/error handling.
- Reviews: M16 spec review initially requested completion/resume fixes, then PASS after repair. M16 quality review initially requested bad JSON handling, then APPROVED with no P0/P1/P2 findings.
- Next step: continue M17 ImplementationSubgraph.

### 2026-06-03 M17 ImplementationSubgraph Start

- Alignment review: rechecked M17 scope against SRS goals for implement -> test -> debug -> repair, FR-13/FR-14, FR-67~FR-72, FR-81~FR-84, and design docs 04/05/06 for ImplementationSubgraph, patch-first, workflow-level HITL, `py_compile` syntax checks, artifact-backed reports, and decision trace records.
- Planned implementation boundary: create a deterministic `ImplementationService` that consumes structured implementation intent and existing `PatchService`/`ShellRunner`/`ReportWriter`; later LLM agent nodes only need to supply validated structured file changes.
- Commands run: targeted reads of `docs/codex/plans.md`, `docs/codex/implement.md`, SRS, design docs 04/05/06, `PatchService`, `ShellRunner`, `ReportWriter`, `ArtifactStore`, `AgentState`, and workflow factory/routing modules.
- Next step: add failing M17 integration tests for success, patch validation failure, syntax-check failure, cancellation, and subgraph handler state output.

### 2026-06-03 M17 ImplementationSubgraph

- Completed content: added `ImplementationPlan`, `ImplementationFileChange`, `ImplementationRequest`, `ImplementationService`, deterministic implementation stage handler, interrupting implementation subgraph, and M17 integration tests.
- Behavior implemented: generates `implementation_plan.md`, `implementation_plan.json`, `implementation.patch.diff`, `patch_attempts.json`, `changed_files.json`, `syntax_check.log`, `implementation_report.md`, and `stage_result.json`; validates patch candidates, retries alternate candidates, blocks sensitive/escaped targets before diff persistence, records approval decisions, applies approved patches, runs `python -m py_compile`, and writes artifact-backed reports.
- HITL/resume implemented: `build_interrupting_implementation_subgraph()` separates `prepare_patch`, `approve_patch` with LangGraph `interrupt()`, and `apply_patch`; resume approve applies the previously approved patch file after `patch_sha256` verification instead of regenerating patch content; `implementation_plan.json` preserves the approved plan for syntax checks and reporting.
- Requirements/design alignment: reviewed SRS implementation/debug/test workflow goals, FR-13/FR-14, FR-67~FR-72, FR-81~FR-84, and design docs 04/05/06 for ImplementationSubgraph, workflow HITL, patch-first side effects, `py_compile`, reports, and checkpoint-safe state.
- Commands run: `python -m pytest tests/integration/test_implementation_stage.py -q`; `python -m pytest tests/integration/test_implementation_stage.py tests/integration/test_resume.py tests/unit/workflow tests/unit/tools tests/unit/reports -q`; `python -m compileall -q codeagent`; `python -m pytest -q`; `python -m codeagent --help`; `codeagent --help`.
- Result summary: M17 integration tests passed with 10 tests; related regression passed with 95 tests; full suite passed with 170 tests; compileall and both CLI help commands exited 0.
- Failures and fixes: TDD red run first failed on missing `codeagent.stages`. Quality review found sensitive candidate diff persistence; fixed with fail-closed precheck before diff generation and a `.env` regression test. Spec review found missing real interrupt/resume approval; fixed with a three-node subgraph and checkpoint resume test. Spec re-review found resume was regenerating the approved patch; fixed with `patch_sha256` and `apply_prepared_patch()`. Final spec review found report/syntax used resume-time plan; fixed by persisting and loading `implementation_plan.json`.
- Reviews: M17 quality review final result APPROVED. M17 spec review final result PASS with no remaining P0/P1/P2 findings.
- Developer report: `docs/dev_reports/M17_implementation_subgraph.md`.
- Next step: continue M18 TestingSubgraph.

### 2026-06-03 M18 TestingSubgraph Start

- Alignment review: rechecked M18 scope against SRS G-04/G-08/G-10, AC-09, UC-03, testing-stage input/output rules, and design docs 04/06 for test-plan review, test patch approval, command approval, test execution, parsing, and artifact-backed reporting.
- Planned implementation boundary: mirror the M17 deterministic service/subgraph pattern for testing. The first pass will consume structured test intent, then later LLM TestDesigner/TestWriter nodes can supply that intent.
- Commands run: targeted reads of M18 milestone block, SRS testing-stage and UC-03 sections, design testing subgraph, and existing M17 stage/subgraph files for implementation pattern reuse.
- Next step: add failing M18 integration tests for approved test-plan/test-patch/command success, command rejection, command edit, test patch restrictions, and pass/fail routing output.

### 2026-06-03 M18 TestingSubgraph Complete

- Backup before doc update: `docs/_backups/20260603_085909/plans.md`.
- Behavior implemented: structured testing plan schema, test-file patch intent, testing plan review interrupt, test patch approval interrupt, command approval interrupt, patch-first test modification, hidden benchmark path denial, patch hash tamper detection, pytest/unittest result parsing integration, stage report/artifact writing, and SQLite resume coverage for testing interrupts.
- Fixes during review: rejected hidden benchmark command paths including `--option=value`; added approved patch SHA-256 validation; repaired plan/patch edit decisions so they regenerate approval payloads; fixed stale `test_plan.json` persistence after edit so command approval and reports use the edited plan.
- Commands run: `python -m pytest tests\integration\test_testing_stage.py::test_interrupting_testing_subgraph_accepts_edited_plan_review tests\integration\test_testing_stage.py::test_interrupting_testing_subgraph_accepts_edited_patch_plan -q` first failed with stale command assertions, then passed after the persistence fix.
- Commands run: `python -m pytest tests\integration\test_testing_stage.py -q` -> 15 passed.
- Commands run: `python -m pytest tests\integration\test_testing_stage.py tests\integration\test_implementation_stage.py tests\integration\test_resume.py tests\unit\workflow tests\unit\tools tests\unit\reports -q` -> 110 passed.
- Commands run: `python -m pytest -q` -> 185 passed.
- Commands run: `python -m codeagent --help` and `codeagent --help` -> both succeeded.
- Reviews: M18 spec review final result PASS. M18 quality review final result APPROVED.
- Developer report: `docs/dev_reports/M18_testing_subgraph.md`.
- Next step: continue M19 DebuggingSubgraph.

### 2026-06-03 M19 DebuggingSubgraph Start

- Backup before doc update: `docs/_backups/20260603_090445/plans.md`.
- Alignment review: rechecked M19 against SRS G-05, FR-49~FR-56, AC-10, UC-04, debugging-stage input/output rules, and design docs 03/04/07/09 for reproduction, static-log fallback, failure summary, fault localization, root cause, repair plan, debug trace, and debug report artifacts.
- Planned implementation boundary: follow the deterministic service/subgraph pattern from M17/M18. The first pass will consume structured/debuggable inputs and local evidence; later LLM Debugger nodes can enrich root-cause reasoning through the same schemas.
- Commands run: exact-path reads/searches of M19 milestone block, SRS debugging sections, design debugging subgraph, artifact layout, and current workflow/stage patterns.
- Next step: add failing M19 integration tests for reproduction success, reproduction command rejection/static fallback, fault-localization schema, low-confidence reporting, and main-graph compatible state output.

### 2026-06-03 M19 DebuggingSubgraph Complete

- Backup before completion doc update: `docs/_backups/20260603_093507/plans.md`.
- Behavior implemented: structured debugging request/schema, reproduction command HITL, approve/edit/reject/cancel handling, static-log fallback, pytest/unittest result parsing, failure summary, fault localization, root cause, repair plan, debug trace, debug report, stage result/artifact writing, main-graph handler, standalone subgraph, and SQLite resume interrupt coverage.
- Safety implemented: debugging logs/test reports must resolve under project root or current run_dir; secret-like paths, sensitive suffixes, `Software Engineering Project.txt`, hidden benchmark paths, and hidden benchmark command arguments are denied before reading or execution.
- Fixes during review: quality review found arbitrary external log ingestion and bare hidden benchmark command tokens; added red tests for both, then fixed path allow-root checks and command token checks.
- Commands run: `python -m pytest tests\integration\test_debugging_stage.py -q` -> 12 passed.
- Commands run: `python -m pytest tests\integration\test_debugging_stage.py tests\integration\test_testing_stage.py tests\integration\test_implementation_stage.py tests\integration\test_resume.py tests\unit\workflow tests\unit\tools tests\unit\reports -q` -> 122 passed.
- Commands run: `python -m pytest -q` -> 197 passed.
- Commands run: `python -m codeagent --help` and `codeagent --help` -> both succeeded.
- Reviews: M19 spec review final result PASS. M19 quality review final result APPROVED.
- Developer report: `docs/dev_reports/M19_debugging_subgraph.md`.
- OpenRouter validation note: current milestones have not consumed OpenRouter tokens because stage services are still deterministic/test-injected. Before final example and benchmark execution, run a controlled real LLM smoke using `OPENROUTER_API_KEY` without printing or persisting the secret value.
- Next step: continue M20 RepairSubgraph and Multi-Round Repair Loop.

### 2026-06-03 M20 RepairSubgraph Start

- Backup before doc update: `docs/_backups/20260603_093756/plans.md`.
- Alignment review: rechecked M20 against SRS FR-57~FR-65, AC-11, UC-05, repair-stage input/output rules, and design docs 03/04/06/07/09 for final repair plan, repair patch approval, risk checks, patch application, regression command approval, after-test logs, repair report, and multi-round route behavior.
- Planned implementation boundary: mirror M17/M18/M19 service/subgraph pattern. The first pass will use structured repair plans and deterministic patch generation; later LLM Repairer nodes can supply richer plans through the same schema.
- Commands run: exact-path reads/searches of M20 milestone block, SRS repair-stage sections, design repair subgraph, PatchService risk behavior, and current workflow routing contracts.
- Next step: add failing M20 integration tests for repair success, risky patch rejection, regression command rejection, repair verification failure routing, max-attempt final failure, and interrupt resume.

### 2026-06-03 M20 RepairSubgraph Complete

- Backup before completion doc update: `docs/_backups/20260603_101303/plans.md`.
- Behavior implemented: structured repair plan schema, repair patch generation, risk checker, patch approval interrupt, patch SHA-256 tamper check, patch application, regression command approval interrupt, after-test log, parsed repair test result, repair report, main-graph handler, standalone subgraph, SQLite resume, and failed repair routing through the main graph retry limit.
- Safety implemented: sensitive/generated/hidden repair targets are prechecked before diff generation or candidate artifact writes; repair patches modifying tests or test infrastructure are high risk; regression commands reject hidden benchmark paths and tokens.
- Fixes during review: spec/quality review found hidden benchmark repair paths could be diffed or read before validation, and test infrastructure files were not high-risk. Added red tests for `.env`, `evaluation`, `oracle_tests`, `expected_result.json`, and `conftest.py`, then fixed path prechecks and risk classification.
- Related regression fix: related-suite validation exposed a brittle ShellRunner long-output test timeout under load; widened that non-timeout scenario while preserving dedicated timeout behavior.
- Commands run: `python -m pytest tests\integration\test_repair_stage.py -q` -> 16 passed.
- Commands run: `python -m pytest tests\integration\test_repair_stage.py tests\integration\test_debugging_stage.py tests\integration\test_testing_stage.py tests\integration\test_implementation_stage.py tests\integration\test_resume.py tests\unit\workflow tests\unit\tools tests\unit\reports -q` -> 138 passed.
- Commands run: `python -m pytest -q` -> 213 passed.
- Commands run: `python -m codeagent --help` and `codeagent --help` -> both succeeded.
- Reviews: M20 spec review final result PASS. M20 quality review final result APPROVED.
- Developer report: `docs/dev_reports/M20_repair_subgraph.md`.
- Next step: continue M21 Wizard, Streaming Progress, and Approval UI.

### 2026-06-03 M21 Wizard, Streaming Progress, and Approval UI Start

- Backup before doc update: `docs/_backups/20260603-101725/plans.md`.
- Alignment review in progress: rechecking M21 against SRS CLI/HITL/progress/cancel requirements and design docs for workflow interrupts, report artifacts, streaming events, and CLI behavior.
- Planned implementation boundary: keep terminal rendering thin and test controller logic separately so scripted wizard input, approval decisions, progress event formatting, and cancellation reporting remain stable in CI.
- OpenRouter validation reminder: M21 remains UI/controller oriented and should not consume tokens; a controlled real LLM smoke with `OPENROUTER_API_KEY` is still required before final example/benchmark execution, without printing or persisting the secret value.
- Next step: add failing M21 integration tests for wizard configuration, approve/edit/reject/cancel approval handling, progress rendering, and cancellation final report.

### 2026-06-03 M21 Wizard, Streaming Progress, and Approval UI Complete

- Backup before completion doc update: `docs/_backups/20260603-103814/plans.md`.
- Behavior implemented: semi-interactive `codeagent wizard` answer collection, `TaskConfig(mode="wizard")` normalization, task summary rendering, run-dir initialization on confirmation, cancelled wizard stage/final report writing, approval decision parsing for approve/edit/reject/respond/cancel, edit payload JSON validation, allowed-decision enforcement, and normalized progress event rendering for stage/route/result/tool/final/approval events.
- Safety and scope implemented: M21 stays at CLI/controller level and does not run business stages or consume OpenRouter tokens; project path must be an existing directory; input materials must exist and may be files or directories; cancellation writes run artifacts without modifying project source.
- TDD and fixes: initial red run failed on missing `codeagent.cli.approval_console`; after implementation, one Rich rendering test exposed markup stripping and was fixed with `markup=False`. Quality review found an existing file could be accepted as `project_path`; added a failing regression test, then fixed directory-aware validation.
- Commands run: `python -m pytest tests\integration\test_cli_wizard.py -q` -> 11 passed.
- Commands run: `python -m pytest tests\integration\test_cli_wizard.py tests\test_cli_contract.py tests\unit\workflow tests\unit\reports tests\unit\runtime -q` -> 61 passed.
- Commands run: `python -m pytest -q` -> 224 passed.
- Commands run: `python -m compileall -q codeagent` -> passed.
- Commands run: `python -m codeagent --help`, `python -m codeagent wizard --help`, and `codeagent --help` -> all succeeded.
- Reviews: M21 spec review final result PASS. M21 quality review initially requested project-directory validation, then APPROVED after repair.
- Developer report: `docs/dev_reports/M21_wizard_streaming_approval_ui.md`.
- OpenRouter validation note: no OpenRouter token consumption expected in M21; keep the controlled real LLM smoke before final example/benchmark execution.
- Next step: continue M22 Non-Interactive Run and Stage Subcommands.

### 2026-06-03 M22 Non-Interactive Run and Stage Subcommands Start

- Backup before doc update: `docs/_backups/20260603-104021/plans.md`.
- Alignment review in progress: rechecking M22 against SRS CLI non-interactive mode, stage command mapping, path validation, output directory/run artifact rules, workflow streaming, and explicit failure behavior.
- Planned implementation boundary: introduce one CLI-to-`TaskConfig` normalization path for `run --config`, `run --project --stages`, and stage subcommands, then execute configured stages through the existing LangGraph factory and deterministic stage handlers where enough structured inputs are available.
- OpenRouter validation reminder: M22 should not print or persist secrets; if real LLM smoke becomes possible, keep it controlled and do not consume tokens except for the planned final validation point.
- Next step: add failing M22 integration tests for config run, stage subcommand mapping, invalid path, invalid stage order, run-dir creation, and streaming progress output.

### 2026-06-03 M22 Non-Interactive Run and Stage Subcommands Complete

- Backup before completion doc update: `docs/_backups/20260603-110243/plans.md`.
- Behavior implemented: `run --config`, `run --project --stages`, and `implement/test/debug/repair` normalize through `TaskConfig(mode="run")`; CLI executor creates run context, runs the LangGraph main workflow, renders stream progress, writes stage reports and final report, and exits nonzero for final failed/cancelled runs.
- Stage execution implemented: testing stage can execute user-supplied pytest/unittest/py_compile-like commands through `ShellRunner` policy and parse results; debugging stage reuses `DebuggingService` with static logs or non-interactive reproduction; implementation and repair create explicit failed reports when structured plans are absent instead of reporting skeleton success.
- Validation and examples implemented: config loader rejects `project_path` files; `examples/task.yaml` provides a public debug-only static-log run that succeeds without LLM calls or project modification.
- TDD and fixes: initial M22 red run showed all five new CLI tests failing against skeleton commands; implementation added `cli_mapping`, `executor`, command wiring, example config, and updated CLI help contracts until tests passed.
- Commands run: `python -m pytest tests\integration\test_cli_run.py -q` -> 5 passed.
- Commands run: `python -m pytest tests\integration\test_cli_run.py tests\test_cli_contract.py -q` -> 9 passed.
- Commands run: `python -m pytest tests\integration\test_cli_run.py tests\integration\test_cli_wizard.py tests\test_cli_contract.py tests\unit\config tests\unit\workflow tests\unit\reports tests\unit\runtime tests\unit\tools -q` -> 140 passed.
- Commands run: `python -m pytest -q` -> 229 passed.
- Commands run: `python -m compileall -q codeagent` -> passed.
- Commands run: `codeagent run --config examples\task.yaml` and `python -m codeagent run --config examples\task.yaml` -> both succeeded and generated ignored `codeagent_runs/examples/<run_id>/` directories.
- Commands run: `python -m codeagent --help` and `codeagent --help` -> both succeeded.
- Reviews: M22 spec review final result PASS. M22 quality review final result APPROVED.
- Developer report: `docs/dev_reports/M22_non_interactive_run_stage_subcommands.md`.
- OpenRouter validation note: M22 intentionally does not consume tokens; controlled real LLM smoke remains required before final example/benchmark execution.
- Next step: continue M23 BenchmarkRunner, CaseLoader, Evaluator, and Aggregator.

### 2026-06-03 M23 BenchmarkRunner, CaseLoader, Evaluator, and Aggregator Start

- Backup before doc update: `docs/_backups/20260603-110504/plans.md`.
- Alignment review in progress: rechecking benchmark scope against SRS benchmark reporting/isolation requirements and design doc 10 for clean copied case directories, visible/hidden path enforcement, auto-approval trace, evaluator inputs, metrics, and report artifacts.
- Non-negotiable boundary: original `benchmark/cases/**` and `benchmark/selfbuilt/cases/**` directories remain reusable read-only templates; every benchmark run must copy each case into a clean run workspace before Agent execution, patching, dependency setup, testing, logging, or evaluation.
- OpenRouter validation reminder: user requested real OpenRouter calls from this point forward. Implement controlled LLM smoke and LLM-driven structured request builders as soon as M23 runner is stable; the only supported secret source is `OPENROUTER_API_KEY`, not checked-in files or `Software Engineering Project.txt`.
- Next step: add failing M23 integration tests for case loading, case clean-copy reuse, `{{CASE_DIR}}` command substitution, hidden path denial, auto-approval trace, evaluator artifact requirements, and aggregate reports.

### 2026-06-03 OpenRouter Real LLM Integration Directive

- User directive: from this point forward, plan and implement real OpenRouter API calls so the LLM can drive the agent workflow, and be ready to return to code implementation when live-call issues expose robustness gaps.
- Safety decision: `Software Engineering Project.txt` remains a forbidden secret source under project rules. Do not read, print, copy, summarize, or parse it for API keys. Real calls must obtain credentials from `OPENROUTER_API_KEY` or another explicitly configured environment variable resolved by `SecretResolver`; secret values must never appear in logs, reports, transcripts, exceptions, or git.
- Current environment check: process-level `OPENROUTER_API_KEY` is still absent in the already-running Codex process, but the Windows user-level environment variable is present. `SecretResolver` now falls back to the Windows user environment when the process environment has not inherited the value, without printing or persisting the secret. A controlled OpenRouter smoke using `ModelClientFactory` returned `CODEAGENT_OPENROUTER_SMOKE_OK`.
- Implementation impact: after M23 benchmark runner, prioritize M24 work on controlled LLM smoke, structured-output invocation for implementation/repair plans, request builders that feed existing deterministic stage services, prompt hardening, retry/error reporting, and regression tests that keep hidden benchmark and secret-isolation guarantees intact.

### 2026-06-03 M23 BenchmarkRunner, CaseLoader, Evaluator, and Aggregator Complete

- Backup before completion doc update: `docs/_backups/20260603-114112/plans.md`.
- Behavior implemented: benchmark config loading, enabled case enumeration, clean per-case copy under `case_workspaces/<case_id>`, `{{CASE_DIR}}` substitution, benchmark-mode auto approval, CLI `benchmark` execution, per-case failure isolation, aggregate `benchmark_result.json` and `benchmark_report.md`, and original source case reuse without generated artifacts.
- Hidden oracle behavior implemented: if a case test command references hidden `evaluation/`, `oracle_tests/`, or `expected_result.json`, the Agent-visible workflow receives a safe visible `python -m py_compile ...` smoke command, while `CaseEvaluator` executes the original oracle command runner-only from the copied case root and records `oracle_success` plus oracle logs under the benchmark run directory.
- Robustness fixes: nested hidden paths already inside the copied case are preserved instead of flattened; Agent-visible smoke commands exclude all Python files under hidden roots; preparation/load/path failures are captured as failed `CaseEvaluation` entries and do not abort later cases; debug after test now prefers generated test logs over stale external failure materials.
- OpenRouter validation: user-level `OPENROUTER_API_KEY` is available; `SecretResolver` can resolve it even when the current process env has not inherited the value; real OpenRouter smoke through `ModelClientFactory` succeeded with `CODEAGENT_OPENROUTER_SMOKE_OK`.
- Commands run: `python -m pytest tests\integration\test_benchmark_runner.py -q` -> 8 passed.
- Commands run: `python -m pytest tests\integration\test_benchmark_runner.py tests\integration\test_cli_run.py tests\test_cli_contract.py tests\unit\models\test_model_factory.py -q` -> 24 passed.
- Commands run: `python -m compileall -q codeagent` -> passed.
- Commands run: `python -m codeagent benchmark --config benchmark\benchmark.yaml` -> completed 6 cases, generated aggregate reports, success_rate=0.00 because implementation/repair stages still require LLM-generated structured plans in later milestones. Re-run after hidden-root smoke filtering also completed 6 cases and generated aggregate reports.
- Commands run: `codeagent benchmark --config benchmark\benchmark.yaml` -> completed 6 cases, generated aggregate reports, success_rate=0.00 for the same missing LLM-plan reason.
- Commands run: `python -m pytest -q` -> 239 passed.
- Commands run: `python -m codeagent --help` and `codeagent --help` -> both succeeded.
- Commands run: `git ls-files --others --exclude-standard -- benchmark codeagent_runs` -> no generated benchmark or run artifacts are untracked.
- Reviews: M23 spec review initially requested runner-only hidden oracle execution and then nested hidden-root filtering; final result APPROVED. M23 quality review initially requested copied-path preservation and per-case preparation isolation, then requested the same hidden-root smoke filtering; final result APPROVED.
- Developer report: `docs/dev_reports/M23_benchmark_runner.md`.
- Next step: continue M24 Public Benchmark Pass with real LLM-driven planning/implementation/repair integration.

### 2026-06-03 M24 Public Benchmark Pass Start

- Backup before doc update: `docs/_backups/20260603-115727/plans.md`.
- Alignment review in progress: rechecking public benchmark failures against SRS FR-31 implementation plan generation, FR-55 repair suggestion generation, FR-58 repair patch generation, AI-01~AI-05 model access/error handling, and design docs for LLM-driven implementation/repair subgraphs.
- Baseline failure observed: public benchmark runner completes all 6 enabled cases, but implementation cases fail because the CLI executor still reports “structured implementation plan required,” and QuixBugs repair loops fail because no structured repair plan is generated.
- OpenRouter readiness: user-level `OPENROUTER_API_KEY` is present and usable through `SecretResolver`; both a small smoke prompt and the previously configured default model `anthropic/claude-opus-4.8` returned successfully via `ModelClientFactory`.
- Engineering direction: build LLM plan generation as an auditable orchestration layer over the existing deterministic `ImplementationService` and `RepairService`, then iterate on context selection, schema validation, retry diagnostics, prompt quality, patch risk control, and benchmark evidence until the public cases converge.
- Next step: add failing tests for LLM-generated implementation/repair requests and non-interactive CLI handlers that no longer fail only because a structured plan was missing.

### 2026-06-03 M24 Quality Escalation Directive

- User directive: do not treat the remaining work as a passable MVP. The target is a mature, high-completion product that is responsible for real environments, real OpenRouter calls, repeatable benchmark evidence, and robust failure handling.
- Plan adjustment: M24 is expanded from a single public benchmark pass into a benchmark-driven LLM integration and hardening phase, and M24A is added for LLM orchestration hardening plus a self-designed regression benchmark pack.
- Allowed iteration: later M24 work may return to earlier stage services, prompt registry, model wrappers, benchmark runner, or reports when real LLM/benchmark evidence reveals design gaps. Fixes should be tested, documented, and reviewed rather than worked around.
- Quality bar: avoid placeholder success, silent skip, or “environment not configured” pass paths; missing external readiness must be a clear failure/blocker record with evidence, while configured environments must execute directly.

### 2026-06-03 M24 Public Benchmark Pass Complete

- Backup before completion doc update: `docs/_backups/20260603-125156/plans.md`.
- Alignment review: checked M24 against SRS FR-30~FR-37 implementation, FR-49~FR-56 debugging, FR-57~FR-66 repair, FR-70~FR-77 logging/benchmark, FR-81~FR-84 failure handling, AI-01 model configuration, and design docs 02/05/10 for module boundaries, clean benchmark copies, hidden oracle isolation, and output artifacts.
- Real LLM integration: `PlanGenerationService` now calls OpenRouter through `ModelClientFactory` to produce validated `ImplementationPlan` and `RepairPlan` payloads, feeds deterministic stage services, retries invalid model output, normalizes model paths to project-root-relative paths, and keeps hidden/secret benchmark paths out of prompts.
- Benchmark-driven hardening: fixed copied benchmark context filtering under `codeagent_runs`, project-relative command normalization, UTF-8 BOM/CRLF patch preservation, Windows long-path command logs, CLI stage-runtime error classification, and debug evidence selection when ShellRunner shortens log filenames.
- Real benchmark evidence: `codeagent benchmark --config benchmark\benchmark.yaml` with real OpenRouter calls completed 6/6 public cases; latest aggregate report: `benchmark/codeagent_runs/benchmark/2026-06-03_053939_990694_codeagent_course_benchmark_b44835/benchmark_result.json`, success_rate=1.00.
- Commands run: `python -m pytest tests\unit\tools\test_shell_runner.py tests\integration\test_implementation_stage.py tests\integration\test_cli_run.py tests\unit\agents tests\unit\models tests\unit\tools\test_patch_service.py tests\integration\test_benchmark_runner.py tests\test_cli_contract.py -q` -> 70 passed.
- Commands run: `python -m pytest -q` -> 250 passed.
- Commands run: `python -m compileall -q codeagent` -> passed.
- Commands run: `codeagent benchmark --config benchmark\benchmark.yaml` -> latest run completed 6 cases, success_rate=1.00 (6/6), no hidden oracle material exposed to Agent-visible workflow.
- Developer report: `docs/dev_reports/M24_public_benchmark_llm_pass.md`.
- Next step: continue M24A LLM Orchestration Hardening and Benchmark Regression Pack, including self-designed public-style regression cases and richer malformed-response diagnostics.

### 2026-06-03 M24A LLM Orchestration Hardening and Benchmark Regression Pack

- Backup before doc update: `docs/_backups/20260603-141115/M24A_docs/plans.md`.
- Alignment review: checked M24A against SRS requirements for full-process logs, benchmark success statistics, model error handling, test/debug/repair artifacts, and design docs 07/09/10 for structured validation retries, reproducible reports, clean case copies, and runner-only hidden oracle boundaries.
- LLM audit hardening: `PlanGenerationService` now writes `plan_generation_attempts.json` for implementation and repair planning attempts with schema name, prompt hash, prompt length, attempt status, redacted response preview, and redacted validation/model errors. It does not write full prompt text or secret values.
- Malformed output diagnostics: non-JSON responses, schema validation failures, and model invocation errors are retried and recorded as structured attempt entries; final `PlanGenerationError` remains redacted.
- Prompt evidence hardening: repair prompts now discover actual `ShellRunner` stdout/stderr logs even when Windows path-length protection shortens log stems to `cmd-<hash>.*.log`, so repair LLM context uses current testing evidence instead of stale fallback inputs.
- Hidden/sensitive target hardening: generated implementation/repair plans are normalized and rejected before patching if they target absolute/out-of-root paths, hidden benchmark material (`evaluation`, `oracle_tests`, `expected_result.json`), configured hidden roots, sensitive files such as `.env`, or generated directories.
- Benchmark regression pack: integration tests now build a five-case custom pack covering visible tests, runner-only oracle, nested hidden paths, project-relative commands, `{{CASE_DIR}}` placeholder normalization, aggregate reports, and reusable source templates.
- Template reuse evidence: benchmark results now include `source_snapshot_before`, `source_snapshot_after`, and `source_unchanged` per case. Public benchmark run `benchmark/codeagent_runs/benchmark/2026-06-03_060921_184932_codeagent_course_benchmark_b870e4/benchmark_result.json` reported 6/6 success and `source_unchanged=True` for every enabled case.
- Real OpenRouter evidence: process env reports `OPENROUTER_API_KEY=SET`; `python -m codeagent benchmark --config benchmark\benchmark.yaml` completed 6 enabled public cases with real LLM implementation/repair planning, success_rate=1.00.
- Secret scan: run artifacts under the latest public benchmark `case_runs` plus aggregate JSON/Markdown had no matches for OpenRouter-style key, bearer token, or `OPENROUTER_API_KEY=` value patterns.
- Commands run: `python -m pytest tests\unit\agents -q` -> 12 passed.
- Commands run: `python -m pytest tests\integration\test_benchmark_runner.py tests\unit\agents -q` -> 23 passed.
- Commands run: `python -m pytest tests\integration\test_cli_run.py tests\unit\tools\test_shell_runner.py -q` -> 20 passed.
- Commands run: `python -m pytest -q` -> 256 passed.
- Commands run: `python -m compileall -q codeagent\benchmark codeagent\agents` -> passed.
- Commands run: `python -m compileall -q codeagent` -> passed.
- Commands run: `python -m codeagent benchmark --config benchmark\benchmark.yaml` -> 6/6 passed, success_rate=1.00.
- Developer report: `docs/dev_reports/M24A_llm_orchestration_hardening.md`.
- Next step: run full repository verification, finalize M24A status, then continue to M25 optional BugsInPy readiness detection.

### 2026-06-03 M25 BugsInPy Optional Path and Environment Detection Complete

- Backup before doc update: `docs/_backups/20260603-150954/M25_docs/plans.md`.
- Alignment review: checked M25 against SRS benchmark/logging/failure-handling expectations and design 07/09/10 requirements for explicit blockers, reproducible reports, clean case copies, and runner-only hidden material boundaries.
- Environment detector: added `codeagent/benchmark/environment.py` with `BugsInPyEnvironmentDetector`, covering WSL path conversion, WSL bash availability, conda profile, `codeagent-bugsinpy-py383`, Python 3.8.3, `dos2unix`, and official BugsInPy framework scripts.
- Blocker reporting: disabled optional cases now appear in `benchmark_result.json` / `benchmark_report.md` as `blocked_cases` and `blockers`; explicitly enabled BugsInPy cases preflight before workflow execution and are marked `blocked` when the detector reports missing environment readiness.
- Clean-copy prepare path: BenchmarkRunner now preserves `prepare_command`, substitutes `{{CASE_DIR}}` to the copied run case, executes allowed BugsInPy prepare wrappers before workflow when the environment is ready, and rejects unsafe prepare commands.
- Script hardening: `prepare_bugsinpy_wsl_conda.ps1` and `run_bugsinpy_wsl_conda.ps1` now require explicit `-CaseDir`, allow clean `benchmark/codeagent_runs/**/case_workspaces/**` copies, reject paths outside allowed benchmark workspaces, and add WSL path/batch-command timeout blockers to prevent hangs.
- Current machine blocker: standalone detector reports `available=False` with WSL path conversion returning an empty repository path; manual wrapper smoke on a clean copied BugsInPy case returns `WSL bash command timed out after 60 seconds` instead of hanging indefinitely.
- Real benchmark evidence: `python -m codeagent benchmark --config benchmark\benchmark.yaml` completed the 6 enabled public cases with real OpenRouter calls, `success_rate=1.00`, and reported `blocked=1` for `bugsinpy_black_001`. Latest aggregate: `benchmark/codeagent_runs/benchmark/2026-06-03_070800_221548_codeagent_course_benchmark_adfcde/benchmark_result.json`.
- Secret scan: latest public benchmark run artifacts and aggregate reports had no OpenRouter key, bearer token, or `OPENROUTER_API_KEY=` value matches.
- Commands run: `python -m pytest tests\unit\benchmark tests\integration\test_benchmark_runner.py tests\test_cli_contract.py tests\unit\config -q` -> 60 passed.
- Commands run: `python -m pytest tests\unit\config tests\unit\tools\test_shell_runner.py -q` -> 43 passed.
- Commands run: `python -m pytest -q` -> 269 passed.
- Commands run: `python -m compileall -q codeagent scripts` -> passed.
- Commands run: `python -m compileall -q codeagent` -> passed.
- Commands run: copied `benchmark/cases/bugsinpy_black_001` to `benchmark/codeagent_runs/manual_m25/20260603-150516/case_workspaces/bugsinpy_black_001`, then ran `powershell -ExecutionPolicy Bypass -File scripts\run_bugsinpy_wsl_conda.ps1 -CaseDir <copied_case_dir> -AllowTestFailure` -> exit_code=1 with explicit `WSL bash command timed out after 60 seconds` blocker.
- Developer report: `docs/dev_reports/M25_bugsinpy_environment_detection.md`.
- Next step: continue M26 Self-Built Benchmark Pass and Final Developer Docs.

### 2026-06-03 Temporary OpenRouter Model Cost Control

- User directive: switch default LLM calls to `google/gemini-3.5-flash` while continuing benchmark-driven implementation.
- Implementation: updated `DEFAULT_MODEL_NAME` so new `TaskConfig` instances and benchmark case configs without explicit model overrides use `google/gemini-3.5-flash`.
- Documentation: updated the current execution plan and implementation guide; historical Opus 4.8 smoke-test evidence remains recorded as historical evidence only.
- Verification: `python -m pytest tests\unit\config tests\unit\models\test_model_factory.py -q` -> 38 passed; `python -m codeagent --help` -> succeeded; controlled OpenRouter smoke through default `ModelConfig()` returned the expected marker using `google/gemini-3.5-flash`.

### 2026-06-03 M26 Self-Built Benchmark Pass and Final Docs Complete

- Backup before final doc update: `docs/_backups/20260603_165726/m26_final_docs/`.
- Alignment review: checked M26 against SRS/design expectations for benchmark isolation, hidden oracle separation, reproducible reports, OpenRouter model configuration, run artifacts, and developer documentation.
- Self-built benchmark convergence: fixed Windows long-path artifact handling, internal syntax checking without `__pycache__`, runner-only oracle import path, duplicate `workspace/` LLM path normalization, and visible CSV export ordering clarification for `02_personal_ledger`.
- Real default-model evidence: `python -m codeagent benchmark --config benchmark\selfbuilt\selfbuilt_benchmark.yaml` ran with normalized `model_name: google/gemini-3.5-flash`, completed 5/5 self-built cases, `success_rate=1.00`, `blocked=0`.
- Latest aggregate: `benchmark/selfbuilt/codeagent_runs/benchmark/2026-06-03_085139_493426_codeagent_selfbuilt_python_benchmark_3bc92c/benchmark_result.json`; every case reports `oracle_success=True` and `source_unchanged=True`.
- Final documentation: expanded `README.md`; added `docs/dev_reports/M26_selfbuilt_benchmark_final_docs.md`.
- Commands run: `python -m pytest tests\unit\tools\test_patch_service.py tests\unit\reports\test_writer.py tests\unit\agents\test_plan_generation.py tests\integration\test_implementation_stage.py -q` -> 47 passed.
- Commands run: `python -m pytest tests\integration\test_benchmark_runner.py::test_hidden_oracle_can_import_workspace_package_without_modifying_oracle_sys_path -q` -> passed.
- Commands run: `python -m pytest tests\unit\config tests\unit\models\test_model_factory.py -q` -> 38 passed.
- Commands run: `python -m pytest -q` -> 277 passed.
- Commands run: `python -m compileall -q codeagent tests` -> passed.
- Commands run: `codeagent --help` and `python -m codeagent --help` -> both succeeded.
- Developer report: `docs/dev_reports/M26_selfbuilt_benchmark_final_docs.md`.
- Next step: review git diff, ensure generated benchmark artifacts remain ignored, then commit M26 changes.

### 2026-06-03 M26 Review Hardening and Final Validation

- Backup before review-hardening doc/spec updates: `docs/_backups/20260603_172444/`, `docs/_backups/20260603_180254/`, `docs/_backups/20260603_181433/`.
- Subagent review fixes: tightened generated-path normalization so real top-level directories named like `workspace` or the project root are preserved; kept wrapper-prefix stripping for empty benchmark project roots; removed local-secret-file key-source wording from active specs; corrected README stage subcommand examples; synchronized active design docs to temporary Sonnet 4.6 default; removed contradictory CSV export ordering text from `02_personal_ledger`.
- Long-path hardening: extended `codeagent.filesystem` with append/touch/is_file helpers and routed run context initialization, transcript/decision trace, checkpoint pending interrupts, artifact index, CLI testing reports, benchmark aggregate reports, benchmark prepare logs, and testing/debugging/repair stage artifact writes through long-path-aware helpers.
- Real benchmark regression and fix: first fresh self-built Sonnet run after review hardening reported 4/5 because `05_meeting_room_booking` generated SQLite connections that were not closed, causing Windows temp-directory cleanup to fail with locked `booking.db`; fixed by adding prompt coverage for explicit SQLite connection closing and adding the same visible cross-platform requirement to the case input.
- Current public benchmark evidence: `python -m codeagent benchmark --config benchmark\benchmark.yaml` completed enabled public cases with real Sonnet calls, `success_rate=1.00`, `blocked=1` for the optional BugsInPy environment blocker. Latest aggregate: `benchmark/codeagent_runs/benchmark/2026-06-03_095303_304297_codeagent_course_benchmark_b88270/benchmark_result.json`; every executed case has `source_unchanged=True`.
- Current self-built benchmark evidence: `python -m codeagent benchmark --config benchmark\selfbuilt\selfbuilt_benchmark.yaml` completed 5/5 with real Sonnet calls, `success_rate=1.00`, `blocked=0`. Latest aggregate: `benchmark/selfbuilt/codeagent_runs/benchmark/2026-06-03_100356_416230_codeagent_selfbuilt_python_benchmark_670ea1/benchmark_result.json`; every case has `oracle_success=True` and `source_unchanged=True`.
- Commands run: initial red suite for path normalization, active docs, long-path run context/artifact/benchmark/stage writes -> 11 failed as expected.
- Commands run after fixes: same red suite -> 11 passed.
- Commands run: `python -m pytest tests\unit\agents tests\unit\docs tests\unit\runtime tests\unit\reports tests\unit\benchmark -q` -> 53 passed.
- Commands run: `python -m pytest tests\integration\test_testing_stage.py -q` -> 16 passed; `python -m pytest tests\integration\test_debugging_stage.py -q` -> 13 passed; `python -m pytest tests\integration\test_repair_stage.py -q` -> 17 passed.
- Commands run: `python -m pytest -q` -> 289 passed; `python -m compileall -q codeagent tests` -> passed.
- Commands run: `python -m codeagent --help` and `codeagent --help` -> both succeeded.
- Commands run: controlled OpenRouter smoke through default `ModelConfig()` -> `OpenRouter smoke OK for google/gemini-3.5-flash`.
- Safety checks: strict OpenRouter/Bearer key scan over active code/docs/benchmark input found no real secret values; `git ls-files --others --exclude-standard -- benchmark codeagent_runs docs\_backups` returned no untracked generated benchmark artifacts or backups.
- Developer report: `docs/dev_reports/M27_review_hardening_and_final_validation.md`.

### 2026-06-03 M27 Review Follow-Up Fixes

- Review follow-up: a read-only subagent found that CodeAgent's own SQLite checkpoint initialization/status checks still relied on `with sqlite3.connect(...)`, and that plan-generation context reads plus implementation resume reads still used some direct `Path` operations.
- Fixes: checkpoint SQLite connections now use `contextlib.closing`; added regression tests proving `checkpoints.sqlite` can be deleted immediately after run-context initialization, checkpoint status, and SQLite saver setup. Plan-generation visible-context reads, failure-log discovery, and implementation prepared-plan/patch reads now use long-path-aware filesystem helpers.
- Known path-normalization limitation: wrapper-prefix stripping remains existence-based for `workspace/` and `project/` because benchmark copies commonly expose project roots named `workspace`; if a future visible requirement intentionally creates a new top-level `workspace/` directory from an empty project, the prompt and tests should be extended with an explicit opt-out rule.
- Commands run: `python -m pytest tests\unit\runtime\test_run_context.py::test_create_run_context_closes_checkpoint_connection tests\unit\workflow\test_checkpoint.py::test_checkpoint_manager_closes_sqlite_connections -q` -> initially 2 failed with Windows file-lock `PermissionError`, then 2 passed after `contextlib.closing`.
- Commands run: `python -m pytest tests\unit\agents\test_plan_generation.py tests\integration\test_implementation_stage.py -q` -> 25 passed.
- Commands run: `python -m pytest tests\unit\runtime\test_run_context.py tests\unit\workflow\test_checkpoint.py tests\unit\reports\test_writer.py tests\unit\benchmark\test_report.py -q` -> 22 passed.

### 2026-06-03 M28 Agent Self-Test, Chinese Wizard, and Streaming UX Hardening

- User-reported gaps: benchmark testing showed `0 passed` and skipped meaningful Agent self-tests; wizard was an English fill-in flow and required a second `run --config`; progress output was sparse, English-heavy, and only printed after graph nodes completed.
- Implementation changes: `PlanGenerationService` now creates `TestingRequest` from LLM-generated `TestingPlan`; CLI testing stage uses full `TestingService` instead of directly running a configured command; `TestingService` rejects zero collected tests including `py_compile`-only smoke checks, `Ran 0 tests`, `NO TESTS RAN`, and `collected 0 items`.
- Interaction changes: `codeagent wizard` is now a Chinese form. In real terminals it uses questionary selection/multi-select controls; in non-TTY tests it uses a scriptable fallback. Confirming the form directly starts Agent execution while preserving `task_config.yaml` for audit.
- Streaming changes: main graph execution now requests `stream_mode=["updates", "custom", "messages"]`; executor and testing service emit custom events for LLM planning, patch generation/application, command execution, artifact writes, and parsed test results. CLI progress formatting is Chinese and flushes after each line.
- Benchmark reporting changes: per-case results now include `agent_test_success`, `agent_test_total`, `agent_test_command`, and `agent_test_report`; evaluator requires nonzero Agent-visible self-test success in addition to final workflow success and runner-only oracle success.
- Follow-up hardening after real benchmark: split Agent self-test timeout from runner-only oracle timeout, so short hidden-oracle budgets no longer interrupt generated public tests; default `ModelConfig.max_tokens` is now `16384` to avoid OpenRouter overbudget errors from provider-default 65536 output windows; shared redaction now removes OpenRouter key-management links and user identifiers from model error reports.
- Repair-loop hardening: repair prompts now include `testing/test_command.json`, `testing/test_result.json`, `testing/test_report.md`, debug summaries, and failure logs, and prefer the latest Agent self-test command instead of falling back to py_compile or hidden oracle commands.
- Documentation updates: README, demo flow, project implementation report, design 05/08/10, and the M28 developer report explain direct wizard execution, Agent self-tests, hidden oracle separation, streaming progress, and the cost-controlled benchmark policy. Added `benchmark/selfbuilt/meeting_room_demo_benchmark.yaml` for single-case meeting-room API demonstrations.
- CLI localization follow-up: root/command help descriptions, config validation errors, wizard path errors, and approval-console parsing errors are now Chinese; `resume` no longer advertises itself as a planned skeleton. Typer's built-in `--help` description remains framework-provided English text.
- Commands run: `python -m py_compile codeagent\context\redaction.py codeagent\config\defaults.py codeagent\config\schema.py codeagent\agents\plan_generation.py codeagent\cli\executor.py codeagent\benchmark\runner.py codeagent\benchmark\schemas.py codeagent\benchmark\evaluator.py codeagent\stages\testing_service.py` -> passed.
- Commands run: `python -m pytest tests\unit\models\test_model_factory.py tests\unit\agents\test_plan_generation.py -q` -> 24 passed.
- Commands run: `python -m pytest tests\integration\test_benchmark_runner.py::test_prepare_case_workspace_preserves_nested_hidden_paths_in_copied_case tests\integration\test_benchmark_runner.py::test_case_evaluator_reports_agent_self_test_timeout_with_collected_count -q` -> 2 passed.
- Commands run: `python -m pytest tests\unit -q` -> 185 passed.
- Commands run: `python -m pytest tests\integration\test_testing_stage.py tests\integration\test_benchmark_runner.py tests\integration\test_cli_wizard.py tests\integration\test_cli_run.py -q` -> 55 passed.
- Commands run: `python -m pytest -q` -> 303 passed.
- Commands run: `python -m compileall -q codeagent tests` -> passed.
- Commands run: `python -m codeagent --help`, `python -m codeagent benchmark --help`, `python -m codeagent wizard --help`, and `python -m codeagent resume --help` -> passed.
- Real OpenRouter validation: created ignored selected-case config `benchmark/selfbuilt/codeagent_runs/m28_selected_benchmark.yaml` with only `01_todo_manager`, then ran `python -m codeagent benchmark --config benchmark\selfbuilt\codeagent_runs\m28_selected_benchmark.yaml` -> success_rate=1.00 (1/1), Agent self-test `43 passed`, `agent_test_total=43`, `oracle_success=True`, and `source_unchanged=True`. Latest aggregate: `benchmark/selfbuilt/codeagent_runs/benchmark/2026-06-03_150709_561194_m28_selected_selfbuilt_4a3e14/benchmark_result.json`.
- Cost-control note: user requested reduced future test load; full self-built benchmark should not be run routinely. Prefer 1-2 representative cases unless explicit final acceptance requires all cases.
- Developer report: `docs/dev_reports/M28_agent_selftest_wizard_streaming_hardening.md`.
