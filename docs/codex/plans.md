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
- Default model access is OpenRouter OpenAI-compatible, model `anthropic/claude-opus-4.8`, with key from `OPENROUTER_API_KEY` or a local secret file held only in memory.
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

Scope out for MVP:

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

- Scope: create minimal Python package, `pyproject.toml`, dependency groups, test folders, examples folder.
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
- Status: pending.

### M16 SQLite Checkpoint, Interrupt, and Resume

- Scope: add SQLite checkpointer, `thread_id=run_id`, interrupt payload persistence, and `resume --run-id`.
- Key files/modules: `codeagent/workflow/checkpoint.py`, `codeagent/cli/resume.py`.
- Acceptance criteria: a pending approval can be resumed; completed run displays final report; corrupted/missing checkpoint falls back to artifact summary.
- Verification commands: `python -m pytest tests/integration/test_resume.py -q`.
- Unit/integration tests to add: checkpoint file creation, interrupt/resume path, missing run_id, missing checkpoint fallback.
- Risks and mitigations: LangGraph checkpoint API changes; verify official persistence/interrupt docs and pin versions.
- Status: pending.

### M17 ImplementationSubgraph

- Scope: requirement extraction, project impact analysis, implementation plan, code patch generation, patch approval/application, syntax check, implementation report.
- Key files/modules: `codeagent/workflow/subgraphs/implementation.py`, `codeagent/stages/implementation_service.py`.
- Acceptance criteria: on a small fixture, generates plan, patch, changed files, syntax log, implementation report, and stage result.
- Verification commands: `python -m pytest tests/integration/test_implementation_stage.py -q`.
- Unit/integration tests to add: plan schema, patch loop on validation failure, syntax-check failure path, cancelled approval path.
- Risks and mitigations: LLM patch quality; use schema validation, targeted context, and small fixtures before benchmark.
- Status: pending.

### M18 TestingSubgraph

- Scope: test target analysis, test plan generation/review, test patch generation/review, command approval, execution, result parsing, report.
- Key files/modules: `codeagent/workflow/subgraphs/testing.py`, `codeagent/stages/testing_service.py`.
- Acceptance criteria: fixture project can approve test plan, apply test patch, run configured command, parse result, and route correctly.
- Verification commands: `python -m pytest tests/integration/test_testing_stage.py -q`.
- Unit/integration tests to add: test-plan review decisions, test patch restrictions, command edit/reject, pass/fail result routing.
- Risks and mitigations: hidden tests leakage in benchmark mode; expose only visible paths and deny reads of `evaluation` or `oracle_tests`.
- Status: pending.

### M19 DebuggingSubgraph

- Scope: collect logs, reproduce when command is available, summarize failures, search source, fault localization, root cause, repair plan, debug report.
- Key files/modules: `codeagent/workflow/subgraphs/debugging.py`, `codeagent/stages/debugging_service.py`.
- Acceptance criteria: QuixBugs-like fixture produces failure summary, ranked suspects with evidence, root cause, repair plan, and stage result.
- Verification commands: `python -m pytest tests/integration/test_debugging_stage.py -q`.
- Unit/integration tests to add: reproduction approved/rejected, static-log fallback, fault-localization schema, low-confidence reporting.
- Risks and mitigations: speculative root cause; require tool evidence in structured output.
- Status: pending.

### M20 RepairSubgraph and Multi-Round Repair Loop

- Scope: final repair plan, repair patch, risk check, approval/application, regression command, result parsing, loop back to debugging on failure.
- Key files/modules: `codeagent/workflow/subgraphs/repair.py`, `codeagent/stages/repair_service.py`, `codeagent/tools/risk_checker.py`.
- Acceptance criteria: buggy fixture is repaired and verified; repeated failure stops at `max_repair_attempts` with clear failure report.
- Verification commands: `python -m pytest tests/integration/test_repair_stage.py -q`.
- Unit/integration tests to add: risk checker, repair success, repair failure loop, max-attempt final failure.
- Risks and mitigations: overfitting patch; deny test deletion/skip/hardcoding and record risk decisions.
- Status: pending.

### M21 Wizard, Streaming Progress, and Approval UI

- Scope: implement semi-interactive wizard, Rich progress rendering, approval prompts, streaming event display, cancellation handling.
- Key files/modules: `codeagent/cli/wizard.py`, `approval_console.py`, `progress.py`.
- Acceptance criteria: user can configure a task, review summary, approve/edit/reject/cancel approvals, and see stage/tool/test progress.
- Verification commands: `python -m pytest tests/integration/test_cli_wizard.py -q`.
- Unit/integration tests to add: scripted wizard input, approval decisions, cancellation final report.
- Risks and mitigations: flaky interactive tests; test controller logic separately from terminal rendering.
- Status: pending.

### M22 Non-Interactive Run and Stage Subcommands

