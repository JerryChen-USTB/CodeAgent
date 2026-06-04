"""Codex-style terminal UI helpers for wizard, approvals, and progress."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Literal, Protocol

from rich.console import Console

from codeagent.cli.approval_console import (
    ApprovalInputError,
    _choice_label,
    _ordered_allowed_decisions,
    parse_approval_decision,
)
from codeagent.cli.progress import ProgressEventFormatter, ProgressReporter
from codeagent.config import defaults
from codeagent.tools.hitl import ApprovalDecision, ApprovalRequest


FORM_GROUPS = {
    "basic": "基础设置",
    "materials": "输入材料",
    "runtime": "运行策略",
    "model": "模型与审批",
    "final": "最终确认",
}

FIELD_GROUPS = {
    "stages": "basic",
    "project_path": "basic",
    "input_materials": "materials",
    "manual_materials": "materials",
    "output_dir": "runtime",
    "test_command": "runtime",
    "model_name": "model",
    "approval_mode": "model",
    "start": "final",
}

FIELD_LABELS = {
    "stages": "执行阶段",
    "project_path": "项目目录",
    "input_materials": "输入材料",
    "manual_materials": "补充材料路径",
    "output_dir": "输出目录",
    "test_command": "测试命令",
    "model_name": "模型",
    "approval_mode": "审批模式",
    "start": "开始运行",
}

FIELD_HELP = {
    "stages": "选择 Agent 要执行的阶段组合。",
    "project_path": "Agent 将在这个项目目录中读写代码。",
    "input_materials": "从自动发现的候选文档中多选需求材料。",
    "manual_materials": "手动追加候选列表里没有的材料路径，多个路径用分号分隔。",
    "output_dir": "运行产物、日志和报告会写入这个目录。",
    "test_command": "测试、调试和修复阶段用于验证的命令。",
    "model_name": "本次 wizard 运行使用的 OpenRouter 模型。",
    "approval_mode": "是否要求人工确认计划、补丁和命令。",
    "start": "校验表单并启动 CodeAgent。",
}

EDITABLE_FIELDS = tuple(field_id for field_id in FIELD_LABELS if field_id != "start")


@dataclass(frozen=True)
class TuiChoice:
    title: str
    value: str


@dataclass(frozen=True)
class FormRow:
    kind: Literal["group", "field", "action"]
    row_id: str
    label: str
    value: str = ""


@dataclass
class WizardFormState:
    """Small reducer-friendly state model for the task form."""

    values: dict[str, object] = field(default_factory=dict)
    expanded_groups: set[str] = field(default_factory=lambda: set(FORM_GROUPS))
    cursor: int = 0
    status: str = "按 ↑/↓ 移动，Enter 编辑，Space 展开/收起或多选，Ctrl+S 开始运行。"

    @classmethod
    def create(cls) -> WizardFormState:
        return cls(
            values={
                "stages": "implement,test,debug,repair",
                "project_path": ".",
                "input_materials": [],
                "manual_materials": "",
                "output_dir": defaults.DEFAULT_OUTPUT_DIR,
                "test_command": defaults.DEFAULT_TEST_COMMAND,
                "model_name": defaults.DEFAULT_MODEL_NAME,
                "approval_mode": "manual",
            }
        )

    def visible_rows(self) -> list[FormRow]:
        rows: list[FormRow] = []
        for group_id, group_label in FORM_GROUPS.items():
            icon = "▾" if group_id in self.expanded_groups else "▸"
            rows.append(FormRow("group", group_id, f"{icon} {group_label}"))
            if group_id not in self.expanded_groups:
                continue
            for field_id, field_group in FIELD_GROUPS.items():
                if field_group != group_id:
                    continue
                if field_id == "start":
                    rows.append(FormRow("action", field_id, "▶ 开始运行 CodeAgent"))
                else:
                    rows.append(
                        FormRow(
                            "field",
                            field_id,
                            FIELD_LABELS[field_id],
                            self.render_value(field_id),
                        )
                    )
        return rows

    def selected_row(self) -> FormRow:
        rows = self.visible_rows()
        if not rows:
            raise RuntimeError("wizard form has no rows")
        self.cursor = max(0, min(self.cursor, len(rows) - 1))
        return rows[self.cursor]

    def move(self, offset: int) -> None:
        rows = self.visible_rows()
        if not rows:
            self.cursor = 0
            return
        self.cursor = (self.cursor + offset) % len(rows)

    def toggle_group(self, group_id: str) -> None:
        if group_id in self.expanded_groups:
            self.expanded_groups.remove(group_id)
        else:
            self.expanded_groups.add(group_id)
        self.cursor = min(self.cursor, max(0, len(self.visible_rows()) - 1))

    def set_value(self, field_id: str, value: object) -> None:
        self.values[field_id] = value
        self.status = f"已更新：{FIELD_LABELS.get(field_id, field_id)}"

    def render_value(self, field_id: str) -> str:
        value = self.values.get(field_id)
        if field_id == "input_materials":
            items = [Path(str(item)).name for item in value or []]  # type: ignore[union-attr]
            return "，".join(items) if items else "<未选择>"
        if field_id == "approval_mode":
            return "开启人工审批" if value == "manual" else "关闭人工审批，自动批准"
        return str(value or "<空>")


class TuiPromptDriver(Protocol):
    def select(self, title: str, choices: list[TuiChoice], *, default: str) -> str: ...

    def multi_select(
        self,
        title: str,
        choices: list[TuiChoice],
        *,
        default: list[str],
    ) -> list[str]: ...

    def text(self, title: str, *, default: str = "") -> str: ...


class PromptToolkitTuiDriver:
    """Small prompt_toolkit-backed controls with Codex-like keyboard behavior."""

    def select(self, title: str, choices: list[TuiChoice], *, default: str) -> str:
        return _run_select_prompt(title, choices, default=default)

    def multi_select(
        self,
        title: str,
        choices: list[TuiChoice],
        *,
        default: list[str],
    ) -> list[str]:
        return _run_multi_select_prompt(title, choices, default=default)

    def text(self, title: str, *, default: str = "") -> str:
        from prompt_toolkit import PromptSession

        session = PromptSession()
        answer = session.prompt(f"{title}: ", default=default)
        return answer


class CodexLikeWizardSession:
    """Questionnaire-style wizard that can revisit fields before running."""

    def __init__(self, driver: TuiPromptDriver | None = None) -> None:
        self.driver = driver or PromptToolkitTuiDriver()
        self.state = WizardFormState.create()

    def run(self):
        from codeagent.cli.wizard import WizardPromptAnswers

        while True:
            action, row_id = self._run_form_once()
            if action == "cancel":
                return None
            if action == "start":
                try:
                    return self._build_answers(WizardPromptAnswers)
                except ValueError as exc:
                    self.state.status = f"表单校验失败：{exc}"
                    continue
            if action == "edit":
                self._edit_field(row_id)

    def _run_form_once(self) -> tuple[str, str]:
        return _run_form_prompt(self.state)

    def _edit_field(self, field_id: str) -> None:
        from codeagent.cli.wizard import (
            _MANUAL_MATERIAL_SENTINEL,
            _approval_mode_choices,
            _discover_input_material_candidates,
            _model_choices,
            _stage_choices,
        )

        if field_id == "stages":
            choices = [TuiChoice(title, value) for title, value in _stage_choices()]
            value = self.driver.select(
                "选择执行阶段",
                choices,
                default=str(self.state.values["stages"]),
            )
            self.state.set_value(field_id, value)
            return
        if field_id == "input_materials":
            project_path = str(self.state.values.get("project_path") or ".")
            choices = [
                TuiChoice(title, value)
                for title, value in _discover_input_material_candidates(project_path)
            ]
            current = [str(item) for item in self.state.values.get(field_id, [])]
            selected = self.driver.multi_select(
                "选择输入材料",
                choices,
                default=current,
            )
            if _MANUAL_MATERIAL_SENTINEL in selected:
                selected = [item for item in selected if item != _MANUAL_MATERIAL_SENTINEL]
                self.state.status = "已选择手动添加，请填写“补充材料路径”。"
            self.state.set_value(field_id, selected)
            return
        if field_id == "model_name":
            choices = [TuiChoice(title, value) for title, value in _model_choices()]
            value = self.driver.select(
                "选择模型",
                choices,
                default=str(self.state.values["model_name"]),
            )
            self.state.set_value(field_id, value)
            return
        if field_id == "approval_mode":
            choices = [TuiChoice(title, value) for title, value in _approval_mode_choices()]
            value = self.driver.select(
                "选择审批模式",
                choices,
                default=str(self.state.values["approval_mode"]),
            )
            self.state.set_value(field_id, value)
            return

        value = self.driver.text(
            FIELD_LABELS[field_id],
            default=str(self.state.values.get(field_id) or ""),
        )
        self.state.set_value(field_id, value)

    def _build_answers(self, answer_type):
        from codeagent.cli.wizard import _split_manual_paths

        values = self.state.values
        selected = [str(item) for item in values.get("input_materials", [])]
        manual = _split_manual_paths(str(values.get("manual_materials") or ""))
        if not str(values.get("project_path") or "").strip():
            raise ValueError("项目目录不能为空")
        if not str(values.get("output_dir") or "").strip():
            raise ValueError("输出目录不能为空")
        if not str(values.get("test_command") or "").strip():
            raise ValueError("测试命令不能为空")
        return answer_type(
            stages=str(values["stages"]),
            project_path=str(values["project_path"]),
            input_material_paths=[*selected, *manual],
            output_dir=str(values["output_dir"]),
            test_command=str(values["test_command"]),
            approval_mode=str(values["approval_mode"]),
            model_name=str(values["model_name"]),
        )


class TuiApprovalConsole:
    """Approval console rendered as a compact bottom-pane style selector."""

    def __init__(self, driver: TuiPromptDriver | None = None) -> None:
        self.driver = driver or PromptToolkitTuiDriver()

    def prompt(self, request: ApprovalRequest) -> ApprovalDecision:
        decisions = _ordered_allowed_decisions(request)
        choices = [
            TuiChoice(_choice_label(request, decision), decision)
            for decision in decisions
        ]
        decision_type = self.driver.select(
            request.title,
            choices,
            default=(
                request.default_decision
                if request.default_decision in request.allowed_decisions
                else decisions[0]
            ),
        )
        comment = None
        edited_payload_text = None
        if decision_type == "respond":
            comment = self.driver.text(
                "请告知 CodeAgent 如何调整",
                default="",
            )
            if not comment.strip():
                raise ApprovalInputError("提出修改意见时必须填写具体意见")
        elif decision_type == "edit":
            edited_payload_text = self.driver.text(
                "请粘贴修改后的 JSON 对象",
                default="",
            )
        elif decision_type in {"reject", "cancel"}:
            comment = self.driver.text("可选：请输入原因", default="")

        return parse_approval_decision(
            decision_type,
            request=request,
            edited_payload_text=edited_payload_text,
            comment=comment,
        )


class TuiProgressReporter(ProgressReporter):
    """Progress reporter with a lightweight Codex-like transcript style."""

    def __init__(
        self,
        console: Console | None = None,
        formatter: ProgressEventFormatter | None = None,
    ) -> None:
        super().__init__(console=console, formatter=formatter)

    def render_event(self, event: dict[str, object]) -> str:
        line = self._formatter.format_event(event)
        self._console.print(f"│ {line}", markup=False)
        try:
            self._console.file.flush()
        except Exception:
            pass
        return line


def tui_available() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import prompt_toolkit  # noqa: F401
    except Exception:
        return False
    return True


def _run_form_prompt(state: WizardFormState) -> tuple[str, str]:
    from prompt_toolkit.application import Application
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.styles import Style

    kb = KeyBindings()

    def rows() -> list[FormRow]:
        return state.visible_rows()

    def finish(action: str, row_id: str) -> None:
        get_app().exit(result=(action, row_id))

    @kb.add("up")
    def _(event) -> None:
        state.move(-1)
        event.app.invalidate()

    @kb.add("down")
    def _(event) -> None:
        state.move(1)
        event.app.invalidate()

    @kb.add(" ")
    def _(event) -> None:
        selected = state.selected_row()
        if selected.kind == "group":
            state.toggle_group(selected.row_id)
            event.app.invalidate()
            return
        if selected.row_id == "input_materials":
            finish("edit", selected.row_id)

    @kb.add("enter")
    def _(event) -> None:
        selected = state.selected_row()
        if selected.kind == "group":
            state.toggle_group(selected.row_id)
            event.app.invalidate()
        elif selected.kind == "action":
            finish("start", selected.row_id)
        else:
            finish("edit", selected.row_id)

    @kb.add("c-s")
    def _(event) -> None:
        finish("start", "start")

    @kb.add("c-c")
    def _(event) -> None:
        finish("cancel", "cancel")

    @kb.add("escape")
    def _(event) -> None:
        state.status = "仍在任务表单中；移动到“开始运行”后按 Enter，或按 Ctrl+C 取消。"
        event.app.invalidate()

    def render() -> FormattedText:
        rendered: list[tuple[str, str]] = []
        rendered.append(("class:title", "CodeAgent 任务表单\n"))
        rendered.append(("class:hint", "Codex 风格 TUI：像填问卷一样修改字段，确认后直接运行。\n\n"))
        current_rows = rows()
        state.cursor = max(0, min(state.cursor, len(current_rows) - 1))
        for index, row in enumerate(current_rows):
            selected = index == state.cursor
            prefix = "▌ " if selected else "  "
            style = "class:selected" if selected else "class:normal"
            if row.kind == "group":
                rendered.append((style, f"{prefix}{row.label}\n"))
            elif row.kind == "action":
                rendered.append((style, f"{prefix}{row.label}\n"))
            else:
                rendered.append((style, f"{prefix}{row.label}: {row.value}\n"))
        selected = state.selected_row()
        rendered.append(("", "\n"))
        rendered.append(("class:status", f"{state.status}\n"))
        if selected.row_id in FIELD_HELP:
            rendered.append(("class:hint", f"说明：{FIELD_HELP[selected.row_id]}\n"))
        return FormattedText(rendered)

    app = Application(
        layout=Layout(Window(FormattedTextControl(render, focusable=True))),
        key_bindings=kb,
        style=Style.from_dict(
            {
                "title": "bold",
                "hint": "ansibrightblack",
                "selected": "reverse",
                "status": "ansicyan",
            }
        ),
        full_screen=False,
        mouse_support=False,
    )
    return app.run()


def _run_select_prompt(
    title: str,
    choices: list[TuiChoice],
    *,
    default: str,
) -> str:
    if not choices:
        raise ValueError(f"{title} 没有可选项")
    default_index = next(
        (index for index, choice in enumerate(choices) if choice.value == default),
        0,
    )
    index = default_index

    from prompt_toolkit.application import Application
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.styles import Style

    kb = KeyBindings()

    @kb.add("up")
    def _(event) -> None:
        nonlocal index
        index = (index - 1) % len(choices)
        event.app.invalidate()

    @kb.add("down")
    def _(event) -> None:
        nonlocal index
        index = (index + 1) % len(choices)
        event.app.invalidate()

    @kb.add("enter")
    def _(event) -> None:
        get_app().exit(result=choices[index].value)

    @kb.add("c-c")
    def _(event) -> None:
        get_app().exit(result=choices[default_index].value)

    @kb.add("escape")
    def _(event) -> None:
        get_app().exit(result=choices[default_index].value)

    def render() -> FormattedText:
        rendered: list[tuple[str, str]] = [
            ("class:title", f"{title}\n"),
            ("class:hint", "上下键移动，回车选中。\n\n"),
        ]
        for item_index, choice in enumerate(choices):
            selected = item_index == index
            marker = "▌ " if selected else "  "
            style = "class:selected" if selected else "class:normal"
            rendered.append((style, f"{marker}{choice.title}\n"))
        return FormattedText(rendered)

    app = Application(
        layout=Layout(Window(FormattedTextControl(render, focusable=True))),
        key_bindings=kb,
        style=Style.from_dict({"title": "bold", "hint": "ansibrightblack", "selected": "reverse"}),
        full_screen=False,
        mouse_support=False,
    )
    return app.run()


def _run_multi_select_prompt(
    title: str,
    choices: list[TuiChoice],
    *,
    default: list[str],
) -> list[str]:
    if not choices:
        return []
    index = 0
    selected = set(default)

    from prompt_toolkit.application import Application
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.styles import Style

    kb = KeyBindings()

    @kb.add("up")
    def _(event) -> None:
        nonlocal index
        index = (index - 1) % len(choices)
        event.app.invalidate()

    @kb.add("down")
    def _(event) -> None:
        nonlocal index
        index = (index + 1) % len(choices)
        event.app.invalidate()

    @kb.add(" ")
    def _(event) -> None:
        value = choices[index].value
        if value in selected:
            selected.remove(value)
        else:
            selected.add(value)
        event.app.invalidate()

    @kb.add("enter")
    def _(event) -> None:
        get_app().exit(result=[choice.value for choice in choices if choice.value in selected])

    @kb.add("c-c")
    def _(event) -> None:
        get_app().exit(result=list(default))

    @kb.add("escape")
    def _(event) -> None:
        get_app().exit(result=list(default))

    def render() -> FormattedText:
        rendered: list[tuple[str, str]] = [
            ("class:title", f"{title}\n"),
            ("class:hint", "上下键移动，空格勾选/取消，回车确认。\n\n"),
        ]
        for item_index, choice in enumerate(choices):
            focused = item_index == index
            checked = "●" if choice.value in selected else "○"
            marker = "▌ " if focused else "  "
            style = "class:selected" if focused else "class:normal"
            rendered.append((style, f"{marker}{checked} {choice.title}\n"))
        return FormattedText(rendered)

    app = Application(
        layout=Layout(Window(FormattedTextControl(render, focusable=True))),
        key_bindings=kb,
        style=Style.from_dict({"title": "bold", "hint": "ansibrightblack", "selected": "reverse"}),
        full_screen=False,
        mouse_support=False,
    )
    return app.run()
