"""Loopback daemon that owns the real PTY-backed TUI process."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socketserver
import threading
import time
from typing import Any
import os

from tools.tui_harness import actions
from tools.tui_harness.pty_backend import PtyBackend, create_pty_backend
from tools.tui_harness.screen import TerminalDisplay, parse_snapshot, strip_ansi


class HarnessError(RuntimeError):
    """Raised for daemon-side harness failures."""


class TerminalHarnessDaemon:
    """Own a CodeAgent TUI process and expose observe/act/stop over localhost."""

    def __init__(
        self,
        *,
        session_dir: Path,
        host: str,
        port: int,
        token: str,
        command: list[str],
        cwd: Path,
        rows: int,
        columns: int,
    ) -> None:
        self.session_dir = session_dir
        self.host = host
        self.port = port
        self.token = token
        self.command = command
        self.cwd = cwd
        self.rows = rows
        self.columns = columns
        self.display = TerminalDisplay(columns=columns, rows=rows)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.backend: PtyBackend | None = None
        self.server: _HarnessServer | None = None

    def run(self) -> int:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._record_event("daemon_starting", command=self.command, cwd=str(self.cwd))
        try:
            self.backend = create_pty_backend(
                self.command,
                cwd=self.cwd,
                rows=self.rows,
                columns=self.columns,
                env={"PYTHONUNBUFFERED": "1"},
            )
        except Exception as exc:
            self._write_session_status("failed", error=str(exc))
            self._record_event("daemon_failed", error=str(exc))
            return 1

        self._write_session_status("running", child_pid=self.backend.pid())
        reader = threading.Thread(target=self._reader_loop, name="tui-harness-reader", daemon=True)
        reader.start()

        self.server = _HarnessServer((self.host, self.port), _HarnessRequestHandler)
        self.server.daemon_ref = self
        self._record_event("daemon_ready", host=self.host, port=self.port)
        try:
            self.server.serve_forever(poll_interval=0.1)
        finally:
            self.stop_event.set()
            if self.backend is not None and self.backend.is_alive():
                self.backend.terminate()
            reader.join(timeout=2)
            self._write_session_status("stopped")
            self._record_event("daemon_stopped")
        return 0

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("token") != self.token:
            return {"ok": False, "error": "invalid token"}
        action = str(request.get("action") or "")
        try:
            if action == "observe":
                return {"ok": True, "snapshot": self.observe()}
            if action == "act":
                return {"ok": True, "result": self.act(request)}
            if action == "stop":
                return {"ok": True, "result": self.stop()}
            return {"ok": False, "error": f"unknown action: {action}"}
        except Exception as exc:
            self._record_event("request_failed", action=action, error=str(exc))
            return {"ok": False, "error": str(exc)}

    def observe(self) -> dict[str, Any]:
        with self.lock:
            screen = self.display.screen_text()
        snapshot = parse_snapshot(screen).to_dict()
        snapshot["process_alive"] = bool(self.backend and self.backend.is_alive())
        self._record_event(
            "observed",
            prompt_kind=snapshot["prompt_kind"],
            choices=[choice["label"] for choice in snapshot["choices"]],
        )
        return snapshot

    def act(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.backend is None or not self.backend.is_alive():
            raise HarnessError("PTY process is not running")
        snapshot = parse_snapshot(self.display.screen_text())
        planned: actions.PlannedKeys | None = None
        if label := request.get("select_label"):
            planned = actions.select_label(snapshot, str(label))
        elif request.get("approve"):
            planned = actions.approve(snapshot)
        elif request.get("approve_rest"):
            planned = actions.approve_rest(snapshot)
        elif response := request.get("respond"):
            planned = actions.respond(snapshot)
            self._write(planned.text)
            self._record_event("acted", kind="respond-select", reason=planned.reason)
            if not self._wait_for_text_prompt(timeout=8):
                raise HarnessError("respond selected, but no text prompt appeared")
            planned = actions.text_input(str(response), submit=True)
        elif text := request.get("text"):
            planned = actions.text_input(str(text), submit=bool(request.get("submit", True)))
        elif keys := request.get("keys"):
            planned = actions.keys_to_text(str(keys))
        else:
            raise HarnessError("no action option provided")

        self._write(planned.text)
        time.sleep(float(request.get("settle_seconds") or 0.3))
        result_snapshot = self.observe()
        self._record_event("acted", reason=planned.reason)
        return {
            "reason": planned.reason,
            "snapshot": result_snapshot,
        }

    def stop(self) -> dict[str, Any]:
        self._record_event("stop_requested")
        self.stop_event.set()
        if self.backend is not None and self.backend.is_alive():
            self.backend.terminate()
        if self.server is not None:
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        return {"status": "stopping"}

    def _write(self, text: str) -> None:
        if self.backend is None:
            raise HarnessError("PTY backend is not initialized")
        self.backend.write(text)

    def _wait_for_text_prompt(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = parse_snapshot(self.display.screen_text())
            if snapshot.prompt_kind == "text_input":
                return True
            time.sleep(0.1)
        return False

    def _reader_loop(self) -> None:
        assert self.backend is not None
        while not self.stop_event.is_set():
            if not self.backend.is_alive():
                break
            chunk = self.backend.read(timeout=0.1)
            if not chunk:
                time.sleep(0.02)
                continue
            text = chunk.decode("utf-8", errors="replace")
            with self.lock:
                self.display.feed(text)
                self._append_bytes("terminal.raw.log", chunk)
                self._append_text("terminal.clean.log", strip_ansi(text))
                self._write_text("screen.txt", self.display.screen_text())
        self._record_event("child_exited", alive=bool(self.backend and self.backend.is_alive()))

    def _write_session_status(self, status: str, **extra: object) -> None:
        payload: dict[str, object] = {}
        session_file = self.session_dir / "session.json"
        if session_file.exists():
            try:
                payload = json.loads(session_file.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        payload.update(
            {
                "version": 1,
                "status": status,
                "daemon_pid": os.getpid(),
                "host": self.host,
                "port": self.port,
                "token": self.token,
                "cwd": str(self.cwd),
                "command": self.command,
                "updated_at": _now(),
                **extra,
            }
        )
        tmp = session_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(session_file)

    def _record_event(self, event_type: str, **payload: object) -> None:
        record = {"type": event_type, "timestamp": _now(), **payload}
        with self.lock:
            with (self.session_dir / "events.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _append_bytes(self, name: str, data: bytes) -> None:
        with (self.session_dir / name).open("ab") as file:
            file.write(data)

    def _append_text(self, name: str, text: str) -> None:
        with (self.session_dir / name).open("a", encoding="utf-8") as file:
            file.write(text)

    def _write_text(self, name: str, text: str) -> None:
        (self.session_dir / name).write_text(text, encoding="utf-8")


class _HarnessServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    daemon_ref: TerminalHarnessDaemon


class _HarnessRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(1_000_000)
        try:
            request = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            response = {"ok": False, "error": f"invalid request JSON: {exc}"}
        else:
            response = self.server.daemon_ref.handle_request(request)  # type: ignore[attr-defined]
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a PTY-backed TUI harness daemon.")
    parser.add_argument("--session", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--rows", type=int, default=40)
    parser.add_argument("--columns", type=int, default=120)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("daemon requires a command after --")
    daemon = TerminalHarnessDaemon(
        session_dir=Path(args.session).resolve(),
        host=args.host,
        port=args.port,
        token=args.token,
        command=command,
        cwd=Path(args.cwd).resolve(),
        rows=args.rows,
        columns=args.columns,
    )
    return daemon.run()


if __name__ == "__main__":
    raise SystemExit(main())
