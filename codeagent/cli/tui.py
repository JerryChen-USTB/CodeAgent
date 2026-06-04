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
    kind: Literal["field", "action"]
    row_id: str
    label: str
    value: str = ""


@dataclass
class WizardFormState:
    """Small reducer-friendly state model for the task form."""

    values: dict[str, object] = field(default_factory=dict)
    cursor: int = 0
    status: str = "方向键移动，Enter 编辑，Space 展开选项或选择，Ctrl+S 开始运行。"

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
        for field_id in FIELD_GROUPS:
            if field_id == "start":
                rows.append(FormRow("action", field_id, "开始运行 CodeAgent"))
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

        if isinstance(self.driver, PromptToolkitTuiDriver):
            result = _run_wizard_application(self.state)
            if result == "cancel":
                return None
            if result == "start":
                try:
                    return self._build_answers(WizardPromptAnswers)
                except ValueError:
                    # The interactive application validates before returning start.
                    return None

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
        if selected.row_id == "input_materials":
            finish("edit", selected.row_id)

    @kb.add("enter")
    def _(event) -> None:
        selected = state.selected_row()
        if selected.kind == "action":
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
        rendered.append(("class:hint", "配置任务后直接开始运行。\n\n"))
        current_rows = rows()
        state.cursor = max(0, min(state.cursor, len(current_rows) - 1))
        for index, row in enumerate(current_rows):
            selected = index == state.cursor
            prefix = "> " if selected else "  "
            style = "class:active" if selected else "class:normal"
            if row.kind == "action":
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
                "active": "ansibrightblue bold",
                "status": "ansicyan",
            }
        ),
        full_screen=False,
        mouse_support=False,
    )
    return app.run()


