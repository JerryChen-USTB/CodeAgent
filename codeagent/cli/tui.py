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
    "output_dir": "输出目录",
    "test_command": "测试命令",
    "model_name": "模型",
    "approval_mode": "审批模式",
    "start": "开始运行",
}

FIELD_HELP = {
    "stages": "选择 Agent 要执行的阶段组合。",
    "project_path": "Agent 将在这个项目目录中读写代码。",
    "input_materials": "逐项添加需求材料；可从候选列表选择，也可手动输入，并支持移除。",
    "output_dir": "运行产物、日志和报告会写入这个目录。",
    "test_command": "测试、调试和修复阶段用于验证的命令。",
    "model_name": "本次 wizard 运行使用的 OpenRouter 模型。",
    "approval_mode": "是否要求人工确认计划、补丁和命令。",
    "start": "校验表单并启动 CodeAgent。",
}

EDITABLE_FIELDS = tuple(field_id for field_id in FIELD_LABELS if field_id != "start")
_CHOICE_MODES = {
    "select",
    "materials",
    "material_source",
    "material_candidate",
    "material_remove",
}


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
            items = _material_values(value)
            return f"{len(items)} 项材料" if items else "<未添加>"
        if field_id == "approval_mode":
            return "开启人工审批" if value == "manual" else "关闭人工审批，自动批准"
        return str(value or "<空>")


def _material_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        material = str(item).strip()
        if material and material not in result:
            result.append(material)
    return result


def _append_material(materials: list[str], material: str) -> list[str]:
    normalized = material.strip()
    if not normalized or normalized in materials:
        return list(materials)
    return [*materials, normalized]


def _material_display(material: str) -> str:
    path = Path(material)
    name = path.name or material
    return f"{name} ({material})"


def _add_material_to_state(state: WizardFormState, material: str) -> None:
    materials = _append_material(
        _material_values(state.values.get("input_materials")),
        material,
    )
    state.set_value("input_materials", materials)


def _remove_material_from_state(state: WizardFormState, material: str) -> None:
    materials = [
        item
        for item in _material_values(state.values.get("input_materials"))
        if item != material
    ]
    state.set_value("input_materials", materials)


class TuiPromptDriver(Protocol):
    def select(self, title: str, choices: list[TuiChoice], *, default: str) -> str: ...

    def text(self, title: str, *, default: str = "") -> str: ...


