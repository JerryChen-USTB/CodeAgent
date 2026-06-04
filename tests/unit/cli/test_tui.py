from __future__ import annotations

from pathlib import Path

import pytest

import codeagent.cli.tui as tui
from codeagent.cli.tui import (
    CodexLikeWizardSession,
    TuiApprovalConsole,
    TuiChoice,
    TuiProgressReporter,
    WizardFormState,
    _allow_blinking_cursor,
    _build_wizard_text_container,
    _render_form_panel,
    _tui_style,
    _wizard_text_fragments,
    _wizard_text_prefix_width,
)
from codeagent.config import defaults
from codeagent.tools.hitl import ApprovalRequest


class ScriptedDriver:
    def __init__(
        self,
        *,
        selects: list[str] | None = None,
        texts: list[str] | None = None,
    ) -> None:
        self.selects = list(selects or [])
        self.texts = list(texts or [])
        self.seen_select_choices: list[list[str]] = []

    def select(self, title: str, choices: list[TuiChoice], *, default: str) -> str:
        self.seen_select_choices.append([choice.value for choice in choices])
        return self.selects.pop(0) if self.selects else default

    def text(self, title: str, *, default: str = "") -> str:
        return self.texts.pop(0) if self.texts else default


def test_wizard_form_state_moves_between_fields_and_updates_values() -> None:
    state = WizardFormState.create()

    assert state.render_value("model_name") == defaults.DEFAULT_MODEL_NAME
    rows = state.visible_rows()
    assert rows[0].row_id == "stages"
    assert all(row.kind != "group" for row in rows)
    assert any(row.row_id == "model_name" for row in rows)

    state.move(1)
    assert state.selected_row().row_id == "project_path"
    state.set_value("model_name", "openai/gpt-5.5")

    assert state.render_value("model_name") == "openai/gpt-5.5"
    assert "模型" in state.status


def test_form_rendering_uses_stable_ascii_layout_without_internal_tagline() -> None:
    state = WizardFormState.create()
    rendered = _render_form_panel(state)
    text = "".join(fragment for _style, fragment in rendered)

    assert "\u25be" not in text
    assert "\u25b8" not in text
    assert "\u25b6" not in text
    assert ("Codex 风格 TUI：" + "像填问卷一样修改字段") not in text
    assert "\n\n输入材料" in text
    assert ">   执行阶段:" in text
    assert "    开始运行 CodeAgent" in text


def test_form_rendering_expands_choices_inline_under_the_active_field() -> None:
    state = WizardFormState.create()
    rows = state.visible_rows()
    state.cursor = next(index for index, row in enumerate(rows) if row.row_id == "model_name")

    rendered = _render_form_panel(
        state,
        mode="select",
        edit_field="model_name",
        choices=[
            TuiChoice("anthropic/claude-sonnet-4.6", "anthropic/claude-sonnet-4.6"),
            TuiChoice("openai/gpt-5.5", "openai/gpt-5.5"),
        ],
        focused_choice_index=1,
    )
    text = "".join(fragment for _style, fragment in rendered)

    assert "模型与审批\n>   模型: anthropic/claude-sonnet-4.6\n" in text
    assert "      > 2. openai/gpt-5.5\n" in text
    assert "    审批模式:" in text


def test_form_rendering_shows_input_materials_as_vertical_list() -> None:
    state = WizardFormState.create()
    state.values["input_materials"] = [
        r"D:\demo\requirements.md",
        r"D:\demo\api.md",
    ]

    rendered = _render_form_panel(state)
    text = "".join(fragment for _style, fragment in rendered)

    assert "输入材料:\n" in text
    assert "      1. requirements.md (D:\\demo\\requirements.md)\n" in text
    assert "      2. api.md (D:\\demo\\api.md)\n" in text
    assert "requirements.md;api.md" not in text


def test_text_editing_container_uses_real_buffer_control() -> None:
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.layout.controls import BufferControl
    from prompt_toolkit.layout.containers import HSplit, VSplit, Window

    state = WizardFormState.create()
    rows = state.visible_rows()
    state.cursor = next(index for index, row in enumerate(rows) if row.row_id == "output_dir")
    buffer = Buffer(multiline=False)
    buffer.text = "codeagent_runs"
    buffer.cursor_position = len(buffer.text)
    control = BufferControl(buffer=buffer)

    container = _build_wizard_text_container(
        state,
        mode="text",
        edit_field="output_dir",
        edit_control=control,
    )

    def contains_buffer_control(node: object) -> bool:
        if isinstance(node, Window):
            return node.content is control
        if isinstance(node, (HSplit, VSplit)):
            return any(contains_buffer_control(child) for child in node.children)
        return False

    assert contains_buffer_control(container)