def _run_wizard_application(state: WizardFormState) -> str:
    from prompt_toolkit.application import Application
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.containers import Window
    from codeagent.cli.wizard import (
        _MANUAL_MATERIAL_SENTINEL,
        _approval_mode_choices,
        _discover_input_material_candidates,
        _model_choices,
        _parse_approval_mode,
        _parse_model_name,
        _parse_stages,
        _split_manual_paths,
        _stage_choices,
    )

    mode = "form"
    edit_field = ""
    choices: list[TuiChoice] = []
    choice_index = 0
    multi_selected: set[str] = set()
    text_value = ""
    text_cursor = 0
    text_original = ""
    kb = KeyBindings()

    def finish(result: str) -> None:
        get_app().exit(result=result)

    def current_row() -> FormRow:
        return state.selected_row()

    def choice_default_index(default: str) -> int:
        return next(
            (index for index, choice in enumerate(choices) if choice.value == default),
            0,
        )

    def enter_select(field_id: str, title: str, raw_choices: list[tuple[str, str]]) -> None:
        nonlocal mode, edit_field, choices, choice_index
        mode = "select"
        edit_field = field_id
        choices = [TuiChoice(choice_title, value) for choice_title, value in raw_choices]
        choice_index = choice_default_index(str(state.values.get(field_id) or ""))
        state.status = f"正在选择：{title}"

    def enter_multi_select() -> None:
        nonlocal mode, edit_field, choices, choice_index, multi_selected
        mode = "multi"
        edit_field = "input_materials"
        project_path = str(state.values.get("project_path") or ".")
        choices = [
            TuiChoice(title, value)
            for title, value in _discover_input_material_candidates(project_path)
        ]
        choice_index = 0
        multi_selected = {
            str(item) for item in state.values.get("input_materials", [])
        }
        state.status = "正在选择输入材料"

    def enter_text(field_id: str) -> None:
        nonlocal mode, edit_field, text_value, text_cursor, text_original
        mode = "text"
        edit_field = field_id
        text_original = str(state.values.get(field_id) or "")
        text_value = text_original
        text_cursor = len(text_value)
        state.status = f"正在填写：{FIELD_LABELS[field_id]}"

    def begin_edit(field_id: str) -> None:
        if field_id == "stages":
            enter_select(field_id, "执行阶段", _stage_choices())
        elif field_id == "input_materials":
            enter_multi_select()
        elif field_id == "model_name":
            enter_select(field_id, "模型", _model_choices())
        elif field_id == "approval_mode":
            enter_select(field_id, "审批模式", _approval_mode_choices())
        elif field_id in EDITABLE_FIELDS:
            enter_text(field_id)

    def validate_form() -> bool:
        try:
            _parse_stages(str(state.values.get("stages") or ""))
            _parse_approval_mode(str(state.values.get("approval_mode") or ""))
            _parse_model_name(str(state.values.get("model_name") or ""))
            if not str(state.values.get("project_path") or "").strip():
                raise ValueError("项目目录不能为空")
            if not str(state.values.get("output_dir") or "").strip():
                raise ValueError("输出目录不能为空")
            if not str(state.values.get("test_command") or "").strip():
                raise ValueError("测试命令不能为空")
            _split_manual_paths(str(state.values.get("manual_materials") or ""))
        except ValueError as exc:
            state.status = f"表单校验失败：{exc}"
            return False
        return True

    def commit_text() -> None:
        nonlocal mode
        state.set_value(edit_field, text_value.strip())
        mode = "form"

    def cancel_edit() -> None:
        nonlocal mode
        mode = "form"
        state.status = "已返回任务表单。"

    def commit_choice() -> None:
        nonlocal mode
        if not choices:
            mode = "form"
            return
        state.set_value(edit_field, choices[choice_index].value)
        mode = "form"

    def commit_multi() -> None:
        nonlocal mode
        manual_requested = _MANUAL_MATERIAL_SENTINEL in multi_selected
        selected = [choice.value for choice in choices if choice.value in multi_selected]
        if manual_requested:
            selected = [item for item in selected if item != _MANUAL_MATERIAL_SENTINEL]
        state.set_value("input_materials", selected)
        if manual_requested:
            state.status = "已选择手动添加，请填写“补充材料路径”。"
        mode = "form"

    @kb.add("up")
    def _(event) -> None:
        nonlocal choice_index
        if mode == "form":
            state.move(-1)
        elif mode in {"select", "multi"} and choices:
            choice_index = (choice_index - 1) % len(choices)
        event.app.invalidate()

    @kb.add("down")
    def _(event) -> None:
        nonlocal choice_index
        if mode == "form":
            state.move(1)
        elif mode in {"select", "multi"} and choices:
            choice_index = (choice_index + 1) % len(choices)
        event.app.invalidate()

    @kb.add("left")
    def _(event) -> None:
        nonlocal text_cursor
        if mode == "text":
            text_cursor = max(0, text_cursor - 1)
        event.app.invalidate()

    @kb.add("right")
    def _(event) -> None:
        nonlocal text_cursor
        if mode == "text":
            text_cursor = min(len(text_value), text_cursor + 1)
        event.app.invalidate()

    @kb.add(" ")
    def _(event) -> None:
        if mode == "form":
            row = current_row()
            if row.row_id == "input_materials":
                begin_edit(row.row_id)
        elif mode == "multi" and choices:
            value = choices[choice_index].value
            if value in multi_selected:
                multi_selected.remove(value)
            else:
                multi_selected.add(value)
        elif mode == "text":
            insert_text(" ")
        event.app.invalidate()

    @kb.add("enter")
    def _(event) -> None:
        if mode == "form":
            row = current_row()
            if row.kind == "action":
                if validate_form():
                    finish("start")
            else:
                begin_edit(row.row_id)
        elif mode == "select":
            commit_choice()
        elif mode == "multi":
            commit_multi()
        elif mode == "text":
            commit_text()
        event.app.invalidate()

    @kb.add("c-s")
    def _(event) -> None:
        if validate_form():
            finish("start")
        else:
            event.app.invalidate()

    @kb.add("c-c")
    def _(event) -> None:
        finish("cancel")

    @kb.add("escape")
    def _(event) -> None:
        if mode == "form":
            state.status = "仍在任务表单中。"
        else:
            cancel_edit()
        event.app.invalidate()

    @kb.add("backspace")
    def _(event) -> None:
        nonlocal text_value, text_cursor
        if mode == "text" and text_cursor > 0:
            text_value = text_value[: text_cursor - 1] + text_value[text_cursor:]
            text_cursor -= 1
        event.app.invalidate()

    @kb.add("delete")
    def _(event) -> None:
        nonlocal text_value
        if mode == "text" and text_cursor < len(text_value):
            text_value = text_value[:text_cursor] + text_value[text_cursor + 1 :]
        event.app.invalidate()

    @kb.add("home")
    def _(event) -> None:
        nonlocal text_cursor
        if mode == "text":
            text_cursor = 0
        event.app.invalidate()

    @kb.add("end")
    def _(event) -> None:
        nonlocal text_cursor
        if mode == "text":
            text_cursor = len(text_value)
        event.app.invalidate()

    def insert_text(value: str) -> None:
        nonlocal text_value, text_cursor
        text_value = text_value[:text_cursor] + value + text_value[text_cursor:]
        text_cursor += len(value)

    @kb.add(Keys.Any)
    def _(event) -> None:
        if mode != "text":
            return
        data = event.data
        if data and data.isprintable():
            insert_text(data)
            event.app.invalidate()

    def render() -> FormattedText:
        return _render_form_panel(
            state,
            mode=mode,
            edit_field=edit_field,
            choices=choices,
            focused_choice_index=choice_index,
            selected_values=multi_selected,
            text_value=text_value,
            text_cursor=text_cursor,
        )

    def cursor_position() -> Point | None:
        return _form_cursor_position(
            state,
            mode=mode,
            edit_field=edit_field,
            text_value=text_value,
            text_cursor=text_cursor,
        )

    app = Application(
        layout=Layout(
            Window(
                FormattedTextControl(
                    render,
                    focusable=True,
                    get_cursor_position=cursor_position,
                )
            )
        ),
        key_bindings=kb,
        style=_tui_style(),
        full_screen=False,
        mouse_support=False,
    )
    return app.run()


