from __future__ import annotations

import importlib.util
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
sys.path.insert(0, str(WORKSPACE))


class RunningServer:
    def __init__(self, db_path: Path):
        from library_lending import create_server

        self.server = create_server(db_path=str(db_path), host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base_url = ""

    def __enter__(self) -> "RunningServer":
        self.thread.start()
        port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self, path: str) -> tuple[int, str]:
        return self._request(path)

    def post(self, path: str, data: dict[str, str]) -> tuple[int, str]:
        return self._request(path, data)

    def _request(self, path: str, data: dict[str, str] | None = None) -> tuple[int, str]:
        url = self.base_url + path
        if data is None:
            request = Request(url)
        else:
            body = urlencode(data).encode("utf-8")
            request = Request(
                url,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except HTTPError as error:
            return error.code, error.read().decode("utf-8")


class LibraryLendingWebOracleTests(unittest.TestCase):
    def assert_success(self, status: int, body: str) -> None:
        self.assertIn(status, {200, 201}, body)

    def assert_error(self, status: int, body: str, expected: str) -> None:
        self.assertGreaterEqual(status, 400, body)
        self.assertIn(expected, body)

    def add_book(self, server: RunningServer, isbn: str = "978-1", copies: str = "2") -> str:
        status, body = server.post(
            "/books",
            {
                "isbn": isbn,
                "title": "Clean Code",
                "author": "Robert Martin",
                "copies": copies,
            },
        )
        self.assert_success(status, body)
        return body

    def add_reader(self, server: RunningServer, reader: str = "r1", name: str = "Ada") -> str:
        status, body = server.post("/readers", {"reader": reader, "name": name})
        self.assert_success(status, body)
        return body

    def test_home_lending_lifecycle_and_persistence(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("library_lending.__main__"))

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library.db"

            with RunningServer(db) as server:
                status, home = server.get("/")
                self.assert_success(status, home)
                self.assertIn("图书借阅管理系统", home)
                for label in ["添加图书", "注册读者", "借书", "还书", "库存", "逾期"]:
                    self.assertIn(label, home)

                self.assertIn("book 978-1 available copies: 2", self.add_book(server))
                self.assertIn("reader r1 registered", self.add_reader(server))

                status, borrowed = server.post(
                    "/loans/borrow",
                    {"reader": "r1", "isbn": "978-1", "date": "2026-06-01"},
                )
                self.assert_success(status, borrowed)
                self.assertIn("borrowed 978-1 by r1 due 2026-06-15", borrowed)

                status, books = server.get("/books")
                self.assert_success(status, books)
                self.assertIn("978-1 Clean Code by Robert Martin copies 2 available 1", books)

                status, duplicate = server.post(
                    "/loans/borrow",
                    {"reader": "r1", "isbn": "978-1", "date": "2026-06-02"},
                )
                self.assert_error(status, duplicate, "already borrowed")

                status, not_yet_overdue = server.get("/overdue?date=2026-06-14")
                self.assert_success(status, not_yet_overdue)
                self.assertIn("no overdue loans", not_yet_overdue)

                status, overdue = server.get("/overdue?date=2026-06-20")
                self.assert_success(status, overdue)
                self.assertIn("r1 978-1 due 2026-06-15", overdue)

                status, returned = server.post(
                    "/loans/return",
                    {"reader": "r1", "isbn": "978-1", "date": "2026-06-05"},
                )
                self.assert_success(status, returned)
                self.assertIn("returned 978-1 by r1", returned)

                status, restored = server.get("/books")
                self.assert_success(status, restored)
                self.assertIn("978-1 Clean Code by Robert Martin copies 2 available 2", restored)

            with RunningServer(db) as restarted:
                status, books = restarted.get("/books")
                self.assert_success(status, books)
                self.assertIn("978-1 Clean Code by Robert Martin copies 2 available 2", books)

                status, overdue = restarted.get("/overdue?date=2026-06-20")
                self.assert_success(status, overdue)
                self.assertIn("no overdue loans", overdue)

    def test_stock_limit_and_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library.db"

            with RunningServer(db) as server:
                status, bad_copies = server.post(
                    "/books",
                    {
                        "isbn": "bad",
                        "title": "Bad",
                        "author": "Nobody",
                        "copies": "0",
                    },
                )
                self.assert_error(status, bad_copies, "invalid copies")

                status, bad_date = server.get("/overdue?date=2026/06/20")
                self.assert_error(status, bad_date, "invalid date")

                status, missing_reader = server.post(
                    "/loans/borrow",
                    {"reader": "missing", "isbn": "missing-book", "date": "2026-06-01"},
                )
                self.assert_error(status, missing_reader, "reader not found")

                self.assertIn("reader r1 registered", self.add_reader(server, "r1", "Ada"))
                status, missing_book = server.post(
                    "/loans/borrow",
                    {"reader": "r1", "isbn": "missing-book", "date": "2026-06-01"},
                )
                self.assert_error(status, missing_book, "book not found")

                self.assertIn("book 978-1 available copies: 1", self.add_book(server, "978-1", "1"))
                self.assertIn("reader r2 registered", self.add_reader(server, "r2", "Bob"))

                status, first = server.post(
                    "/loans/borrow",
                    {"reader": "r1", "isbn": "978-1", "date": "2026-06-01"},
                )
                self.assert_success(status, first)

                status, no_stock = server.post(
                    "/loans/borrow",
                    {"reader": "r2", "isbn": "978-1", "date": "2026-06-02"},
                )
                self.assert_error(status, no_stock, "no available copies")

                status, missing_loan = server.post(
                    "/loans/return",
                    {"reader": "r2", "isbn": "978-1", "date": "2026-06-03"},
                )
                self.assert_error(status, missing_loan, "loan not found")


if __name__ == "__main__":
    unittest.main()
