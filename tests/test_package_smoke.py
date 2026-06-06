from __future__ import annotations

import tomllib
from pathlib import Path

from typer.testing import CliRunner

import codeagent
from codeagent.cli.app import app


def test_package_version_is_available() -> None:
    assert codeagent.__version__


def test_project_metadata_is_defined() -> None:
    project_root = Path(__file__).resolve().parents[1]
    project_metadata = tomllib.loads(
        (project_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert project_metadata["project"]["name"] == "codeagent"
    assert project_metadata["project"]["requires-python"] == ">=3.11"


def test_cli_help_lists_product_name() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "codeagent" in result.output.lower()


def test_cli_version_option_exits_successfully() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "codeagent" in result.output.lower()
