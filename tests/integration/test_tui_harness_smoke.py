from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("CODEAGENT_TUI_HARNESS_SMOKE") != "1",
    reason="set CODEAGENT_TUI_HARNESS_SMOKE=1 to run the real PTY smoke test",
)


def test_tui_harness_drives_prompt_toolkit_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture_tui.py"
    fixture.write_text(
        textwrap.dedent(
            """
            from prompt_toolkit.application import Application
            from prompt_toolkit.application.current import get_app
            from prompt_toolkit.formatted_text import FormattedText
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.layout import Layout
            from prompt_toolkit.layout.controls import FormattedTextControl
            from prompt_toolkit.layout.containers import Window

            choices = ["First", "Second"]
            index = 0
            kb = KeyBindings()

            @kb.add("down")
            def _(event):
                global index
                index = (index + 1) % len(choices)
                event.app.invalidate()

            @kb.add("enter")
            def _(event):
                get_app().exit(result=choices[index])

            def render():
                lines = [("class:title", "Pick one\\n"), ("", "上下键移动，回车选中。\\n\\n")]
                for item_index, choice in enumerate(choices):
                    marker = "> " if item_index == index else "  "
                    lines.append(("", f"{marker}{choice}\\n"))
                return FormattedText(lines)

            result = Application(
                layout=Layout(Window(FormattedTextControl(render))),
                key_bindings=kb,
                full_screen=False,
            ).run()
            print(f"picked={result}", flush=True)
            """
        ),
        encoding="utf-8",
    )
    session = tmp_path / "session"
    start = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.tui_harness",
            "start",
            "--session",
            str(session),
            "--cwd",
            str(tmp_path),
            "--",
            sys.executable,
            str(fixture),
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert start.returncode == 0, start.stderr + start.stdout

    observe = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.tui_harness",
            "observe",
            "--session",
            str(session),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert observe.returncode == 0, observe.stderr
    assert json.loads(observe.stdout)["snapshot"]["prompt_kind"] == "select"

    act = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.tui_harness",
            "act",
            "--session",
            str(session),
            "--select-label",
            "Second",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert act.returncode == 0, act.stderr + act.stdout
    assert "picked=Second" in (session / "terminal.clean.log").read_text(encoding="utf-8")
