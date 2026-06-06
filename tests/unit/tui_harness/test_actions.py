from __future__ import annotations

import pytest

from tools.tui_harness.actions import (
    KEY_SEQUENCES,
    ActionPlanningError,
    approve,
    approve_rest,
    keys_to_text,
    select_label,
    text_input,
)
from tools.tui_harness.screen import Choice, ScreenSnapshot


def _snapshot(choices: list[Choice]) -> ScreenSnapshot:
    return ScreenSnapshot(
        prompt_kind="approval",
        screen="",
        choices=choices,
        context_files=[],
        command=None,
        suggested_actions=[],
    )


def test_select_label_uses_shortest_visible_choice_movement() -> None:
    snapshot = _snapshot(
        [
            Choice("A", 0, selected=True),
            Choice("B", 1),
            Choice("C", 2),
        ]
    )

    planned = select_label(snapshot, "C")

    assert planned.text == KEY_SEQUENCES["up"] + KEY_SEQUENCES["enter"]
    assert planned.reason == "select visible label: C"


def test_approve_and_approve_rest_choose_visible_approval_labels() -> None:
    snapshot = _snapshot(
        [
            Choice("是，应用此补丁", 0, selected=True),
            Choice("是，应用此补丁，本阶段不再提示", 1),
            Choice("否，告知 CodeAgent 如何调整", 2),
        ]
    )

    assert approve(snapshot).text == KEY_SEQUENCES["enter"]
    assert approve_rest(snapshot).text == KEY_SEQUENCES["down"] + KEY_SEQUENCES["enter"]


def test_text_and_named_keys_are_converted_to_terminal_sequences() -> None:
    assert text_input("hello").text == "hello\r"
    assert text_input("hello", submit=False).text == "hello"
    assert keys_to_text("down,enter").text == "\x1b[B\r"


def test_select_label_reports_available_choices_when_missing() -> None:
    snapshot = _snapshot([Choice("A", 0, selected=True)])

    with pytest.raises(ActionPlanningError, match="available: A"):
        select_label(snapshot, "missing")
