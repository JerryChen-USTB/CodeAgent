from __future__ import annotations

from pathlib import Path


def test_bugsinpy_scripts_default_to_explicit_case_dir_placeholder() -> None:
    prepare = Path("scripts/prepare_bugsinpy_wsl_conda.ps1").read_text(
        encoding="utf-8"
    )
    run = Path("scripts/run_bugsinpy_wsl_conda.ps1").read_text(encoding="utf-8")

    assert '[string]$CaseDir = ""' in prepare
    assert '[string]$CaseDir = ""' in run
    assert 'benchmark\\cases\\bugsinpy_black_001' not in prepare
    assert 'benchmark\\cases\\bugsinpy_black_001' not in run


def test_prepare_script_allows_clean_case_workspaces_and_rejects_outside_repo() -> None:
    prepare = Path("scripts/prepare_bugsinpy_wsl_conda.ps1").read_text(
        encoding="utf-8"
    )

    assert "case_workspaces" in prepare
    assert "Refusing to prepare path outside allowed benchmark workspaces" in prepare
    assert 'benchmark/codeagent_runs/*/case_workspaces/*' in prepare
    assert 'codeagent_runs/benchmarks/*/case_workspaces/*' in prepare


def test_bugsinpy_scripts_timeout_wsl_path_conversion() -> None:
    prepare = Path("scripts/prepare_bugsinpy_wsl_conda.ps1").read_text(
        encoding="utf-8"
    )
    run = Path("scripts/run_bugsinpy_wsl_conda.ps1").read_text(encoding="utf-8")

    assert "Invoke-WslPath" in prepare
    assert "Invoke-WslPath" in run
    assert "WSL path conversion timed out" in prepare
    assert "WSL path conversion timed out" in run
    assert "Test-WslBashAvailable" in prepare
    assert "Test-WslBashAvailable" in run
    assert "WSL bash preflight timed out" in prepare
    assert "WSL bash preflight timed out" in run
    assert "Invoke-WslBash" in prepare
    assert "Invoke-WslBash" in run
    assert "WSL bash command timed out" in prepare
    assert "WSL bash command timed out" in run
