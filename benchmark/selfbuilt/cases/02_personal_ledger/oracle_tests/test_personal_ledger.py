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


def run_ledger_session(script: str, store: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKSPACE)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        PYTHON_CMD + ["-m", "personal_ledger", "--file", str(store)],
        cwd=WORKSPACE,
        env=env,
        input=script,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


class PersonalLedgerTuiOracleTests(unittest.TestCase):
    def test_tui_lifecycle_add_list_summary_delete_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "ledger.json"

            session = run_ledger_session(
                "\n".join(
                    [
                        "1",
                        "2026-06-01",
                        "income",
                        "salary",
                        "5000",
                        "monthly salary",
                        "1",
                        "2026-06-02",
                        "expense",
                        "food",
                        "23.5",
                        "lunch",
                        "1",
                        "2026-06-03",
                        "expense",
                        "transport",
                        "6.75",
                        "bus",
                        "1",
                        "2026-05-20",
                        "expense",
                        "food",
                        "10.00",
                        "snack",
                        "2",
                        "2026-06",
                        "",
                        "",
                        "3",
                        "2026-06",
                        "5",
                        "3",
                        "y",
                        "2",
                        "",
                        "",
                        "",
                        "6",
                        "",
                    ]
                ),
                store,
            )

            self.assertEqual(session.returncode, 0, session.stderr)
            self.assertIn("个人记账系统", session.stdout)
            for label in ["新增账目", "查询流水", "统计汇总", "编辑账目", "删除账目", "保存并退出"]:
                self.assertIn(label, session.stdout)

            self.assertIn("已新增账目 #1: income salary 5000.00", session.stdout)
            self.assertIn("已新增账目 #2: expense food 23.50", session.stdout)
            self.assertIn("已新增账目 #3: expense transport 6.75", session.stdout)
            self.assertIn("#1 2026-06-01 income salary 5000.00 monthly salary", session.stdout)
            self.assertIn("#2 2026-06-02 expense food 23.50 lunch", session.stdout)
            self.assertIn("#3 2026-06-03 expense transport 6.75 bus", session.stdout)
            self.assertIn("收入合计: 5000.00", session.stdout)
            self.assertIn("支出合计: 30.25", session.stdout)
            self.assertIn("余额: 4969.75", session.stdout)
            self.assertIn("food: 23.50", session.stdout)
            self.assertIn("transport: 6.75", session.stdout)
            self.assertIn("已删除账目 #3: expense transport 6.75", session.stdout)

            data = json.loads(store.read_text(encoding="utf-8"))
            self.assertEqual([record["id"] for record in data], [1, 2, 4])
            self.assertEqual(data[0]["amount"], "5000.00")
            self.assertEqual(data[1]["amount"], "23.50")
            self.assertTrue(all(isinstance(record["amount"], str) for record in data))
            self.assertNotIn(3, {record["id"] for record in data})

            reopen = run_ledger_session("2\n2026-06\n\n\n6\n", store)
            self.assertEqual(reopen.returncode, 0, reopen.stderr)
            self.assertIn("#1 2026-06-01 income salary 5000.00 monthly salary", reopen.stdout)
            self.assertIn("#2 2026-06-02 expense food 23.50 lunch", reopen.stdout)
            self.assertNotIn("#3 2026-06-03", reopen.stdout)
            self.assertNotIn("2026-05-20", reopen.stdout)

    def test_edit_record_and_persist_updated_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "ledger.json"

            session = run_ledger_session(
                "\n".join(
                    [
                        "1",
                        "2026-06-02",
                        "expense",
                        "food",
                        "12",
                        "lunch",
                        "4",
                        "1",
                        "",
                        "",
                        "groceries",
                        "88.8",
                        "weekly groceries",
                        "2",
                        "",
                        "",
                        "",
                        "6",
                        "",
                    ]
                ),
                store,
            )

            self.assertEqual(session.returncode, 0, session.stderr)
            self.assertIn("已更新账目 #1: expense groceries 88.80", session.stdout)
            self.assertIn("#1 2026-06-02 expense groceries 88.80 weekly groceries", session.stdout)

            data = json.loads(store.read_text(encoding="utf-8"))
            self.assertEqual(data[0]["category"], "groceries")
            self.assertEqual(data[0]["amount"], "88.80")
            self.assertEqual(data[0]["note"], "weekly groceries")

            reopen = run_ledger_session("2\n\n\n\n6\n", store)
            self.assertEqual(reopen.returncode, 0, reopen.stderr)
            self.assertIn("#1 2026-06-02 expense groceries 88.80 weekly groceries", reopen.stdout)

    def test_validation_errors_recover_to_menu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "ledger.json"

            session = run_ledger_session(
                "\n".join(
                    [
                        "2",
                        "2026-06",
                        "",
                        "",
                        "1",
                        "2026/06/01",
                        "expense",
                        "food",
                        "1",
                        "bad date",
                        "1",
                        "2026-06-01",
                        "bonus",
                        "food",
                        "1",
                        "bad type",
                        "1",
                        "2026-06-01",
                        "expense",
                        "food",
                        "0",
                        "zero amount",
                        "1",
                        "",
                        "expense",
                        "food",
                        "1",
                        "missing date",
                        "1",
                        "2026-06-01",
                        "expense",
                        "",
                        "1",
                        "missing category",
                        "3",
                        "2026-6",
                        "4",
                        "99",
                        "5",
                        "99",
                        "y",
                        "9",
                        "6",
                        "",
                    ]
                ),
                store,
            )

            self.assertEqual(session.returncode, 0, session.stderr)
            self.assertIn("暂无账目", session.stdout)
            self.assertIn("invalid date", session.stdout)
            self.assertIn("invalid type", session.stdout)
            self.assertIn("invalid amount", session.stdout)
            self.assertIn("required field missing", session.stdout)
            self.assertIn("invalid month", session.stdout)
            self.assertIn("record not found", session.stdout)
            self.assertIn("unknown option", session.stdout)
            if store.exists():
                self.assertEqual(json.loads(store.read_text(encoding="utf-8")), [])

    def test_invalid_ledger_file_fails_before_menu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "ledger.json"
            store.write_text("{not-json", encoding="utf-8")

            session = run_ledger_session("6\n", store)

            self.assertNotEqual(session.returncode, 0)
            self.assertIn("invalid ledger file", session.stderr)
            self.assertNotIn("个人记账系统", session.stdout)
            self.assertEqual(store.read_text(encoding="utf-8"), "{not-json")


if __name__ == "__main__":
    unittest.main()
