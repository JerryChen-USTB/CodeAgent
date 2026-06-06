"""Action planning for the independent TUI harness."""

from __future__ import annotations

from dataclasses import dataclass

from tools.tui_harness.screen import Choice, ScreenSnapshot


KEY_SEQUENCES = {
    "up": "\x1b[A",
    "down": "\x1b[B",
    "left": "\x1b[D",
    "right": "\x1b[C",
    "enter": "\r",
    "ctrl-s": "\x13",
    "ctrl-c": "\x03",
    "escape": "\x1b",
    "space": " ",
    "backspace": "\x7f",
}


@dataclass(frozen=True)
class PlannedKeys:
    """Concrete key stream plus a short reason for logs."""

    text: str
    reason: str


class ActionPlanningError(ValueError):
    """Raised when the current screen cannot support the requested action."""


def keys_to_text(keys: str | list[str]) -> PlannedKeys:
    """Convert comma-separated key names or literal text into a key stream."""
    names = [item.strip() for item in keys.split(",")] if isinstance(keys, str) else keys
    output: list[str] = []
    for name in names:
        if not name:
            continue
        normalized = name.lower()
        if normalized in KEY_SEQUENCES:
            output.append(KEY_SEQUENCES[normalized])
        elif len(name) == 1:
            output.append(name)
        else:
            raise ActionPlanningError(f"unknown key name: {name}")
    return PlannedKeys("".join(output), f"send keys: {', '.join(names)}")


def text_input(text: str, *, submit: bool = True) -> PlannedKeys:
    suffix = KEY_SEQUENCES["enter"] if submit else ""
    return PlannedKeys(text + suffix, "send text input")


def select_label(snapshot: ScreenSnapshot, label: str) -> PlannedKeys:
    """Plan arrow keys and Enter to choose a visible label."""
    if not snapshot.choices:
        raise ActionPlanningError("current screen has no visible choices")
    target = _find_choice(snapshot.choices, label)
    current = _current_choice_index(snapshot.choices)
    moves = _movement_keys(current, target.index, len(snapshot.choices))
    keys = "".join(KEY_SEQUENCES[move] for move in moves)
    return PlannedKeys(
        keys + KEY_SEQUENCES["enter"],
        f"select visible label: {target.label}",
    )


def approve(snapshot: ScreenSnapshot) -> PlannedKeys:
    return select_label(snapshot, _approval_label(snapshot, approve_rest=False))


def approve_rest(snapshot: ScreenSnapshot) -> PlannedKeys:
    return select_label(snapshot, _approval_label(snapshot, approve_rest=True))


def respond(snapshot: ScreenSnapshot) -> PlannedKeys:
    label = _find_label_containing(
        snapshot.choices,
        ["告知 CodeAgent 如何调整", "提出修改意见", "反馈"],
    )
    return select_label(snapshot, label)


def _approval_label(snapshot: ScreenSnapshot, *, approve_rest: bool) -> str:
    if approve_rest:
        return _find_label_containing(snapshot.choices, ["本阶段不再提示"])
    return _find_label_containing(
        snapshot.choices,
        ["是，实施", "是，应用此补丁", "是，运行命令", "批准并继续", "approve"],
    )


def _find_choice(choices: list[Choice], label: str) -> Choice:
    normalized = _normalize_label(label)
    for choice in choices:
        if _normalize_label(choice.label) == normalized:
            return choice
    for choice in choices:
        choice_label = _normalize_label(choice.label)
        if normalized in choice_label or choice_label in normalized:
            return choice
    available = ", ".join(choice.label for choice in choices)
    raise ActionPlanningError(f"label not found: {label}; available: {available}")


def _find_label_containing(choices: list[Choice], fragments: list[str]) -> str:
    lowered = [(choice.label, _normalize_label(choice.label)) for choice in choices]
    for fragment in fragments:
        needle = _normalize_label(fragment)
        for label, normalized in lowered:
            if needle in normalized:
                return label
    available = ", ".join(choice.label for choice in choices)
    raise ActionPlanningError(f"no matching choice; available: {available}")


def _current_choice_index(choices: list[Choice]) -> int:
    for choice in choices:
        if choice.selected:
            return choice.index
    return 0


def _movement_keys(current: int, target: int, total: int) -> list[str]:
    if total <= 0:
        return []
    forward = (target - current) % total
    backward = (current - target) % total
    if forward <= backward:
        return ["down"] * forward
    return ["up"] * backward


def _normalize_label(label: str) -> str:
    return " ".join(label.strip().lower().split())
