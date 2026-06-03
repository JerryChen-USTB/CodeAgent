from __future__ import annotations

import subprocess

from codeagent.benchmark.environment import (
    BugsInPyEnvironmentDetector,
    CommandProbeResult,
)


def test_bugsinpy_detector_reports_missing_wsl_as_blocker() -> None:
    def missing_wsl(_argv, _timeout_seconds):
        raise FileNotFoundError("wsl")

    status = BugsInPyEnvironmentDetector(command_runner=missing_wsl).detect()

    assert status.available is False
    assert any("WSL is not available" in blocker for blocker in status.blockers)
    assert status.name == "bugsinpy_wsl_conda"


def test_bugsinpy_detector_reports_wsl_timeout_as_blocker() -> None:
    def timed_out(_argv, timeout_seconds):
        raise subprocess.TimeoutExpired(["wsl"], timeout_seconds)

    status = BugsInPyEnvironmentDetector(command_runner=timed_out).detect()

    assert status.available is False
    assert any("WSL is not available" in blocker for blocker in status.blockers)


def test_bugsinpy_detector_reports_ready_environment() -> None:
    def ready_runner(argv, _timeout_seconds):
        text = " ".join(argv)
        if "wslpath" in text:
            return CommandProbeResult(argv=tuple(argv), exit_code=0, stdout="/mnt/d/CodeAgent\n")
        if "sys.version_info" in text:
            return CommandProbeResult(argv=tuple(argv), exit_code=0, stdout="3.8.3\n")
        return CommandProbeResult(argv=tuple(argv), exit_code=0, stdout="")

    status = BugsInPyEnvironmentDetector(command_runner=ready_runner).detect()

    assert status.available is True
    assert status.blockers == []
    assert status.details["conda_env"] == "codeagent-bugsinpy-py383"
    assert status.details["python_version"] == "3.8.3"


def test_bugsinpy_detector_reports_missing_conda_env_and_framework_scripts() -> None:
    def runner(argv, _timeout_seconds):
        text = " ".join(argv)
        if "wslpath" in text:
            return CommandProbeResult(argv=tuple(argv), exit_code=0, stdout="/mnt/d/CodeAgent\n")
        if "conda env list" in text:
            return CommandProbeResult(argv=tuple(argv), exit_code=1, stderr="missing env")
        if "dataset/BugsInPy" in text:
            return CommandProbeResult(argv=tuple(argv), exit_code=1, stderr="missing scripts")
        if "sys.version_info" in text:
            return CommandProbeResult(argv=tuple(argv), exit_code=1, stderr="missing python")
        return CommandProbeResult(argv=tuple(argv), exit_code=0, stdout="")

    status = BugsInPyEnvironmentDetector(command_runner=runner).detect()

    assert status.available is False
    assert any("conda environment is missing" in blocker for blocker in status.blockers)
    assert any("official framework scripts are missing" in blocker for blocker in status.blockers)
    assert any("Python version could not be checked" in blocker for blocker in status.blockers)


def test_bugsinpy_detector_reports_python_version_mismatch() -> None:
    def runner(argv, _timeout_seconds):
        text = " ".join(argv)
        if "wslpath" in text:
            return CommandProbeResult(argv=tuple(argv), exit_code=0, stdout="/mnt/d/CodeAgent\n")
        if "sys.version_info" in text:
            return CommandProbeResult(argv=tuple(argv), exit_code=0, stdout="3.11.9\n")
        return CommandProbeResult(argv=tuple(argv), exit_code=0, stdout="")

    status = BugsInPyEnvironmentDetector(command_runner=runner).detect()

    assert status.available is False
    assert any("Python version mismatch" in blocker for blocker in status.blockers)
    assert status.details["detected_python_version"] == "3.11.9"
