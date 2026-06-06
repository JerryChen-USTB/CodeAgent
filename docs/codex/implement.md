Now implement the entire CodeAgent project end-to-end.

The user has reviewed and approved `plans.md`. Treat it as the source of truth and begin implementation immediately. Do not stop after a milestone to ask for permission. Continue through every milestone until the project is complete, validated, documented, and benchmarked.

## Non-negotiable constraints

- Follow `plans.md` strictly. If reality differs from the plan, make a reasonable engineering decision, record the decision in `plans.md`, and keep moving.
- Do not leak secrets. Never print, commit, log, copy, or summarize the contents of `Software Engineering Project.txt`, `.env`, API keys, tokens, certificates, or private credentials.
- The product must remain a CLI-based LangGraph + LangChain software-engineering agent for the workflow `implement -> test -> debug -> repair`.
- Do not expose benchmark hidden evaluation material, `oracle_tests`, `evaluation`, or expected answers to the evaluated Agent.
- Do not run benchmark cases against the original case directories. Treat `benchmark/cases/**` and `benchmark/selfbuilt/cases/**` as reusable read-only templates; copy each case to a clean per-run workspace before Agent execution, patching, dependency installation, testing, logging, or evaluation.
- Do not fake completion. Do not claim that code, tests, benchmarks, or docs are done unless the relevant command was actually run and the result was recorded.

## Execution rules

- Start from the first unfinished milestone in `plans.md`; M01 is planning, so begin with the safety/preflight work if it is still pending.
- Implement deliberately in small, reviewable changes. Avoid bundling unrelated features.
- Keep `plans.md` current:
  - update milestone status as `in-progress`, `done`, or `blocked`;
  - append concise running notes after each meaningful module or milestone;
  - record commands run, results, failures, fixes, and deviations from the original plan.
- If a bug is found:
  - first add or update a failing test that reproduces it;
  - fix the bug;
  - rerun the relevant tests;
  - record the fix in `plans.md`.
- If a document must change to match implementation reality, back it up first under `docs/_backups/<timestamp>/...`, update its version/change log, and explain why the change was necessary.

## Testing and validation rules

After every small module:

- add unit tests for the core behavior;
- run the narrowest relevant test command;
- fix all failures before moving on.

After every milestone:

- run that milestone’s verification commands from `plans.md`;
- run broader tests when the milestone affects shared infrastructure;
- update `plans.md` with exact commands and results;
- do not leave known P0/P1 failures unresolved.

Maintain these baseline checks as soon as the project supports them:

```bash
python -m pytest -q
python -m pytest tests/unit -q
python -m pytest tests/integration -q
python -m codeagent --help
codeagent --help
```

When CLI examples and benchmarks become available, also maintain:

```bash
codeagent run --config examples/task.yaml
codeagent benchmark --config benchmark/benchmark.yaml
codeagent benchmark --config benchmark/selfbuilt/selfbuilt_benchmark.yaml
```

If lint/type checking tools are added, keep them passing too.

## Prompt engineering requirements

When implementing Agent nodes, do not write shallow placeholder prompts. Design detailed, role-specific, maintainable prompts for Planner, Coder, TestDesigner, TestWriter, Debugger, Repairer, Verifier, and Benchmark-related agents.

Each prompt must clearly state the role, inputs, allowed tools, output schema, patch-first rules, hidden-test isolation rules, security rules, verification expectations, and failure behavior. Prompts should be detailed enough to guide reliable behavior, but should avoid duplicating entire SRS/design documents verbatim.

Add prompt snapshot/schema tests where practical so prompt regressions are visible.

## Documentation requirements

Create and maintain developer-friendly documentation as implementation progresses.

After each completed milestone, write or update a clear report under:

```text
docs/dev_reports/Mxx_<milestone_name>.md
```

Each milestone report should be concise, readable, and useful to a future developer. Include:

- goal of the milestone;
- main files/modules changed;
- key design decisions;
- how to run or use the new capability;
- tests and verification commands run;
- known limitations or follow-up work;
- links/paths to important artifacts.

Also keep README accurate. By the end, README must explain:

- what CodeAgent is;
- setup and dependency installation;
- OpenRouter API key configuration without exposing secrets;
- CLI commands and examples;
- output directory structure;
- checkpoint/resume behavior;
- benchmark execution;
- troubleshooting.

## Benchmark execution order

Once the system can run end-to-end, benchmark in this order and iterate until all feasible cases pass:

1. HumanEval / MBPP cases.
2. QuixBugs cases.
3. BugsInPy case or explicit environment-blocker detection/reporting.
4. All five self-built benchmark cases.

For every benchmark failure, inspect logs, improve CodeAgent itself, add regression tests when possible, rerun, and update `plans.md` plus benchmark reports. Never hardcode benchmark answers or weaken tests.

## Completion criteria

Do not stop until all of the following are true, or until an external environment blocker is clearly documented:

- All milestones in `plans.md` are implemented, checked off, or explicitly blocked with evidence.
- `python -m pytest -q` passes.
- `codeagent --help` and `python -m codeagent --help` work.
- At least one example task can run and produce a complete `codeagent_runs/<run_id>/` directory.
- Implementation, testing, debugging, and repair stage flows are wired through LangGraph.
- HITL approval points, patch-first behavior, decision trace, transcript, reports, and artifact index are implemented.
- SQLite checkpoint/resume works at least for interrupt approval points, or failure is clearly documented with fallback behavior.
- OpenRouter model configuration for the temporary cost-control model `anthropic/claude-sonnet-4.6` is implemented securely.
- Public benchmark reporting works.
- Self-built benchmark reporting works.
- README and `docs/dev_reports/` are accurate and developer-friendly.
- No API key, local secret, hidden oracle content, or generated bulky run artifact is committed.

Start now by reading `plans.md`, updating its status for the first unfinished milestone, and implementing continuously until the project is complete.

## Implementation Alignment Change Log

| Date | Change | Reason | Impact |
|---|---|---|---|
| 2026-06-03 | Added benchmark original-case protection and clean per-run workspace rule. | Prevent benchmark runs from polluting reusable source cases. | Benchmark implementation must copy cases before execution; no scope reduction. |
