"""Terminal screen parsing for the independent TUI harness."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Literal


PromptKind = Literal[
    "wizard_form",
    "select",
    "text_input",
    "approval",
    "progress",
    "finished",
    "unknown",
]


@dataclass(frozen=True)
class Choice:
    """A visible selectable item on the current terminal screen."""

    label: str
    index: int
    selected: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ScreenSnapshot:
    """Structured view of the current terminal screen."""

    prompt_kind: PromptKind
    screen: str
    choices: list[Choice]
    context_files: list[str]
    command: str | None
    suggested_actions: list[str]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["choices"] = [choice.to_dict() for choice in self.choices]
        return data


_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)
_CSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_FORM_LABELS = (
    "执行阶段",
    "项目目录",
    "输入材料",
    "输出目录",
    "测试命令",
    "模型",
    "审批模式",
    "开始运行 CodeAgent",
)

_NON_CHOICE_PREFIXES = (
    "CodeAgent",
    "任务表单",
    "配置任务后",
    "上下键移动",
    "方向键移动",
    "说明：",
    "当前动作：",
    "请先审查",
    "将执行命令",
    "工作目录",
    "│",
    "[",
)


class TerminalDisplay:
    """Maintain a current terminal screen from raw PTY output.

    ``pyte`` is used when available. A plain ANSI-stripping fallback is kept so
    parser unit tests and basic diagnostics still work before tool dependencies
    are installed.
    """

    def __init__(self, *, columns: int = 120, rows: int = 40) -> None:
        self.columns = columns
        self.rows = rows
        self._raw_text = ""
        self._screen = None
        self._stream = None
        try:
            import pyte
        except Exception:
            return
        self._screen = pyte.Screen(columns, rows)
        self._stream = pyte.Stream(self._screen)

    def feed(self, data: bytes | str) -> None:
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
        if not text:
            return
        self._raw_text = (self._raw_text + text)[-1_000_000:]
        if self._stream is not None:
            self._stream.feed(text)

    def raw_text(self) -> str:
        return self._raw_text

    def screen_text(self) -> str:
        if self._screen is not None:
            lines = [line.rstrip() for line in self._screen.display]
            return "\n".join(lines).rstrip()
        clean = strip_ansi(self._raw_text)
        return "\n".join(clean.splitlines()[-self.rows:]).rstrip()


def strip_ansi(text: str) -> str:
    """Remove ANSI and OSC escape sequences from terminal text."""
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL_RE.sub("", text)


def parse_snapshot(screen: str) -> ScreenSnapshot:
    """Parse a cleaned terminal screen into a structured snapshot."""
    clean = _normalize_screen(screen)
    choices = _extract_choices(clean)
    context_files = _extract_context_files(clean)
    command = _extract_command(clean)
    kind = _detect_prompt_kind(clean, choices)
    return ScreenSnapshot(
        prompt_kind=kind,
        screen=clean,
        choices=choices,
        context_files=context_files,
        command=command,
        suggested_actions=_suggested_actions(kind, choices),
    )


def _normalize_screen(screen: str) -> str:
    clean = strip_ansi(screen)
    lines = [line.rstrip() for line in clean.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _detect_prompt_kind(screen: str, choices: list[Choice]) -> PromptKind:
    if any(marker in screen for marker in ("运行已结束", "[最终结果]", "final_status")):
        return "finished"
    if _looks_like_approval(screen, choices):
        return "approval"
    if _looks_like_text_input(screen):
        return "text_input"
    if "CodeAgent" in screen and "任务表单" in screen and _choices_are_form_rows(choices):
        return "wizard_form"
    if choices and _looks_like_select(screen):
        return "select"
    if "CodeAgent" in screen and "任务表单" in screen:
        return "wizard_form"
    if any(marker in screen for marker in ("[实现阶段]", "[测试阶段]", "[调试阶段]", "[修复阶段]", "[工具]", "[路由]")):
        return "progress"
    return "unknown"


def _choices_are_form_rows(choices: list[Choice]) -> bool:
    return bool(choices) and all(choice.label in _FORM_LABELS for choice in choices)


def _looks_like_select(screen: str) -> bool:
    return "上下键移动" in screen or any(
        line.strip().startswith(">") for line in screen.splitlines()
    )


def _looks_like_approval(screen: str, choices: list[Choice]) -> bool:
    labels = [choice.label for choice in choices]
    if any(label.startswith("是，") for label in labels) and any(
        "告知 CodeAgent" in label or "运行命令" in label or "应用此补丁" in label
        for label in labels
    ):
        return True
    return any(
        marker in screen
        for marker in (
            "请先审查以下文件",
            "应用这个单文件",
            "运行此测试命令",
            "运行此回归验证命令",
            "运行此调试复现命令",
            "审查测试方案",
            "审查实现计划",
            "审查修复计划",
        )
    )


def _looks_like_text_input(screen: str) -> bool:
    stripped = screen.rstrip()
    markers = (
        "请告知 CodeAgent 如何调整",
        "请输入希望 Agent 改进的具体意见",
        "请粘贴修改后的 JSON 对象",
        "手动输入路径:",
        "正在填写：",
    )
    if any(marker in screen for marker in markers):
        return True
    return stripped.endswith(":") or stripped.endswith("：")


def _suggested_actions(kind: PromptKind, choices: list[Choice]) -> list[str]:
    if kind == "approval":
        actions = ["approve", "respond", "select-label"]
        if any("本阶段不再提示" in choice.label for choice in choices):
            actions.insert(1, "approve-rest")
        return actions
    if kind in {"wizard_form", "select"}:
        return ["select-label", "keys"]
    if kind == "text_input":
        return ["text", "keys"]
    if kind == "finished":
        return ["stop"]
    if kind == "progress":
        return ["observe", "stop"]
    return ["keys", "stop"]


def _extract_choices(screen: str) -> list[Choice]:
    lines = screen.splitlines()
    numeric = _extract_numeric_choices(lines)
    if numeric:
        return numeric
    if "CodeAgent" in screen and "任务表单" in screen:
        form_choices = _extract_form_choices(lines)
        if form_choices:
            return form_choices
    selected_block = _extract_block_choices(lines)
    if selected_block:
        return selected_block
    return []


def _extract_numeric_choices(lines: list[str]) -> list[Choice]:
    pattern = re.compile(r"^\s*(?P<selected>>)?\s*(?P<number>\d+)\.\s+(?P<label>.+?)\s*$")
    parsed: list[tuple[int, bool, str]] = []
    has_selected = False
    for line_index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        selected = bool(match.group("selected"))
        has_selected = has_selected or selected
        parsed.append((line_index, selected, match.group("label")))
    if not has_selected:
        return []
    return [
        Choice(label=label, index=index, selected=selected)
        for index, (_line_index, selected, label) in enumerate(parsed)
    ]


def _extract_block_choices(lines: list[str]) -> list[Choice]:
    selected_indexes = [
        index for index, line in enumerate(lines) if line.strip().startswith(">")
    ]
    if not selected_indexes:
        return []
    selected_index = selected_indexes[-1]
    start = selected_index
    while start > 0 and _line_can_be_choice(lines[start - 1]):
        start -= 1
    end = selected_index
    while end + 1 < len(lines) and _line_can_be_choice(lines[end + 1]):
        end += 1
    choices: list[Choice] = []
    for line in lines[start : end + 1]:
        parsed = _parse_choice_line(line)
        if parsed is None:
            continue
        selected, label = parsed
        choices.append(Choice(label=label, index=len(choices), selected=selected))
    return choices


def _line_can_be_choice(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(_NON_CHOICE_PREFIXES):
        return False
    if stripped.endswith("。") or stripped.endswith("："):
        return False
    return True


def _parse_choice_line(line: str) -> tuple[bool, str] | None:
    stripped = line.strip()
    if not stripped:
        return None
    selected = stripped.startswith(">")
    if selected:
        stripped = stripped[1:].strip()
    stripped = re.sub(r"^\d+\.\s+", "", stripped)
    if not stripped or stripped.startswith(_NON_CHOICE_PREFIXES):
        return None
    return selected, stripped


def _extract_form_choices(lines: list[str]) -> list[Choice]:
    choices: list[Choice] = []
    label_pattern = "|".join(re.escape(label) for label in _FORM_LABELS)
    pattern = re.compile(rf"^\s*(?P<selected>>)?\s*(?P<label>{label_pattern})(?::|\s*$)")
    for line in lines:
        match = pattern.match(line)
        if not match:
            continue
        if not match.group("selected") and len(line) == len(line.lstrip()):
            continue
        choices.append(
            Choice(
                label=match.group("label"),
                index=len(choices),
                selected=bool(match.group("selected")),
            )
        )
    return choices


def _extract_context_files(screen: str) -> list[str]:
    files: list[str] = []
    in_files = False
    for raw_line in screen.splitlines():
        line = raw_line.strip()
        if "请先审查以下文件" in line:
            in_files = True
            continue
        if in_files and not line:
            break
        if in_files and (line.startswith("将执行命令") or line.startswith("当前动作")):
            break
        if not in_files or not line.startswith("- "):
            continue
        value = line[2:].strip()
        paren = re.search(r"\(([^()]+)\)\s*$", value)
        path = paren.group(1).strip() if paren else value
        if path and path not in files:
            files.append(path)
    return files


def _extract_command(screen: str) -> str | None:
    lines = screen.splitlines()
    for index, raw_line in enumerate(lines):
        if "将执行命令" not in raw_line:
            continue
        for candidate in lines[index + 1 :]:
            line = candidate.strip()
            if not line:
                continue
            if line.startswith("- "):
                return line[2:].strip()
            return line
    return None
