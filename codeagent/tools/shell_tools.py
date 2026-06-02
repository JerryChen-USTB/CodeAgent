"""Approved shell command execution with logs and operation records."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from codeagent.runtime.commands import (
    CommandApproval,
    CommandOperationRecord,
    CommandPolicyDecision,
    ShellResult,
)


ALLOWED_DIRECT_COMMANDS = {"pytest", "pytest.exe"}
ALLOWED_PYTHON_MODULES = {"pytest", "unittest", "py_compile"}
PYTEST_DENIED_OPTIONS = {"--override-ini", "-o", "-p", "--pyargs"}
PYTEST_PATH_OPTIONS = {"-c", "--config-file", "--rootdir", "--confcutdir", "--basetemp"}
UNITTEST_PATH_OPTIONS = {"-s", "--start-directory", "-t", "--top-level-directory"}
PYTHON_EXECUTABLE_NAMES = {
    "python",
    "python.exe",
    "python3",
    "python3.exe",
    "py",
    "py.exe",
}


class CommandDeniedError(PermissionError):
    """Raised when command approval or policy denies execution."""


class ShellRunner:
    def __init__(self, *, logs_dir: str | Path, max_output_chars: int = 12_000) -> None:
        self.logs_dir = Path(logs_dir)
        self.max_output_chars = max_output_chars

    def run(
        self,
        command: str,
        *,
        cwd: str | Path,
        timeout_seconds: float,
        approval: CommandApproval,
    ) -> ShellResult:
        cwd_path = Path(cwd).resolve()
        if not cwd_path.is_dir():
            raise ValueError(f"command cwd is not a directory: {cwd_path}")
        if not approval.approved:
            raise CommandDeniedError(f"command not approved: {approval.reason}")
        policy = classify_command(command, cwd=cwd_path)
        if not policy.allowed:
            raise CommandDeniedError(f"command not allowed: {policy.reason}")

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stdout_log, stderr_log, record_path = self._operation_paths(approval.operation_id)
        started = time.perf_counter()
        stdout = ""
        stderr = ""
        exit_code: int | None
        timed_out = False

        try:
            completed = subprocess.run(
                policy.argv,
                cwd=cwd_path,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = _coerce_process_output(exc.stdout)
            stderr = _coerce_process_output(exc.stderr)
            timeout_message = f"Command timed out after {timeout_seconds} seconds."
            stderr = f"{stderr}\n{timeout_message}".strip() + "\n"
            exit_code = None
            timed_out = True

        duration_seconds = time.perf_counter() - started
        stdout_log.write_text(stdout, encoding="utf-8")
        stderr_log.write_text(stderr, encoding="utf-8")
        (
            stdout_preview,
            stdout_truncated,
            stdout_original_chars,
        ) = _truncate_output(stdout, self.max_output_chars)
        (
            stderr_preview,
            stderr_truncated,
            stderr_original_chars,
        ) = _truncate_output(stderr, self.max_output_chars)
        result = ShellResult(
            operation_id=approval.operation_id,
            command=command,
            argv=policy.argv,
            cwd=cwd_path,
            exit_code=exit_code,
            stdout=stdout_preview,
            stderr=stderr_preview,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            stdout_original_chars=stdout_original_chars,
            stderr_original_chars=stderr_original_chars,
            duration_seconds=duration_seconds,
            timed_out=timed_out,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            record_path=record_path,
        )
        record = CommandOperationRecord(
            operation_id=approval.operation_id,
            command=command,
            argv=policy.argv,
            cwd=str(cwd_path),
            timeout_seconds=timeout_seconds,
            approval=approval.to_record(),
            policy={"allowed": policy.allowed, "reason": policy.reason},
            exit_code=exit_code,
            timed_out=timed_out,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            stdout_original_chars=stdout_original_chars,
            stderr_original_chars=stderr_original_chars,
            duration_seconds=duration_seconds,
            stdout_log=str(stdout_log),
            stderr_log=str(stderr_log),
        )
        record_path.write_text(
            json.dumps(record.to_json_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return result

    def _operation_paths(self, operation_id: str) -> tuple[Path, Path, Path]:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", operation_id).strip("_") or "command"
        return (
            self.logs_dir / f"{safe_id}.stdout.log",
            self.logs_dir / f"{safe_id}.stderr.log",
            self.logs_dir / f"{safe_id}.command.json",
        )


def classify_command(command: str, *, cwd: str | Path | None = None) -> CommandPolicyDecision:
    argv = _split_command(command)
    if not argv:
        return CommandPolicyDecision(False, "empty command", [])
    executable = Path(argv[0]).name.lower()
    cwd_path = Path(cwd).resolve() if cwd is not None else None
    if executable in ALLOWED_DIRECT_COMMANDS:
        invalid_reason = _validate_module_args("pytest", argv[1:], cwd_path)
        if invalid_reason:
            return CommandPolicyDecision(False, invalid_reason, argv)
        return CommandPolicyDecision(True, "allowed pytest command", argv)
    if (
        executable in PYTHON_EXECUTABLE_NAMES
        and len(argv) >= 3
        and argv[1] == "-m"
        and argv[2] in ALLOWED_PYTHON_MODULES
    ):
        invalid_reason = _validate_module_args(argv[2], argv[3:], cwd_path)
        if invalid_reason:
            return CommandPolicyDecision(False, invalid_reason, argv)
        return CommandPolicyDecision(True, f"allowed python module {argv[2]}", argv)
    return CommandPolicyDecision(
        False,
        "only pytest, unittest, or py_compile commands are allowed",
        argv,
    )


def _split_command(command: str) -> list[str]:
    return [_strip_outer_quotes(part) for part in shlex.split(command, posix=os.name != "nt")]


def _strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _coerce_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _validate_module_args(
    module_name: str, args: list[str], cwd: Path | None
) -> str | None:
    index = 0
    while index < len(args):
        arg = args[index]
        option_name, option_value = _split_option_value(arg)
        if module_name == "pytest":
            if _is_denied_pytest_option(arg, option_name):
                return f"high-risk pytest option is not allowed: {option_name}"
            if option_name in PYTEST_PATH_OPTIONS:
                if option_value is None:
                    index += 1
                    if index >= len(args):
                        return f"missing path for pytest option: {option_name}"
                    option_value = args[index]
                invalid_path = _validate_path_argument(option_value, cwd)
                if invalid_path:
                    return invalid_path
                index += 1
                continue
        if module_name == "unittest" and option_name in UNITTEST_PATH_OPTIONS:
            if option_value is None:
                index += 1
                if index >= len(args):
                    return f"missing path for unittest option: {option_name}"
                option_value = args[index]
            invalid_path = _validate_path_argument(option_value, cwd)
            if invalid_path:
                return invalid_path
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        if module_name == "py_compile" or _looks_like_path_argument(arg):
            invalid_path = _validate_path_argument(arg, cwd)
            if invalid_path:
                return invalid_path
        index += 1
    return None


def _split_option_value(arg: str) -> tuple[str, str | None]:
    if "=" not in arg:
        return arg, None
    option_name, option_value = arg.split("=", 1)
    return option_name, option_value


def _is_denied_pytest_option(arg: str, option_name: str) -> bool:
    return (
        option_name in PYTEST_DENIED_OPTIONS
        or arg.startswith("-o=")
        or arg.startswith("-p")
    )


def _looks_like_path_argument(arg: str) -> bool:
    return (
        "/" in arg
        or "\\" in arg
        or arg.endswith(".py")
        or arg in {".", ".."}
    )


def _validate_path_argument(arg: str, cwd: Path | None) -> str | None:
    if cwd is None:
        return None
    candidate = Path(arg)
    resolved = candidate.resolve() if candidate.is_absolute() else (cwd / candidate).resolve()
    if not _is_relative_to(resolved, cwd):
        return f"path argument outside cwd is not allowed: {arg}"
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _truncate_output(text: str, max_chars: int) -> tuple[str, bool, int]:
    original_chars = len(text)
    if max_chars <= 0 or original_chars <= max_chars:
        return text, False, original_chars
    omitted = original_chars - max_chars
    preview = (
        text[:max_chars]
        + f"\n[truncated: {omitted} chars omitted; full output saved to log]\n"
    )
    return preview, True, original_chars
