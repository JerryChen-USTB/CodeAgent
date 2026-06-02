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


def run_todo(args: list[str], store: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKSPACE)
    return subprocess.run(
        PYTHON_CMD + ["-m", "todo_manager", "--file", str(store)] + args,
        cwd=WORKSPACE,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class TodoManagerOracleTests(unittest.TestCase):
    def test_full_task_lifecycle_and_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "tasks.json"

            first = run_todo(["add", "--title", "Write report", "--due", "2026-06-10", "--priority", "high"], store)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("created task #1: Write report", first.stdout)

            second = run_todo(["add", "--title", "Buy milk"], store)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("created task #2: Buy milk", second.stdout)

            listed = run_todo(["list", "--status", "open"], store)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn("#1 [open] high Write report due 2026-06-10", listed.stdout)
            self.assertIn("#2 [open] normal Buy milk due none", listed.stdout)

            done = run_todo(["done", "1"], store)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("completed task #1: Write report", done.stdout)

            done_list = run_todo(["list", "--status", "done"], store)
            self.assertEqual(done_list.returncode, 0, done_list.stderr)
            self.assertIn("#1 [done] high Write report due 2026-06-10", done_list.stdout)
            self.assertNotIn("#2", done_list.stdout)

            deleted = run_todo(["delete", "2"], store)
            self.assertEqual(deleted.returncode, 0, deleted.stderr)
            self.assertIn("deleted task #2: Buy milk", deleted.stdout)

            data = json.loads(store.read_text(encoding="utf-8"))
            self.assertEqual(data, [{"id": 1, "title": "Write report", "status": "done", "priority": "high", "due": "2026-06-10"}])

    def test_empty_list_and_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "tasks.json"

            empty = run_todo(["list"], store)
            self.assertEqual(empty.returncode, 0, empty.stderr)
            self.assertEqual(empty.stdout.strip(), "no tasks")

            bad_title = run_todo(["add", "--title", "   "], store)
            self.assertNotEqual(bad_title.returncode, 0)
            self.assertIn("title is required", bad_title.stderr)

            bad_date = run_todo(["add", "--title", "Bad date", "--due", "2026/06/10"], store)
            self.assertNotEqual(bad_date.returncode, 0)
            self.assertIn("invalid due date", bad_date.stderr)

            missing = run_todo(["done", "99"], store)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("task not found", missing.stderr)

            store.write_text("{not-json", encoding="utf-8")
            invalid_file = run_todo(["list"], store)
            self.assertNotEqual(invalid_file.returncode, 0)
            self.assertIn("invalid task file", invalid_file.stderr)


if __name__ == "__main__":
    unittest.main()