- Scope: wire `run --config`, `implement`, `test`, `debug`, and `repair` to normalized `TaskConfig` and graph execution.
- Key files/modules: `codeagent/cli/app.py`, `codeagent/config/cli_mapping.py`.
- Acceptance criteria: config mode and each stage command create run dirs, run legal stages, and reject illegal inputs.
- Verification commands: `codeagent run --config examples/task.yaml`; `python -m pytest tests/integration/test_cli_run.py -q`.
- Unit/integration tests to add: run config, stage subcommand mapping, invalid path, invalid stage order.
- Risks and mitigations: command/config divergence; use one loader normalization path for all commands.
- Status: pending.

### M23 BenchmarkRunner, CaseLoader, Evaluator, and Aggregator

- Scope: load benchmark configs, copy each original case into a clean isolated run workspace, replace `{{CASE_DIR}}` command placeholders with the copied case directory, enforce visible/hidden paths, run workflow in benchmark mode, evaluate criteria, aggregate metrics.
- Key files/modules: `codeagent/benchmark/runner.py`, `case_loader.py`, `evaluator.py`, `metrics.py`, `report.py`.
- Acceptance criteria: enabled cases run only in clean copied case directories, original benchmark cases remain unchanged and reusable, `{{CASE_DIR}}` in case commands resolves to the run copy, auto-approvals are logged, result JSON/Markdown reports are generated.
- Verification commands: `codeagent benchmark --config benchmark/benchmark.yaml`; `python -m pytest tests/integration/test_benchmark_runner.py -q`.
- Unit/integration tests to add: case loading, hidden path enforcement, auto-approval trace, artifact-required evaluator, failure aggregation.
- Risks and mitigations: original benchmark pollution; always copy the entire case to a clean temp/run dir, run Agent/test commands against that copy, and never edit source benchmark directories.
- Status: pending.

### M24 Public Benchmark Pass: HumanEval, MBPP, QuixBugs

- Scope: run 2 HumanEval cases, 2 MBPP cases, and 2 QuixBugs cases from `benchmark/benchmark.yaml`; iterate on CodeAgent failures.
- Key files/modules: benchmark configs plus all implementation/runtime modules.
- Acceptance criteria: enabled public cases report success or clear failure categories with logs; target is all enabled public cases passing.
- Verification commands: `codeagent benchmark --config benchmark/benchmark.yaml`.
- Unit/integration tests to add: regression tests for any failure discovered in public benchmark.
- Risks and mitigations: model variability and hidden-answer leakage; use deterministic prompts, no golden answers, repeated isolated runs.
- Status: pending.

### M25 BugsInPy Optional Path and Environment Detection

- Scope: detect WSL/conda/official BugsInPy readiness, run or clearly block `bugsinpy_black_001`, and document blockers.
- Key files/modules: `scripts/*bugsinpy*.ps1`, `codeagent/benchmark/environment.py`, BugsInPy case config.
- Acceptance criteria: if environment exists, run official prepare/test wrapper; if missing, benchmark report records blocker without silent skip.
- Verification commands: copy `benchmark/cases/bugsinpy_black_001` to a clean `<copied_case_dir>`, then run `powershell -ExecutionPolicy Bypass -File scripts/run_bugsinpy_wsl_conda.ps1 -CaseDir <copied_case_dir> -AllowTestFailure`.
- Unit/integration tests to add: environment detection unit tests and disabled-case reporting.
- Risks and mitigations: Windows/WSL filesystem and Python 3.8.3 complexity; keep optional, explicit, and well documented.
- Status: pending.

### M26 Self-Built Benchmark Pass and Final Developer Docs

- Scope: run all 5 self-built cases, iterate failures, finalize README, developer reports, benchmark report, and demonstration notes.
- Key files/modules: `benchmark/selfbuilt/**`, `README.md`, `docs/dev_reports/`, benchmark reports.
- Acceptance criteria: each self-built case has isolated run output, success/failure reason, logs, patches, and aggregate report; README documents installation, API key, CLI, resume, and benchmark.
- Verification commands: `codeagent benchmark --config benchmark/selfbuilt/selfbuilt_benchmark.yaml`; `python -m pytest -q`; `codeagent --help`.
- Unit/integration tests to add: regression tests based on self-built failures and README command smoke tests.
- Risks and mitigations: large scope and external dependencies in Flask case; run easier CLI cases first, install generated dependencies only in isolated benchmark env.
- Status: pending.

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
  - `01_todo_manager`: CLI + JSON persistence.
  - `02_personal_ledger`: CLI + JSON + CSV export.
  - `03_student_gradebook`: CLI + CSV import/export and grade stats.
  - `04_library_lending`: CLI + SQLite lending workflow.
  - `05_meeting_room_booking`: Flask API + SQLite; requires generated Flask dependency.
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
