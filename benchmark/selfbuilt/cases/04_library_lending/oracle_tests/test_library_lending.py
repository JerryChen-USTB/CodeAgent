from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
PYTHON_CMD = [shutil.which("py"), "-3"] if os.name == "nt" and shutil.which("py") else [sys.executable]


def run_library(args: list[str], db: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKSPACE)
    return subprocess.run(
        PYTHON_CMD + ["-m", "library_lending", "--db", str(db)] + args,
        cwd=WORKSPACE,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class LibraryLendingOracleTests(unittest.TestCase):
    def test_lending_lifecycle_stock_and_overdue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library.db"
            for args, expected in [
                (["init"], "database ready"),
                (["add-book", "--isbn", "978-1", "--title", "Clean Code", "--author", "Robert Martin", "--copies", "2"], "book 978-1 available copies: 2"),
                (["add-reader", "--reader", "r1", "--name", "Ada"], "reader r1 registered"),
            ]:
                result = run_library(args, db)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

            borrow = run_library(["borrow", "--reader", "r1", "--isbn", "978-1", "--date", "2026-06-01"], db)
            self.assertEqual(borrow.returncode, 0, borrow.stderr)
            self.assertIn("borrowed 978-1 by r1 due 2026-06-15", borrow.stdout)

            books = run_library(["books"], db)
            self.assertEqual(books.returncode, 0, books.stderr)
            self.assertIn("978-1 Clean Code by Robert Martin copies 2 available 1", books.stdout)

            duplicate = run_library(["borrow", "--reader", "r1", "--isbn", "978-1", "--date", "2026-06-02"], db)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("already borrowed", duplicate.stderr)

            overdue = run_library(["overdue", "--date", "2026-06-20"], db)
            self.assertEqual(overdue.returncode, 0, overdue.stderr)
            self.assertIn("r1 978-1 due 2026-06-15", overdue.stdout)

            returned = run_library(["return", "--reader", "r1", "--isbn", "978-1", "--date", "2026-06-05"], db)
            self.assertEqual(returned.returncode, 0, returned.stderr)
            self.assertIn("returned 978-1 by r1", returned.stdout)

            no_overdue = run_library(["overdue", "--date", "2026-06-20"], db)
            self.assertEqual(no_overdue.returncode, 0, no_overdue.stderr)
            self.assertEqual(no_overdue.stdout.strip(), "no overdue loans")

    def test_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library.db"
            self.assertEqual(run_library(["init"], db).returncode, 0)

            bad_copies = run_library(["add-book", "--isbn", "x", "--title", "Bad", "--author", "Nobody", "--copies", "0"], db)
            self.assertNotEqual(bad_copies.returncode, 0)
            self.assertIn("invalid copies", bad_copies.stderr)

            missing_reader = run_library(["borrow", "--reader", "missing", "--isbn", "x", "--date", "2026-06-01"], db)
            self.assertNotEqual(missing_reader.returncode, 0)
            self.assertIn("reader not found", missing_reader.stderr)

            bad_date = run_library(["overdue", "--date", "2026/06/20"], db)
            self.assertNotEqual(bad_date.returncode, 0)
            self.assertIn("invalid date", bad_date.stderr)


if __name__ == "__main__":
    unittest.main()
