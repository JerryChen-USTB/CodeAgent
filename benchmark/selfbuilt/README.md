# Self-Built Python Benchmark

This directory contains course-specific benchmark cases for the software
engineering agent. The cases are managed separately from public-dataset cases
under `benchmark/cases`.

## Output Directory

Self-built benchmark source material remains in `benchmark/selfbuilt/`. New
self-built benchmark run artifacts are written to the centralized repository
output root:

```text
codeagent_runs/benchmarks/selfbuilt/
```

Older ignored validation artifacts may still exist under
`benchmark/selfbuilt/codeagent_runs/`, but new runs should use the `output_dir`
declared in `selfbuilt_benchmark.yaml` and `meeting_room_demo_benchmark.yaml`.

## Design Principles

- Each case starts from an empty `workspace/`.
- The agent must implement the project from the input materials.
- The `input/` directory is the primary task context. Each current self-built
  case keeps exactly four simplified-Chinese materials: PRD, user stories,
  design model, and acceptance criteria.
- The `oracle_tests/` directory is hidden from the agent and is used only by
  the benchmark runner.
- No case uses `expected_result.json`; success criteria are declared in
  `case.yaml` and verified by hidden tests.

## Layout

```text
benchmark/selfbuilt/
  README.md
  selfbuilt_benchmark.yaml
  cases/
    01_todo_manager/
    02_personal_ledger/
    03_student_gradebook/
    04_library_lending/
    05_meeting_room_booking/
```

Each current case uses:

```text
case/
  case.yaml
  input/
    PRD.md
    user_stories.md
    design_model.md
    acceptance_criteria.md
  workspace/
  oracle_tests/
```

`workspace/` should remain empty in the original benchmark copy. A runner must
copy the entire case to a clean temporary run directory before letting the agent
write code or before running oracle tests. The agent and test command operate
only on the copied workspace; the original case remains reusable for later
benchmark runs.

The first three cases require simple line-oriented TUI interaction that hidden
oracle tests can drive through stdin/stdout. The fourth case requires a local
browser-accessible Web UI implemented with the Python standard library. The
fifth case is the Flask upgrade: it must provide both a browser Web UI and a
stable JSON API.

## Case Summary

| Case | Difficulty | Type | Persistence |
| --- | --- | --- | --- |
| `01_todo_manager` | Introductory | TUI | JSON |
| `02_personal_ledger` | Easy | TUI | JSON |
| `03_student_gradebook` | Medium | TUI | JSON |
| `04_library_lending` | Medium-high | Standard-library Web UI | SQLite |
| `05_meeting_room_booking` | High | Flask Web UI + JSON API | SQLite |

## Manual Initial Check

The initial workspaces are empty, so oracle tests should fail before the agent
implements each project. Run this check on a copied case, not on the reusable
original benchmark case:

```powershell
cd <copied_case_dir>
python -m unittest discover -s oracle_tests
```

Only the benchmark runner/evaluator should run this command, and only inside the
copied case directory. The expected initial failure is a missing entry module or
missing package. After the agent implements the case in the copied workspace, the
same command is the final verification step.

## Change Log

| Date | Change | Reason |
|---|---|---|
| 2026-06-03 | Strengthened copy-to-clean-run-directory rule for self-built cases. | Keep original empty workspaces and hidden oracle tests reusable across repeated benchmark runs. |
| 2026-06-05 | Upgraded `01_todo_manager` to four richer Chinese materials and TUI-based hidden oracle tests. | Make the first self-built case require realistic interactive software behavior instead of one-command-at-a-time usage. |
| 2026-06-06 | Synchronized all five cases to four Chinese input materials; upgraded cases 02 and 03 to TUI, case 04 to standard-library Web UI, and case 05 to Flask Web UI + JSON API. | Keep the benchmark documentation aligned with the current case materials, case configs, and hidden oracle expectations. |