def test_text_editing_fragments_split_active_field_into_stable_regions() -> None:
    state = WizardFormState.create()
    rows = state.visible_rows()
    state.cursor = next(index for index, row in enumerate(rows) if row.row_id == "project_path")

    before, prefix, after = _wizard_text_fragments(
        state,
        mode="text",
        edit_field="project_path",
    )

    before_text = "".join(fragment for _style, fragment in before)
    prefix_text = "".join(fragment for _style, fragment in prefix)
    after_text = "".join(fragment for _style, fragment in after)

    assert before_text.endswith("执行阶段: implement,test,debug,repair\n")
    assert prefix_text == ">   项目目录: "
    assert after_text.startswith("      Enter 保存，Esc 放弃修改。\n")
    assert "输出目录:" in after_text


def test_text_prefix_width_uses_terminal_display_width_for_chinese() -> None:
    from prompt_toolkit.utils import get_cwidth

    state = WizardFormState.create()
    rows = state.visible_rows()
    state.cursor = next(index for index, row in enumerate(rows) if row.row_id == "project_path")

    width = _wizard_text_prefix_width(
        state,
        "text",
        "project_path",
    )

    assert width == get_cwidth(">   项目目录: ")


def test_material_text_fragments_keep_material_list_before_input() -> None:
    state = WizardFormState.create()
    state.values["input_materials"] = ["requirements.md", "api.md"]
    rows = state.visible_rows()
    state.cursor = next(index for index, row in enumerate(rows) if row.row_id == "input_materials")

    before, prefix, after = _wizard_text_fragments(
        state,
        mode="material_text",
        edit_field="input_materials",
    )

    before_text = "".join(fragment for _style, fragment in before)
    prefix_text = "".join(fragment for _style, fragment in prefix)
    after_text = "".join(fragment for _style, fragment in after)

    assert "      1. requirements.md (requirements.md)\n" in before_text
    assert "      2. api.md (api.md)\n" in before_text
    assert prefix_text == "      手动输入路径: "
    assert after_text.startswith("      Enter 添加，Esc 返回。\n")


def test_vt_output_show_cursor_keeps_blinking_enabled() -> None:
    Vt100Output = type("Vt100_Output", (), {})
    output = Vt100Output()
    output.raw = []
    output._cursor_visible = None

    def write_raw(value: str) -> None:
        output.raw.append(value)

    output.write_raw = write_raw

    _allow_blinking_cursor(output)
    output.show_cursor()

    assert output.raw == ["\x1b[?12h\x1b[?25h"]
    assert output._cursor_visible is True


def test_windows10_output_show_cursor_keeps_blinking_enabled() -> None:
    Windows10Output = type("Windows10_Output", (), {})
    output = Windows10Output()
    output.raw = []
    output._cursor_visible = None

    def write_raw(value: str) -> None:
        output.raw.append(value)

    output.write_raw = write_raw

    _allow_blinking_cursor(output)
    output.show_cursor()

    assert output.raw == ["\x1b[?12h\x1b[?25h"]


def test_win32_output_show_cursor_uses_vt_when_available(monkeypatch) -> None:
    Win32Output = type("Win32Output", (), {})
    output = Win32Output()
    output.raw = []
    output._cursor_visible = None

    def write_raw(value: str) -> None:
        output.raw.append(value)

    output.write_raw = write_raw
    monkeypatch.setattr(
        tui,
        "_enable_windows_virtual_terminal_processing",
        lambda candidate: candidate is output,
    )

    _allow_blinking_cursor(output)
    output.show_cursor()

    assert output.raw == ["\x1b[?12h\x1b[?25h"]
    assert not hasattr(output, "set_cursor_shape")


def test_non_vt_output_cursor_policy_is_unchanged() -> None:
    class FakeOutput:
        def show_cursor(self) -> None:
            pass

    output = FakeOutput()
    original = output.show_cursor

    _allow_blinking_cursor(output)

    assert output.show_cursor == original


