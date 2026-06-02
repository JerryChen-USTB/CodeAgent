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

`workspace/` should remain empty in the original benchmark copy. A runner should
copy each case to a temporary run directory before letting the agent write code.

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
implements each project:

```powershell
cd benchmark\selfbuilt\cases\01_todo_manager
python -m unittest discover -s oracle_tests
```

The expected initial failure is a missing entry module or missing package. After
the agent implements the case, the same command is the final verification step.
