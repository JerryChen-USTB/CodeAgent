from __future__ import annotations

import csv
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


def run_ledger(args: list[str], store: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKSPACE)
    return subprocess.run(
        PYTHON_CMD + ["-m", "personal_ledger", "--file", str(store)] + args,
        cwd=WORKSPACE,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class PersonalLedgerOracleTests(unittest.TestCase):
    def test_records_summary_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "ledger.json"
            output = Path(tmp) / "ledger.csv"

            commands = [
                ["add", "--date", "2026-06-01", "--type", "income", "--category", "salary", "--amount", "5000", "--note", "monthly"],
                ["add", "--date", "2026-06-02", "--type", "expense", "--category", "food", "--amount", "23.5", "--note", "lunch"],
                ["add", "--date", "2026-06-03", "--type", "expense", "--category", "transport", "--amount", "6.75"],
                ["add", "--date", "2026-05-20", "--type", "expense", "--category", "food", "--amount", "10.00"],
            ]
            for index, command in enumerate(commands, start=1):
                result = run_ledger(command, store)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"added record #{index}", result.stdout)

            june = run_ledger(["list", "--month", "2026-06"], store)
            self.assertEqual(june.returncode, 0, june.stderr)
            self.assertIn("#1 2026-06-01 income salary 5000.00 monthly", june.stdout)
            self.assertIn("#2 2026-06-02 expense food 23.50 lunch", june.stdout)
            self.assertNotIn("2026-05-20", june.stdout)

            summary = run_ledger(["summary", "--month", "2026-06"], store)
            self.assertEqual(summary.returncode, 0, summary.stderr)
            self.assertIn("income: 5000.00", summary.stdout)
            self.assertIn("expense: 30.25", summary.stdout)
            self.assertIn("balance: 4969.75", summary.stdout)
            self.assertIn("food: 23.50", summary.stdout)
            self.assertIn("transport: 6.75", summary.stdout)

            export = run_ledger(["export", "--output", str(output)], store)
            self.assertEqual(export.returncode, 0, export.stderr)
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0].keys(), {"id", "date", "type", "category", "amount", "note"})
            self.assertEqual(rows[1]["amount"], "23.50")

            data = json.loads(store.read_text(encoding="utf-8"))
            self.assertEqual(data[0]["amount"], "5000.00")

    def test_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "ledger.json"
            cases = [
                (["add", "--date", "2026/06/01", "--type", "expense", "--category", "food", "--amount", "1"], "invalid date"),
                (["add", "--date", "2026-06-01", "--type", "expense", "--category", "food", "--amount", "0"], "invalid amount"),
                (["add", "--date", "2026-06-01", "--type", "expense", "--category", "   ", "--amount", "1"], "category is required"),
                (["summary", "--month", "2026-6"], "invalid month"),
            ]
            for args, expected_error in cases:
                result = run_ledger(args, store)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)

            store.write_text("{not-json", encoding="utf-8")
            invalid_file = run_ledger(["list"], store)
            self.assertNotEqual(invalid_file.returncode, 0)
            self.assertIn("invalid ledger file", invalid_file.stderr)


if __name__ == "__main__":
    unittest.main()
