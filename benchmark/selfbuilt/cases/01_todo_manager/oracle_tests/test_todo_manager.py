from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
PYTHON_CMD = [shutil.which("py"), "-3"] if os.name == "nt" and shutil.which("py") else [sys.executable]


def run_todo_session(script: str, store: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKSPACE)
    return subprocess.run(
        PYTHON_CMD + ["-m", "todo_manager", "--file", str(store)],
        cwd=WORKSPACE,
        env=env,
        input=script,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


class TodoManagerTuiOracleTests(unittest.TestCase):
    def test_interactive_lifecycle_persistence_and_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "tasks.json"
            session = run_todo_session(
                "\n".join(
                    [
                        "1",
                        "Write report",
                        "2026-06-10",
                        "high",
                        "1",
                        "Buy milk",
                        "",
                        "",
                        "2",
                        "open",
                        "3",
                        "1",
                        "2",
                        "done",
                        "4",
                        "2",
                        "2",
                        "all",
                        "5",
                        "",
                    ]
                ),
                store,
            )

            self.assertEqual(session.returncode, 0, session.stderr)
            self.assertIn("Todo Manager", session.stdout)
            self.assertIn("created task #1: Write report", session.stdout)
            self.assertIn("created task #2: Buy milk", session.stdout)
            self.assertIn("#1 [open] high Write report due 2026-06-10", session.stdout)
            self.assertIn("#2 [open] normal Buy milk due none", session.stdout)
            self.assertIn("completed task #1: Write report", session.stdout)
            self.assertIn("#1 [done] high Write report due 2026-06-10", session.stdout)
            self.assertIn("deleted task #2: Buy milk", session.stdout)

            data = json.loads(store.read_text(encoding="utf-8"))
            self.assertEqual(
                data,
                [
                    {
                        "id": 1,
                        "title": "Write report",
                        "status": "done",
                        "priority": "high",
                        "due": "2026-06-10",
                    }
                ],
            )

            reopen = run_todo_session("2\ndone\n5\n", store)
            self.assertEqual(reopen.returncode, 0, reopen.stderr)
            self.assertIn("#1 [done] high Write report due 2026-06-10", reopen.stdout)

    def test_empty_list_validation_errors_and_session_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "tasks.json"
            session = run_todo_session(
                "\n".join(
                    [
                        "2",
                        "all",
                        "1",
                        "   ",
                        "1",
                        "Bad date",
                        "2026/06/10",
                        "normal",
                        "1",
                        "Bad priority",
                        "",
                        "urgent",
                        "2",
                        "blocked",
                        "9",
                        "3",
                        "99",
                        "4",
                        "99",
                        "5",
                        "",
                    ]
                ),
                store,
            )

            self.assertEqual(session.returncode, 0, session.stderr)
            self.assertIn("no tasks", session.stdout)
            self.assertIn("title is required", session.stdout)
            self.assertIn("invalid due date", session.stdout)
            self.assertIn("invalid priority", session.stdout)
            self.assertIn("invalid status", session.stdout)
            self.assertIn("unknown option", session.stdout)
            self.assertIn("task not found", session.stdout)
            if store.exists():
                self.assertEqual(json.loads(store.read_text(encoding="utf-8")), [])

    def test_invalid_task_file_fails_before_menu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "tasks.json"
            store.write_text("{not-json", encoding="utf-8")

            session = run_todo_session("5\n", store)

            self.assertNotEqual(session.returncode, 0)
            self.assertIn("invalid task file", session.stderr)
            self.assertNotIn("Choose an option", session.stdout)
            self.assertEqual(store.read_text(encoding="utf-8"), "{not-json")


if __name__ == "__main__":
    unittest.main()
