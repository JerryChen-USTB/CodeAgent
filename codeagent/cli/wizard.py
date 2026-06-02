"""Interactive wizard command skeleton."""

from __future__ import annotations

import typer

from codeagent.cli.progress import ProgressReporter


def wizard_command() -> None:
    """Start the guided configuration wizard."""
    ProgressReporter().planned(
        "wizard",
        "The guided task setup flow will collect stages, input materials, model "
        "configuration, and approval settings in a later milestone.",
    )
    raise typer.Exit()
