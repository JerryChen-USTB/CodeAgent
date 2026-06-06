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


def run_gradebook_session(script: str, store: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKSPACE)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        PYTHON_CMD + ["-m", "student_gradebook", "--file", str(store)],
        cwd=WORKSPACE,
        env=env,
        input=script,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=25,
    )


class StudentGradebookTuiOracleTests(unittest.TestCase):
    def test_interactive_lifecycle_statistics_rankings_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "gradebook.json"
            session = run_gradebook_session(
                "\n".join(
                    [
                        "1",
                        "S001",
                        "Ada",
                        "Class 1",
                        "1",
                        "S002",
                        "Bob",
                        "Class 1",
                        "2",
                        "CS101",
                        "Python",
                        "3",
                        "2",
                        "MATH",
                        "Math",
                        "4",
                        "3",
                        "S001",
                        "CS101",
                        "95",
                        "3",
                        "S002",
                        "CS101",
                        "75",
                        "3",
                        "S001",
                        "MATH",
                        "88",
                        "4",
                        "S002",
                        "CS101",
                        "80",
                        "5",
                        "S001",
                        "MATH",
                        "6",
                        "S001",
                        "7",
                        "CS101",
                        "8",
                        "1",
                        "CS101",
                        "9",
                        "1",
                        "CS101",
                        "9",
                        "2",
                        "10",
                        "0",
                        "",
                    ]
                ),
                store,
            )

            self.assertEqual(session.returncode, 0, session.stderr)
            self.assertIn("学生成绩管理系统", session.stdout)
            self.assertIn("已新增学生 S001 Ada", session.stdout)
            self.assertIn("已新增学生 S002 Bob", session.stdout)
            self.assertIn("已新增课程 CS101 Python", session.stdout)
            self.assertIn("已新增课程 MATH Math", session.stdout)
            self.assertIn("已录入成绩 S001 CS101 95.00", session.stdout)
            self.assertIn("已录入成绩 S002 CS101 75.00", session.stdout)
            self.assertIn("已录入成绩 S001 MATH 88.00", session.stdout)
            self.assertIn("已修改成绩 S002 CS101 80.00", session.stdout)
            self.assertIn("已删除成绩 S001 MATH", session.stdout)
            self.assertIn("学生 S001 Ada Class 1", session.stdout)
            self.assertIn("CS101 Python 95.00", session.stdout)
            self.assertIn("学生平均分: 95.00", session.stdout)
            self.assertIn("课程 CS101 Python", session.stdout)
            self.assertIn("S001 Ada 95.00", session.stdout)
            self.assertIn("S002 Bob 80.00", session.stdout)
            self.assertIn("成绩人数: 2", session.stdout)
            self.assertIn("课程平均分: 87.50", session.stdout)
            self.assertIn("最高分: S001 Ada 95.00", session.stdout)
            self.assertIn("最低分: S002 Bob 80.00", session.stdout)
            self.assertIn("#1 S001 Ada 95.00", session.stdout)
            self.assertIn("#2 S002 Bob 80.00", session.stdout)
            self.assertIn("#1 S001 Ada 总分 95.00 平均分 95.00", session.stdout)
            self.assertIn("#2 S002 Bob 总分 80.00 平均分 80.00", session.stdout)
            self.assertIn("保存成功", session.stdout)

            data = json.loads(store.read_text(encoding="utf-8"))
            students = {item["student_id"]: item for item in data.get("students", [])}
            courses = {item["course_code"]: item for item in data.get("courses", [])}
            grades = {
                (item["student_id"], item["course_code"]): item
                for item in data.get("grades", [])
            }

            self.assertEqual(students["S001"]["name"], "Ada")
            self.assertEqual(students["S002"]["class_name"], "Class 1")
            self.assertEqual(courses["CS101"]["name"], "Python")
            self.assertEqual(courses["MATH"]["name"], "Math")
            self.assertEqual(set(grades), {("S001", "CS101"), ("S002", "CS101")})
            self.assertAlmostEqual(float(grades[("S001", "CS101")]["score"]), 95.0)
            self.assertAlmostEqual(float(grades[("S002", "CS101")]["score"]), 80.0)

            reopen = run_gradebook_session("6\nS001\n7\nCS101\n9\n2\n0\n", store)
            self.assertEqual(reopen.returncode, 0, reopen.stderr)
            self.assertIn("学生 S001 Ada Class 1", reopen.stdout)
            self.assertIn("学生平均分: 95.00", reopen.stdout)
            self.assertIn("课程平均分: 87.50", reopen.stdout)
            self.assertIn("#1 S001 Ada 总分 95.00 平均分 95.00", reopen.stdout)

    def test_validation_errors_recover_and_keep_valid_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "gradebook.json"
            session = run_gradebook_session(
                "\n".join(
                    [
                        "1",
                        "   ",
                        "No Name",
                        "Class 1",
                        "1",
                        "S001",
                        "Ada",
                        "Class 1",
                        "1",
                        "S001",
                        "Duplicate",
                        "Class 2",
                        "2",
                        "   ",
                        "No Course",
                        "3",
                        "2",
                        "CS101",
                        "Python",
                        "3",
                        "2",
                        "CS101",
                        "Duplicate Python",
                        "3",
                        "2",
                        "MATH",
                        "Math",
                        "4",
                        "3",
                        "   ",
                        "CS101",
                        "90",
                        "3",
                        "S999",
                        "CS101",
                        "90",
                        "3",
                        "S001",
                        "NOPE",
                        "90",
                        "3",
                        "S001",
                        "CS101",
                        "abc",
                        "3",
                        "S001",
                        "CS101",
                        "101",
                        "3",
                        "S001",
                        "CS101",
                        "90",
                        "3",
                        "S001",
                        "CS101",
                        "91",
                        "4",
                        "S001",
                        "MATH",
                        "80",
                        "9",
                        "x",
                        "99",
                        "10",
                        "0",
                        "",
                    ]
                ),
                store,
            )

            self.assertEqual(session.returncode, 0, session.stderr)
            self.assertIn("学号不能为空", session.stdout)
            self.assertIn("学号已存在", session.stdout)
            self.assertIn("课程编号不能为空", session.stdout)
            self.assertIn("课程编号已存在", session.stdout)
            self.assertIn("学生不存在", session.stdout)
            self.assertIn("课程不存在", session.stdout)
            self.assertIn("成绩必须是数字", session.stdout)
            self.assertIn("成绩必须在 0 到 100 之间", session.stdout)
            self.assertIn("成绩记录已存在", session.stdout)
            self.assertIn("成绩记录不存在", session.stdout)
            self.assertIn("无效排名方式", session.stdout)
            self.assertIn("未知选项", session.stdout)
            self.assertIn("已录入成绩 S001 CS101 90.00", session.stdout)
            self.assertIn("保存成功", session.stdout)

            data = json.loads(store.read_text(encoding="utf-8"))
            self.assertEqual(len(data.get("students", [])), 1)
            self.assertEqual(len(data.get("courses", [])), 2)
            self.assertEqual(len(data.get("grades", [])), 1)
            grade = data["grades"][0]
            self.assertEqual(grade["student_id"], "S001")
            self.assertEqual(grade["course_code"], "CS101")
            self.assertAlmostEqual(float(grade["score"]), 90.0)

    def test_invalid_gradebook_file_fails_before_menu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "gradebook.json"
            store.write_text("{not-json", encoding="utf-8")

            session = run_gradebook_session("0\n", store)

            self.assertNotEqual(session.returncode, 0)
            self.assertIn("成绩文件无效", session.stderr)
            self.assertNotIn("学生成绩管理系统", session.stdout)
            self.assertEqual(store.read_text(encoding="utf-8"), "{not-json")


if __name__ == "__main__":
    unittest.main()
