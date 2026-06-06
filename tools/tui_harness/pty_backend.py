"""PTY backend adapters used by the TUI harness daemon."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
import os
import platform
import subprocess


class PtyBackend(ABC):
    """Small PTY interface shared by Windows and POSIX backends."""

    @abstractmethod
    def read(self, *, timeout: float = 0.1) -> bytes:
        """Read available PTY output."""

    @abstractmethod
    def write(self, text: str) -> None:
        """Write a key/text sequence to the PTY."""

    @abstractmethod
    def is_alive(self) -> bool:
        """Return whether the child process is still alive."""

    @abstractmethod
    def terminate(self) -> None:
        """Terminate the child process."""

    @abstractmethod
    def pid(self) -> int | None:
        """Return the child process id if available."""


def create_pty_backend(
    command: list[str],
    *,
    cwd: Path,
    rows: int,
    columns: int,
    env: dict[str, str] | None = None,
) -> PtyBackend:
    """Create the platform-appropriate PTY backend."""
    if platform.system() == "Windows":
        return WinPtyBackend(command, cwd=cwd, rows=rows, columns=columns, env=env)
    return PexpectBackend(command, cwd=cwd, rows=rows, columns=columns, env=env)


class WinPtyBackend(PtyBackend):
    """Windows ConPTY/winpty backend via pywinpty."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        rows: int,
        columns: int,
        env: dict[str, str] | None = None,
    ) -> None:
        try:
            from winpty import PtyProcess
        except Exception as exc:
            raise RuntimeError(
                "pywinpty is required on Windows. Install tools/tui_harness/requirements.txt."
            ) from exc
        command_line = subprocess.list2cmdline(command)
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        self._process = PtyProcess.spawn(
            command_line,
            cwd=str(cwd),
            env=merged_env,
            dimensions=(columns, rows),
        )

    def read(self, *, timeout: float = 0.1) -> bytes:
        del timeout
        try:
            try:
                chunk = self._process.read(4096)
            except TypeError:
                chunk = self._process.read()
        except Exception:
            return b""
        if isinstance(chunk, bytes):
            return chunk
        return str(chunk).encode("utf-8", errors="replace")

    def write(self, text: str) -> None:
        self._process.write(text)

    def is_alive(self) -> bool:
        try:
            return bool(self._process.isalive())
        except Exception:
            return False

    def terminate(self) -> None:
        try:
            self._process.terminate(force=True)
        except TypeError:
            self._process.terminate()
        except Exception:
            pass

    def pid(self) -> int | None:
        return int(getattr(self._process, "pid", 0) or 0) or None


class PexpectBackend(PtyBackend):
    """POSIX PTY backend via pexpect."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        rows: int,
        columns: int,
        env: dict[str, str] | None = None,
    ) -> None:
        try:
            import pexpect
        except Exception as exc:
            raise RuntimeError(
                "pexpect is required on non-Windows platforms. "
                "Install tools/tui_harness/requirements.txt."
            ) from exc
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        self._pexpect = pexpect
        self._child = pexpect.spawn(
            command[0],
            command[1:],
            cwd=str(cwd),
            env=merged_env,
            dimensions=(rows, columns),
            encoding="utf-8",
            codec_errors="replace",
        )

    def read(self, *, timeout: float = 0.1) -> bytes:
        try:
            chunk = self._child.read_nonblocking(size=4096, timeout=timeout)
        except self._pexpect.TIMEOUT:
            return b""
        except self._pexpect.EOF:
            return b""
        if isinstance(chunk, bytes):
            return chunk
        return str(chunk).encode("utf-8", errors="replace")

    def write(self, text: str) -> None:
        self._child.send(text)

    def is_alive(self) -> bool:
        return bool(self._child.isalive())

    def terminate(self) -> None:
        try:
            self._child.terminate(force=True)
        except Exception:
            pass

    def pid(self) -> int | None:
        return int(getattr(self._child, "pid", 0) or 0) or None
