"""Command line interface for the independent CodeAgent TUI harness."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from typing import Any
import os


DEFAULT_HOST = "127.0.0.1"
DEFAULT_ROWS = 40
DEFAULT_COLUMNS = 120


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command_name == "start":
        return _cmd_start(args)
    if args.command_name == "observe":
        return _cmd_observe(args)
    if args.command_name == "act":
        return _cmd_act(args)
    if args.command_name == "stop":
        return _cmd_stop(args)
    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.tui_harness",
        description="Drive CodeAgent's real TUI through a PTY-backed daemon.",
    )
    subparsers = parser.add_subparsers(dest="command_name")

    start = subparsers.add_parser("start", help="start a PTY-backed TUI session")
    start.add_argument("--session", help="session directory")
    start.add_argument("--cwd", required=True, help="working directory for the TUI command")
    start.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    start.add_argument("--columns", type=int, default=DEFAULT_COLUMNS)
    start.add_argument("command", nargs=argparse.REMAINDER)

    observe = subparsers.add_parser("observe", help="observe the current TUI screen")
    observe.add_argument("--session", required=True)
    observe.add_argument("--json", action="store_true", dest="as_json")

    act = subparsers.add_parser("act", help="send one action to the current TUI")
    act.add_argument("--session", required=True)
    group = act.add_mutually_exclusive_group(required=True)
    group.add_argument("--select-label")
    group.add_argument("--text")
    group.add_argument("--approve", action="store_true")
    group.add_argument("--approve-rest", action="store_true")
    group.add_argument("--respond")
    group.add_argument("--keys")
    act.add_argument("--no-submit", action="store_true", help="do not press Enter after --text")
    act.add_argument("--settle-seconds", type=float, default=0.3)
    act.add_argument("--json", action="store_true", dest="as_json")

    stop = subparsers.add_parser("stop", help="stop a TUI harness session")
    stop.add_argument("--session", required=True)
    stop.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _cmd_start(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("start requires a command after --", file=sys.stderr)
        return 2

    session_dir = Path(args.session).resolve() if args.session else _default_session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)
    host = DEFAULT_HOST
    port = _free_port(host)
    token = secrets.token_urlsafe(24)
    cwd = Path(args.cwd).resolve()
    if not cwd.is_dir():
        print(f"cwd does not exist or is not a directory: {cwd}", file=sys.stderr)
        return 2

    _write_json(
        session_dir / "session.json",
        {
            "version": 1,
            "status": "starting",
            "host": host,
            "port": port,
            "token": token,
            "cwd": str(cwd),
            "command": command,
            "created_at": _now(),
        },
    )

    stdout = (session_dir / "daemon.stdout.log").open("ab")
    stderr = (session_dir / "daemon.stderr.log").open("ab")
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = _prepend_path(str(repo_root), env.get("PYTHONPATH"))
    env.setdefault("PYTHONIOENCODING", "utf-8")
    daemon_command = [
        sys.executable,
        "-m",
        "tools.tui_harness.daemon",
        "--session",
        str(session_dir),
        "--host",
        host,
        "--port",
        str(port),
        "--token",
        token,
        "--cwd",
        str(cwd),
        "--rows",
        str(args.rows),
        "--columns",
        str(args.columns),
        "--",
        *command,
    ]
    creationflags = 0
    popen_kwargs: dict[str, object] = {"start_new_session": True}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
        popen_kwargs = {}
    process = subprocess.Popen(
        daemon_command,
        cwd=str(repo_root),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        creationflags=creationflags,
        **popen_kwargs,
    )
    stdout.close()
    stderr.close()

    ready = _wait_until_ready(session_dir, timeout=10)
    session = _load_session(session_dir)
    if not ready:
        print("TUI harness daemon did not become ready.", file=sys.stderr)
        print(f"session: {session_dir}")
        print(f"daemon_pid: {process.pid}")
        print(f"status: {session.get('status', 'unknown')}")
        print(f"stderr: {session_dir / 'daemon.stderr.log'}")
        return 1

    print("TUI harness session started")
    print(f"session: {session_dir}")
    print(f"daemon_pid: {session.get('daemon_pid', process.pid)}")
    print(f"child_pid: {session.get('child_pid', '<unknown>')}")
    print(f"status: {session.get('status', 'running')}")
    return 0


def _cmd_observe(args: argparse.Namespace) -> int:
    response = _request(Path(args.session), {"action": "observe"})
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        _print_observation(response)
    return 0 if response.get("ok") else 1


def _cmd_act(args: argparse.Namespace) -> int:
    payload: dict[str, object] = {"action": "act", "settle_seconds": args.settle_seconds}
    if args.select_label:
        payload["select_label"] = args.select_label
    elif args.text is not None:
        payload["text"] = args.text
        payload["submit"] = not args.no_submit
    elif args.approve:
        payload["approve"] = True
    elif args.approve_rest:
        payload["approve_rest"] = True
    elif args.respond is not None:
        payload["respond"] = args.respond
    elif args.keys:
        payload["keys"] = args.keys
    response = _request(Path(args.session), payload)
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        _print_action_result(response)
    return 0 if response.get("ok") else 1


def _cmd_stop(args: argparse.Namespace) -> int:
    response = _request(Path(args.session), {"action": "stop"})
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        if response.get("ok"):
            print("TUI harness session stopping")
        else:
            print(f"stop failed: {response.get('error')}", file=sys.stderr)
    return 0 if response.get("ok") else 1


def _request(session_dir: Path, payload: dict[str, object]) -> dict[str, Any]:
    session = _load_session(session_dir)
    payload = {**payload, "token": session["token"]}
    with socket.create_connection((session["host"], int(session["port"])), timeout=5) as sock:
        sock.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        response = _read_json_line(sock)
    return response


def _read_json_line(sock: socket.socket) -> dict[str, Any]:
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    raw = b"".join(chunks).splitlines()[0]
    return json.loads(raw.decode("utf-8"))


def _print_observation(response: dict[str, Any]) -> None:
    if not response.get("ok"):
        print(f"observe failed: {response.get('error')}", file=sys.stderr)
        return
    snapshot = response["snapshot"]
    print(f"prompt_kind: {snapshot['prompt_kind']}")
    print(f"process_alive: {snapshot.get('process_alive')}")
    if snapshot.get("choices"):
        print("choices:")
        for choice in snapshot["choices"]:
            marker = ">" if choice.get("selected") else " "
            print(f"  {marker} {choice['index']}: {choice['label']}")
    if snapshot.get("context_files"):
        print("context_files:")
        for path in snapshot["context_files"]:
            print(f"  - {path}")
    if snapshot.get("command"):
        print(f"command: {snapshot['command']}")
    print(f"suggested_actions: {', '.join(snapshot.get('suggested_actions') or [])}")
    print("")
    print("screen:")
    print(snapshot.get("screen") or "")


def _print_action_result(response: dict[str, Any]) -> None:
    if not response.get("ok"):
        print(f"act failed: {response.get('error')}", file=sys.stderr)
        return
    result = response["result"]
    print(f"acted: {result.get('reason')}")
    snapshot = result.get("snapshot") or {}
    if snapshot:
        print(f"prompt_kind: {snapshot.get('prompt_kind')}")
        if snapshot.get("suggested_actions"):
            print(f"suggested_actions: {', '.join(snapshot['suggested_actions'])}")


def _wait_until_ready(session_dir: Path, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            session = _load_session(session_dir)
        except Exception:
            time.sleep(0.1)
            continue
        if session.get("status") == "failed":
            return False
        try:
            response = _request(session_dir, {"action": "observe"})
        except Exception:
            time.sleep(0.2)
            continue
        return bool(response.get("ok"))
    return False


def _load_session(session_dir: Path) -> dict[str, Any]:
    session_file = session_dir / "session.json"
    if not session_file.exists():
        raise FileNotFoundError(f"missing session.json: {session_file}")
    return json.loads(session_file.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _default_session_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("codeagent_runs") / "dev_tui_sessions" / stamp


def _prepend_path(path: str, existing: str | None) -> str:
    if not existing:
        return path
    return path + os.pathsep + existing


def _now() -> str:
    return datetime.now().astimezone().isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
