# Self-Built Python Benchmark

This directory contains course-specific benchmark cases for the software
engineering agent. The cases are managed separately from public-dataset cases
under `benchmark/cases`.

## Design Principles

- Each case starts from an empty `workspace/`.
- The agent must implement the project from the input materials.
- The `input/` directory is the primary task context and contains natural
  requirements, PRD, user stories, design models, and acceptance criteria.
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

Each case uses:

```text
case/
  case.yaml
  input/
    requirements.md
    prd.md
    user_stories.yaml
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

## Case Summary

| Case | Difficulty | Type | Persistence |
| --- | --- | --- | --- |
| `01_todo_manager` | Introductory | CLI | JSON |
| `02_personal_ledger` | Easy | CLI | JSON, CSV export |
| `03_student_gradebook` | Medium | CLI | CSV import and report export |
| `04_library_lending` | Medium-high | CLI | SQLite |
| `05_meeting_room_booking` | High | Flask API | SQLite |

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