class PromptToolkitTuiDriver:
    """Small prompt_toolkit-backed controls with Codex-like keyboard behavior."""

    def select(self, title: str, choices: list[TuiChoice], *, default: str) -> str:
        return _run_select_prompt(title, choices, default=default)

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
            _MATERIAL_ACTION_ADD,
            _MATERIAL_ACTION_DONE,
            _MATERIAL_ACTION_REMOVE,
            _MATERIAL_SOURCE_BACK,
            _MATERIAL_SOURCE_CANDIDATE,
            _MATERIAL_SOURCE_MANUAL,
            _approval_mode_choices,
            _discover_input_material_candidates,
            _format_selected_material,
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
            materials = _material_values(self.state.values.get(field_id))
            project_path = str(self.state.values.get("project_path") or ".")
            while True:
                action_choices = [
                    TuiChoice("添加材料", _MATERIAL_ACTION_ADD),
                    TuiChoice("完成材料选择", _MATERIAL_ACTION_DONE),
                ]
                if materials:
                    action_choices.insert(
                        1,
                        TuiChoice("移除材料", _MATERIAL_ACTION_REMOVE),
                    )
                action = self.driver.select(
                    "输入材料",
                    action_choices,
                    default=(
                        _MATERIAL_ACTION_DONE
                        if materials
                        else _MATERIAL_ACTION_ADD
                    ),
                )
                if action == _MATERIAL_ACTION_DONE:
                    self.state.set_value(field_id, materials)
                    return
                if action == _MATERIAL_ACTION_REMOVE:
                    removed = self.driver.select(
                        "选择要移除的材料",
                        [
                            TuiChoice(_format_selected_material(material), material)
                            for material in materials
                        ],
                        default=materials[0],
                    )
                    materials = [material for material in materials if material != removed]
                    continue
                source = self.driver.select(
                    "添加材料",
                    [
                        TuiChoice("从候选列表选择一项", _MATERIAL_SOURCE_CANDIDATE),
                        TuiChoice("手动输入一项材料路径", _MATERIAL_SOURCE_MANUAL),
                        TuiChoice("返回", _MATERIAL_SOURCE_BACK),
                    ],
                    default=_MATERIAL_SOURCE_CANDIDATE,
                )
                if source == _MATERIAL_SOURCE_BACK:
                    continue
                if source == _MATERIAL_SOURCE_MANUAL:
                    manual = self.driver.text("手动输入一项材料路径", default="").strip()
                    materials = _append_material(materials, manual)
                    continue
                candidates = [
                    TuiChoice(title, value)
                    for title, value in _discover_input_material_candidates(project_path)
                    if value not in materials
                ]
                if not candidates:
                    self.state.status = "没有发现新的候选材料，请手动输入路径。"
                    continue
                selected = self.driver.select(
                    "从候选列表选择一项材料",
                    candidates,
                    default=candidates[0].value,
                )
                materials = _append_material(materials, selected)
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
        values = self.state.values
        selected = _material_values(values.get("input_materials"))
        if not str(values.get("project_path") or "").strip():
            raise ValueError("项目目录不能为空")
        if not str(values.get("output_dir") or "").strip():
            raise ValueError("输出目录不能为空")
        if not str(values.get("test_command") or "").strip():
            raise ValueError("测试命令不能为空")
        return answer_type(
            stages=str(values["stages"]),
            project_path=str(values["project_path"]),
            input_material_paths=selected,
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
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.containers import DynamicContainer, Window
    from codeagent.cli.wizard import (
        _MATERIAL_ACTION_ADD,
        _MATERIAL_ACTION_DONE,
        _MATERIAL_ACTION_REMOVE,
        _MATERIAL_SOURCE_BACK,
        _MATERIAL_SOURCE_CANDIDATE,
        _MATERIAL_SOURCE_MANUAL,
        _approval_mode_choices,
        _discover_input_material_candidates,
        _model_choices,
        _parse_approval_mode,
        _parse_model_name,
        _parse_stages,
        _stage_choices,
    )

    mode = "form"
    edit_field = ""
    choices: list[TuiChoice] = []
    choice_index = 0
    text_original = ""
    edit_buffer = Buffer(multiline=False)
    edit_control = BufferControl(buffer=edit_buffer, focusable=True, focus_on_click=True)
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

    def enter_material_menu() -> None:
        nonlocal mode, edit_field, choices, choice_index
        mode = "materials"
        edit_field = "input_materials"
        materials = _material_values(state.values.get("input_materials"))
        choices = [
            TuiChoice("添加材料", _MATERIAL_ACTION_ADD),
            TuiChoice("完成材料选择", _MATERIAL_ACTION_DONE),
        ]
        if materials:
            choices.insert(1, TuiChoice("移除材料", _MATERIAL_ACTION_REMOVE))
        choice_index = 0
        state.status = "正在管理输入材料"

    def enter_material_source() -> None:
        nonlocal mode, choices, choice_index
        mode = "material_source"
        choices = [
            TuiChoice("从候选列表选择一项", _MATERIAL_SOURCE_CANDIDATE),
            TuiChoice("手动输入一项材料路径", _MATERIAL_SOURCE_MANUAL),
            TuiChoice("返回", _MATERIAL_SOURCE_BACK),
        ]
        choice_index = 0
        state.status = "请选择添加材料的方式"

    def enter_material_candidate() -> None:
        nonlocal mode, choices, choice_index
        mode = "material_candidate"
        selected = set(_material_values(state.values.get("input_materials")))
        project_path = str(state.values.get("project_path") or ".")
        choices = [
            TuiChoice(title, value)
            for title, value in _discover_input_material_candidates(project_path)
            if value not in selected
        ]
        if not choices:
            choices = [TuiChoice("没有发现新的候选材料，返回", _MATERIAL_SOURCE_BACK)]
        choice_index = 0
        state.status = "从候选列表添加一项材料"

    def enter_material_remove() -> None:
        nonlocal mode, choices, choice_index
        materials = _material_values(state.values.get("input_materials"))
        if not materials:
            state.status = "当前没有可移除的输入材料"
            enter_material_menu()
            return
        mode = "material_remove"
        choices = [TuiChoice(_material_display(material), material) for material in materials]
        choice_index = 0
        state.status = "选择要移除的输入材料"

    def enter_material_text() -> None:
        nonlocal mode, edit_field, text_original
        mode = "material_text"
        edit_field = "input_materials"
        text_original = ""
        edit_buffer.text = ""
        edit_buffer.cursor_position = 0
        state.status = "手动输入一项材料路径"
        get_app().layout.focus(edit_control)

    def enter_text(field_id: str) -> None:
        nonlocal mode, edit_field, text_original
        mode = "text"
        edit_field = field_id
        text_original = str(state.values.get(field_id) or "")
        edit_buffer.text = text_original
        edit_buffer.cursor_position = len(edit_buffer.text)
        state.status = f"正在填写：{FIELD_LABELS[field_id]}"
        get_app().layout.focus(edit_control)

    def begin_edit(field_id: str) -> None:
        if field_id == "stages":
            enter_select(field_id, "执行阶段", _stage_choices())
        elif field_id == "input_materials":
            enter_material_menu()
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
        except ValueError as exc:
            state.status = f"表单校验失败：{exc}"
            return False
        return True

    def commit_text() -> None:
        nonlocal mode
        state.set_value(edit_field, edit_buffer.text.strip())
        mode = "form"
        get_app().layout.focus(form_control)

    def cancel_edit() -> None:
        nonlocal mode
        mode = "form"
        state.status = "已返回任务表单。"
        get_app().layout.focus(form_control)

    def commit_choice() -> None:
        nonlocal mode
        if not choices:
            mode = "form"
            return
        state.set_value(edit_field, choices[choice_index].value)
        mode = "form"

    def commit_material_choice() -> None:
        nonlocal mode
        if not choices:
            enter_material_menu()
            return
        value = choices[choice_index].value
        if mode == "materials":
            if value == _MATERIAL_ACTION_ADD:
                enter_material_source()
            elif value == _MATERIAL_ACTION_REMOVE:
                enter_material_remove()
            elif value == _MATERIAL_ACTION_DONE:
                mode = "form"
                state.status = "已返回任务表单。"
            return
        if mode == "material_source":
            if value == _MATERIAL_SOURCE_CANDIDATE:
                enter_material_candidate()
            elif value == _MATERIAL_SOURCE_MANUAL:
                enter_material_text()
            else:
                enter_material_menu()
            return
        if mode == "material_candidate":
            if value != _MATERIAL_SOURCE_BACK:
                _add_material_to_state(state, value)
            enter_material_menu()
            return
        if mode == "material_remove":
            _remove_material_from_state(state, value)
            enter_material_remove()

    def commit_material_text() -> None:
        raw_path = edit_buffer.text.strip()
        if raw_path:
            _add_material_to_state(state, raw_path)
        else:
            state.status = "未填写材料路径，未添加。"
        enter_material_menu()
        get_app().layout.focus(form_control)

    @kb.add("up")
    def _(event) -> None:
        nonlocal choice_index
        if mode == "form":
            state.move(-1)
        elif mode in _CHOICE_MODES and choices:
            choice_index = (choice_index - 1) % len(choices)
        event.app.invalidate()

    @kb.add("down")
    def _(event) -> None:
        nonlocal choice_index
        if mode == "form":
            state.move(1)
        elif mode in _CHOICE_MODES and choices:
            choice_index = (choice_index + 1) % len(choices)
        event.app.invalidate()

    @kb.add(" ")
    def _(event) -> None:
        if mode == "form":
            row = current_row()
            if row.row_id == "input_materials":
                begin_edit(row.row_id)
        elif mode in _CHOICE_MODES:
            commit_material_choice() if mode != "select" else commit_choice()
        elif mode == "text":
            edit_buffer.insert_text(" ")
        elif mode == "material_text":
            edit_buffer.insert_text(" ")
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
        elif mode in _CHOICE_MODES:
            commit_material_choice()
        elif mode == "text":
            commit_text()
        elif mode == "material_text":
            commit_material_text()
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
        elif mode in {"material_source", "material_candidate", "material_remove", "material_text"}:
            enter_material_menu()
            event.app.layout.focus(form_control)
        else:
            cancel_edit()
        event.app.invalidate()

    def render() -> FormattedText:
        return _render_form_panel(
            state,
            mode=mode,
            edit_field=edit_field,
            choices=choices,
            focused_choice_index=choice_index,
            selected_values=set(),
            text_value=edit_buffer.text,
            text_cursor=edit_buffer.cursor_position,
        )

    def active_container():
        if mode in {"text", "material_text"}:
            return _build_wizard_text_container(
                state,
                mode=mode,
                edit_field=edit_field,
                edit_control=edit_control,
            )
        return Window(form_control)

    form_control = FormattedTextControl(render, focusable=True)
    app = Application(
        layout=Layout(DynamicContainer(active_container), focused_element=form_control),
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
        if row.row_id == "input_materials":
            rendered.append((label_style, f"{prefix}  {row.label}:\n"))
            rendered.extend(
                _render_material_list(
                    _material_values(state.values.get("input_materials"))
                )
            )
            if mode in _CHOICE_MODES - {"select"} and row.row_id == edit_field:
                rendered.extend(
                    _render_inline_choices(
                        choices=choices,
                        focused_index=focused_choice_index,
                        selected_values=set(),
                        multi=False,
                    )
                )
            elif mode == "material_text" and row.row_id == edit_field:
                rendered.append(("class:help", "      手动输入路径: "))
                rendered.append(("class:input", text_value))
                rendered.append(("", "\n"))
                rendered.append(("class:help", "      Enter 添加，Esc 返回。\n"))
            continue

        rendered.append((label_style, f"{prefix}  {row.label}: "))
        if mode == "text" and row.row_id == edit_field:
            rendered.append(("class:input", text_value))
            rendered.append(("", "\n"))
        else:
            rendered.append((value_style, f"{row.value}\n"))

        if mode == "select" and row.row_id == edit_field:
            rendered.extend(
                _render_inline_choices(
                    choices=choices,
                    focused_index=focused_choice_index,
                    selected_values=(
                        {choices[focused_choice_index].value}
                        if mode == "select" and choices
                        else selected_values
                    ),
                    multi=False,
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


def _build_wizard_text_container(
    state: WizardFormState,
    *,
    mode: str,
    edit_field: str,
    edit_control,
):
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.containers import HSplit, VSplit, Window
    from prompt_toolkit.utils import get_cwidth

    def line(fragments: list[tuple[str, str]] | None = None) -> Window:
        fragments = fragments or [("", "")]
        return Window(
            FormattedTextControl(FormattedText(fragments), focusable=False),
            height=1,
            dont_extend_height=True,
            wrap_lines=False,
        )

    def input_line(prefix_fragments: list[tuple[str, str]]) -> VSplit:
        label_text = "".join(fragment for _style, fragment in prefix_fragments)
        return VSplit(
            [
                Window(
                    FormattedTextControl(
                        FormattedText(prefix_fragments),
                        focusable=False,
                    ),
                    width=get_cwidth(label_text),
                    height=1,
                    dont_extend_height=True,
                    wrap_lines=False,
                ),
                Window(
                    edit_control,
                    height=1,
                    dont_extend_height=True,
                    wrap_lines=False,
                    style="class:input",
                ),
            ]
        )

    containers = [
        line([("class:title", "CodeAgent")]),
        line([("class:subtitle", "任务表单")]),
        line(),
    ]
    rows = state.visible_rows()
    state.cursor = max(0, min(state.cursor, len(rows) - 1))
    current_group: str | None = None
    for index, row in enumerate(rows):
        selected = index == state.cursor
        group_id = FIELD_GROUPS[row.row_id]
        if group_id != current_group:
            if current_group is not None:
                containers.append(line())
            current_group = group_id
            containers.append(line([("class:section", FORM_GROUPS[group_id])]))

        prefix = "> " if selected else "  "
        label_style = "class:active" if selected else "class:label"
        value_style = "class:active" if selected else "class:value"

        if row.kind == "action":
            style = "class:active" if selected else "class:action"
            containers.append(line([(style, f"{prefix}  {row.label}")]))
            continue

        if row.row_id == "input_materials":
            containers.append(line([(label_style, f"{prefix}  {row.label}:")]))
            materials = _material_values(state.values.get("input_materials"))
            if materials:
                for material_index, material in enumerate(materials, start=1):
                    containers.append(
                        line(
                            [
                                (
                                    "class:value",
                                    f"      {material_index}. {_material_display(material)}",
                                )
                            ]
                        )
                    )
            else:
                containers.append(line([("class:help", "      <未添加材料>")]))
            if mode == "material_text" and row.row_id == edit_field:
                containers.append(
                    input_line([("class:help", "      手动输入路径: ")])
                )
                containers.append(line([("class:help", "      Enter 添加，Esc 返回。")]))
            continue

        if mode == "text" and row.row_id == edit_field:
            containers.append(input_line([(label_style, f"{prefix}  {row.label}: ")]))
            containers.append(line([("class:help", "      Enter 保存，Esc 放弃修改。")]))
        else:
            containers.append(
                line(
                    [
                        (label_style, f"{prefix}  {row.label}: "),
                        (value_style, row.value),
                    ]
                )
            )

    selected = state.selected_row()
    containers.append(line())
    containers.append(line([("class:status", state.status)]))
    if selected.row_id in FIELD_HELP:
        containers.append(line([("class:help", FIELD_HELP[selected.row_id])]))
    return HSplit(containers)


def _render_material_list(materials: list[str]) -> list[tuple[str, str]]:
    if not materials:
        return [("class:help", "      <未添加材料>\n")]
    rendered: list[tuple[str, str]] = []
    for index, material in enumerate(materials, start=1):
        rendered.append(("class:value", f"      {index}. {_material_display(material)}\n"))
    return rendered


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