def _render_form_panel(
    state: WizardFormState,
    *,
    mode: str = "form",
    edit_field: str = "",
    choices: list[TuiChoice] | None = None,
    focused_choice_index: int = 0,
    selected_values: set[str] | None = None,
    text_value: str = "",
    text_cursor: int = 0,
) -> FormattedText:
    from prompt_toolkit.formatted_text import FormattedText

    choices = choices or []
    selected_values = selected_values or set()
    rendered: list[tuple[str, str]] = [
        ("class:title", "CodeAgent\n"),
        ("class:subtitle", "任务表单\n\n"),
    ]
    rows = state.visible_rows()
    state.cursor = max(0, min(state.cursor, len(rows) - 1))
    current_group: str | None = None
    for index, row in enumerate(rows):
        selected = index == state.cursor
        group_id = FIELD_GROUPS[row.row_id]
        if group_id != current_group:
            if current_group is not None:
                rendered.append(("", "\n"))
            current_group = group_id
            rendered.append(("class:section", f"{FORM_GROUPS[group_id]}\n"))

        if row.kind == "action":
            style = "class:active" if selected else "class:action"
            prefix = "> " if selected else "  "
            rendered.append((style, f"{prefix}  {row.label}\n"))
            continue

        prefix = "> " if selected else "  "
        label_style = "class:active" if selected else "class:label"
        value_style = "class:active" if selected else "class:value"
        rendered.append((label_style, f"{prefix}  {row.label}: "))
        if mode == "text" and row.row_id == edit_field:
            rendered.append(
                (
                    "class:input",
                    f"{text_value}\n",
                )
            )
        else:
            rendered.append((value_style, f"{row.value}\n"))

        if mode in {"select", "multi"} and row.row_id == edit_field:
            rendered.extend(
                _render_inline_choices(
                    choices=choices,
                    focused_index=focused_choice_index,
                    selected_values=(
                        {choices[focused_choice_index].value}
                        if mode == "select" and choices
                        else selected_values
                    ),
                    multi=(mode == "multi"),
                )
            )
        elif mode == "text" and row.row_id == edit_field:
            rendered.append(("class:help", "      Enter 保存，Esc 放弃修改。\n"))

    selected = state.selected_row()
    rendered.append(("", "\n"))
    rendered.append(("class:status", f"{state.status}\n"))
    if selected.row_id in FIELD_HELP:
        rendered.append(("class:help", f"{FIELD_HELP[selected.row_id]}\n"))
    return FormattedText(rendered)


def _form_cursor_position(
    state: WizardFormState,
    *,
    mode: str,
    edit_field: str,
    text_value: str,
    text_cursor: int,
):
    if mode != "text" or not edit_field:
        return None

    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.utils import get_cwidth

    rows = state.visible_rows()
    state.cursor = max(0, min(state.cursor, len(rows) - 1))
    y = 3
    current_group: str | None = None
    for index, row in enumerate(rows):
        group_id = FIELD_GROUPS[row.row_id]
        if group_id != current_group:
            if current_group is not None:
                y += 1
            current_group = group_id
            y += 1

        if row.row_id == edit_field:
            selected = index == state.cursor
            prefix = "> " if selected else "  "
            line_prefix = f"{prefix}  {row.label}: "
            cursor = max(0, min(text_cursor, len(text_value)))
            x = get_cwidth(line_prefix) + get_cwidth(text_value[:cursor])
            return Point(x=x, y=y)

        y += 1
    return None