def test_codex_like_session_builds_answers_after_out_of_order_edits(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = tmp_path / "requirements.md"
    requirements.write_text("# Requirements\n", encoding="utf-8")
    driver = ScriptedDriver(
        selects=[
            "openai/gpt-5.5",
            "auto",
            "add",
            "candidate",
            str(requirements),
            "done",
        ],
        texts=[str(project), str(tmp_path / "runs"), "python -m pytest -q"],
    )
    session = CodexLikeWizardSession(driver=driver)
    actions = iter(
        [
            ("edit", "model_name"),
            ("edit", "approval_mode"),
            ("edit", "project_path"),
            ("edit", "input_materials"),
            ("edit", "output_dir"),
            ("edit", "test_command"),
            ("start", "start"),
        ]
    )
    monkeypatch.setattr(session, "_run_form_once", lambda: next(actions))

    answers = session.run()

    assert answers is not None
    assert answers.model_name == "openai/gpt-5.5"
    assert answers.approval_mode == "auto"
    assert answers.project_path == str(project)
    assert answers.input_material_paths == [str(requirements)]
    assert answers.output_dir == str(tmp_path / "runs")
    assert answers.test_command == "python -m pytest -q"
    assert any(
        defaults.WIZARD_MODEL_CHOICES == tuple(choices)
        for choices in driver.seen_select_choices
    )


def test_material_manager_adds_manual_items_and_removes_existing_item(tmp_path) -> None:
    first = tmp_path / "requirements.md"
    second = tmp_path / "api.md"
    first.write_text("# Requirements\n", encoding="utf-8")
    second.write_text("# API\n", encoding="utf-8")
    driver = ScriptedDriver(
        selects=[
            "add",
            "manual",
            "add",
            "manual",
            "remove",
            str(first),
            "done",
        ],
        texts=[str(first), str(second)],
    )
    session = CodexLikeWizardSession(driver=driver)

    session._edit_field("input_materials")

    assert session.state.values["input_materials"] == [str(second)]


def test_codex_like_session_keeps_user_in_form_on_validation_error(monkeypatch) -> None:
    session = CodexLikeWizardSession(driver=ScriptedDriver())
    actions = iter([("edit", "project_path"), ("start", "start"), ("cancel", "cancel")])
    monkeypatch.setattr(session, "_run_form_once", lambda: next(actions))
    session.state.values["project_path"] = ""

    result = session.run()

    assert result is None
    assert "表单校验失败" in session.state.status


def test_tui_approval_console_uses_two_option_plan_prompt_and_requires_feedback() -> None:
    request = ApprovalRequest(
        interrupt_id="testing_plan",
        action="review_test_plan",
        title="实施此测试计划？",
        payload={"plan_path": "testing/test_plan.md"},
        risk_level="low",
        allowed_decisions=("approve", "respond"),
        default_decision="approve",
    )
    driver = ScriptedDriver(selects=["respond"], texts=["请增加边界测试。"])

    decision = TuiApprovalConsole(driver=driver).prompt(request)

    assert decision.decision_type == "respond"
    assert decision.comment == "请增加边界测试。"
    assert driver.seen_select_choices == [["approve", "respond"]]


def test_tui_approval_console_rejects_empty_feedback() -> None:
    request = ApprovalRequest(
        interrupt_id="implementation_patch",
        action="approve_implementation_patch",
        title="应用此实现补丁？",
        payload={"patch_path": "implementation/implementation.patch.diff"},
        risk_level="medium",
        allowed_decisions=("approve", "respond"),
        default_decision="approve",
    )

    with pytest.raises(ValueError, match="必须填写具体意见"):
        TuiApprovalConsole(driver=ScriptedDriver(selects=["respond"], texts=[""])).prompt(
            request
        )


def test_tui_progress_reporter_renders_chinese_event_lines() -> None:
    from rich.console import Console

    console = Console(record=True, force_terminal=False)
    reporter = TuiProgressReporter(console=console)

    line = reporter.render_event(
        {
            "type": "test_result",
            "passed": 3,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "total": 3,
        }
    )

    assert "3 passed" in line
    assert "│ [测试结果]" in console.export_text()


def test_model_choices_are_fixed_for_wizard() -> None:
    assert defaults.WIZARD_MODEL_CHOICES == (
        "anthropic/claude-opus-4.8",
        "anthropic/claude-sonnet-4.6",
        "openai/gpt-5.5",
        "google/gemini-3.5-flash",
        "deepseek/deepseek-v4-pro",
        "minimax/minimax-m3",
        "qwen/qwen3.7-max",
    )


def test_tui_style_uses_prompt_toolkit_supported_color_names() -> None:
    style = _tui_style()

    assert style is not None
    assert ("rev" + "erse") not in str(style.style_rules)
    assert "selected" not in str(style.style_rules)
    assert "active" in str(style.style_rules)
