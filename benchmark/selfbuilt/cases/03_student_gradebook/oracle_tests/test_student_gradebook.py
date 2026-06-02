from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
PYTHON_CMD = [shutil.which("py"), "-3"] if os.name == "nt" and shutil.which("py") else [sys.executable]


def run_gradebook(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKSPACE)
    return subprocess.run(
        PYTHON_CMD + ["-m", "student_gradebook"] + args,
        cwd=WORKSPACE,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class StudentGradebookOracleTests(unittest.TestCase):
    def write_scores(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["student_id", "name", "homework", "midterm", "final"])
            writer.writeheader()
            writer.writerows(rows)

    def test_report_and_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scores = Path(tmp) / "scores.csv"
            report = Path(tmp) / "report.csv"
            self.write_scores(
                scores,
                [
                    {"student_id": "s1", "name": "Ada", "homework": "100", "midterm": "90", "final": "95"},
                    {"student_id": "s2", "name": "Bob", "homework": "70", "midterm": "70", "final": "70"},
                    {"student_id": "s3", "name": "Chen", "homework": "80", "midterm": "80", "final": "80"},
                ],
            )

            generated = run_gradebook(["report", "--input", str(scores), "--output", str(report)])
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertIn("generated report for 3 students", generated.stdout)

            with report.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["total"], "95.00")
            self.assertEqual(rows[0]["letter"], "A")
            self.assertEqual(rows[1]["total"], "70.00")
            self.assertEqual(rows[1]["letter"], "C")
            self.assertEqual(rows[2]["letter"], "B")

            stats = run_gradebook(["stats", "--input", str(scores)])
            self.assertEqual(stats.returncode, 0, stats.stderr)
            self.assertIn("students: 3", stats.stdout)
            self.assertIn("average_total: 81.67", stats.stdout)
            self.assertIn("highest: s1 Ada 95.00", stats.stdout)
            self.assertIn("lowest: s2 Bob 70.00", stats.stdout)
            self.assertIn("A: 1", stats.stdout)
            self.assertIn("B: 1", stats.stdout)
            self.assertIn("C: 1", stats.stdout)

    def test_invalid_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.csv"
            missing.write_text("student_id,name,homework,midterm\ns1,Ada,90,80\n", encoding="utf-8")
            result = run_gradebook(["stats", "--input", str(missing)])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing column", result.stderr)

            duplicate = Path(tmp) / "duplicate.csv"
            self.write_scores(
                duplicate,
                [
                    {"student_id": "s1", "name": "Ada", "homework": "90", "midterm": "90", "final": "90"},
                    {"student_id": "s1", "name": "Bob", "homework": "80", "midterm": "80", "final": "80"},
                ],
            )
            result = run_gradebook(["stats", "--input", str(duplicate)])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate student_id", result.stderr)

            out_of_range = Path(tmp) / "range.csv"
            self.write_scores(out_of_range, [{"student_id": "s2", "name": "Bob", "homework": "101", "midterm": "80", "final": "80"}])
            result = run_gradebook(["stats", "--input", str(out_of_range)])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("score out of range", result.stderr)


if __name__ == "__main__":
    unittest.main()