def _render_inline_choices(
    *,
    choices: list[TuiChoice],
    focused_index: int,
    selected_values: set[str],
    multi: bool,
) -> list[tuple[str, str]]:
    if not choices:
        return [("class:warning", "      没有发现可选项。\n")]
    rendered: list[tuple[str, str]] = []
    for index, choice in enumerate(choices, start=1):
        zero_index = index - 1
        focused = zero_index == focused_index
        marker = "> " if focused else "  "
        style = "class:active" if focused else "class:help"
        if multi:
            checked = "[x]" if choice.value in selected_values else "[ ]"
            rendered.append((style, f"      {marker}{checked} {choice.title}\n"))
        else:
            rendered.append((style, f"      {marker}{index}. {choice.title}\n"))
    return rendered


def _render_choice_panel(
    *,
    title: str,
    choices: list[TuiChoice],
    focused_index: int,
    selected_values: set[str],
    multi: bool,
) -> FormattedText:
    from prompt_toolkit.formatted_text import FormattedText

    rendered: list[tuple[str, str]] = [
        ("class:title", "CodeAgent\n"),
        ("class:subtitle", f"{title}\n"),
        (
            "class:help",
            "上下键移动，Space 勾选，Enter 确认，Esc 返回。\n\n"
            if multi
            else "上下键移动，Enter 确认，Esc 返回。\n\n",
        ),
    ]
    if not choices:
        rendered.append(("class:warning", "没有发现可选项，请返回后手动填写路径。\n"))
        return FormattedText(rendered)
    for index, choice in enumerate(choices, start=1):
        zero_index = index - 1
        focused = zero_index == focused_index
        checked = "[x]" if choice.value in selected_values else "[ ]"
        marker = "> " if focused else "  "
        style = "class:active" if focused else "class:value"
        prefix = f"{marker}{checked} " if multi else f"{marker}{index}. "
        rendered.append((style, f"{prefix}{choice.title}\n"))
    return FormattedText(rendered)


def _render_text_panel(
    *,
    title: str,
    value: str,
    cursor: int,
    original: str,
) -> FormattedText:
    from prompt_toolkit.formatted_text import FormattedText

    cursor = max(0, min(cursor, len(value)))
    display = value[:cursor] + "|" + value[cursor:]
    rendered: list[tuple[str, str]] = [
        ("class:title", "CodeAgent\n"),
        ("class:subtitle", f"{title}\n"),
        ("class:help", "输入文字，Enter 保存，Esc 放弃修改。\n\n"),
        ("class:input", display or "|\n"),
    ]
    if original:
        rendered.append(("", "\n"))
        rendered.append(("class:help", f"原值：{original}\n"))
    return FormattedText(rendered)


def _tui_style() -> Style:
    from prompt_toolkit.styles import Style

    return Style.from_dict(
        {
            "title": "bold ansiwhite",
            "subtitle": "ansiwhite",
            "section": "ansicyan bold",
            "label": "ansiwhite",
            "value": "ansiwhite",
            "action": "ansiwhite bold",
            "active": "ansibrightblue bold",
            "status": "ansibrightblue bold",
            "help": "ansibrightblack",
            "warning": "ansiyellow",
            "input": "ansiwhite",
        }
    )


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
            marker = "> " if selected else "  "
            style = "class:active" if selected else "class:normal"
            rendered.append((style, f"{marker}{choice.title}\n"))
        return FormattedText(rendered)

    app = Application(
        layout=Layout(Window(FormattedTextControl(render, focusable=True))),
        key_bindings=kb,
        style=_tui_style(),
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
            checked = "[x]" if choice.value in selected else "[ ]"
            marker = "> " if focused else "  "
            style = "class:active" if focused else "class:normal"
            rendered.append((style, f"{marker}{checked} {choice.title}\n"))
        return FormattedText(rendered)

    app = Application(
        layout=Layout(Window(FormattedTextControl(render, focusable=True))),
        key_bindings=kb,
        style=_tui_style(),
        full_screen=False,
        mouse_support=False,
    )
    return app.run()
