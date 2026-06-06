"""Environment preflight checks for optional benchmark cases."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True)
class CommandProbeResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class EnvironmentStatus:
    name: str
    available: bool
    blockers: list[str] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)


CommandRunner = Callable[[Sequence[str], float], CommandProbeResult]


class BugsInPyEnvironmentDetector:
    """Detect whether the local WSL + conda BugsInPy environment is ready."""

    def __init__(
        self,
        *,
        conda_env: str = "codeagent-bugsinpy-py383",
        python_version: str = "3.8.3",
        repo_root: str | Path | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.conda_env = conda_env
        self.python_version = python_version
        self.repo_root = Path(repo_root or Path.cwd()).resolve()
        self.command_runner = command_runner or _run_probe

    def detect(self) -> EnvironmentStatus:
        blockers: list[str] = []
        details = {
            "conda_env": self.conda_env,
            "python_version": self.python_version,
        }

        wslpath = self._probe(["wsl", "--", "wslpath", "-a", str(self.repo_root)])
        if wslpath is None:
            blockers.append("WSL is not available or wslpath could not convert paths.")
            return _status(blockers=blockers, details=details)
        repo_wsl = wslpath.stdout.strip().splitlines()[0] if wslpath.stdout.strip() else ""
        if not repo_wsl:
            blockers.append("WSL path conversion returned an empty repository path.")
            return _status(blockers=blockers, details=details)
        details["repo_wsl"] = repo_wsl

        checks = [
            (
                "conda profile is missing in WSL.",
                f'test -f "$HOME/miniconda3/etc/profile.d/conda.sh"',
            ),
            (
                f"conda environment is missing: {self.conda_env}.",
                (
                    'source "$HOME/miniconda3/etc/profile.d/conda.sh" && '
                    f'conda env list | grep -q "^{self.conda_env}[[:space:]]"'
                ),
            ),
            (
                "dos2unix is missing from the BugsInPy conda environment.",
                (
                    'source "$HOME/miniconda3/etc/profile.d/conda.sh" && '
                    f'conda run -n "{self.conda_env}" dos2unix --version >/dev/null'
                ),
            ),
            (
                "BugsInPy official framework scripts are missing under dataset/BugsInPy.",
                (
                    f'test -x "{repo_wsl}/dataset/BugsInPy/framework/bin/bugsinpy-checkout" '
                    f'&& test -x "{repo_wsl}/dataset/BugsInPy/framework/bin/bugsinpy-compile" '
                    f'&& test -x "{repo_wsl}/dataset/BugsInPy/framework/bin/bugsinpy-test"'
                ),
            ),
        ]
        for blocker, script in checks:
            result = self._probe(["wsl", "--", "bash", "-lc", script])
            if result is None or result.exit_code != 0:
                blockers.append(blocker)

        version_script = (
            'source "$HOME/miniconda3/etc/profile.d/conda.sh" && '
            f'conda run -n "{self.conda_env}" python - <<\'PY\'\n'
            "import sys\n"
            "print('.'.join(map(str, sys.version_info[:3])))\n"
            "PY"
        )
        version = self._probe(["wsl", "--", "bash", "-lc", version_script])
        if version is None or version.exit_code != 0:
            blockers.append("Python version could not be checked in the BugsInPy conda environment.")
        else:
            actual_version = version.stdout.strip().splitlines()[-1]
            details["detected_python_version"] = actual_version
            if actual_version != self.python_version:
                blockers.append(
                    "BugsInPy conda Python version mismatch: "
                    f"expected {self.python_version}, got {actual_version}."
                )

        return _status(blockers=blockers, details=details)

    def _probe(self, argv: Sequence[str]) -> CommandProbeResult | None:
        try:
            return self.command_runner(argv, 20.0)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None


def _status(*, blockers: list[str], details: dict[str, str]) -> EnvironmentStatus:
    return EnvironmentStatus(
        name="bugsinpy_wsl_conda",
        available=not blockers,
        blockers=blockers,
        details=details,
    )


def _run_probe(argv: Sequence[str], timeout_seconds: float) -> CommandProbeResult:
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return CommandProbeResult(
        argv=tuple(argv),
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
