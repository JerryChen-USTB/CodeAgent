from __future__ import annotations

from pathlib import Path

import pytest

from codeagent.cli.tui import (
    CodexLikeWizardSession,
    TuiApprovalConsole,
    TuiChoice,
    TuiProgressReporter,
    WizardFormState,
    _render_form_panel,
    _tui_style,
)
from codeagent.config import defaults
from codeagent.tools.hitl import ApprovalRequest


class ScriptedDriver:
    def __init__(
        self,
        *,
        selects: list[str] | None = None,
        multi_selects: list[list[str]] | None = None,
        texts: list[str] | None = None,
    ) -> None:
        self.selects = list(selects or [])
        self.multi_selects = list(multi_selects or [])
        self.texts = list(texts or [])
        self.seen_select_choices: list[list[str]] = []

    def select(self, title: str, choices: list[TuiChoice], *, default: str) -> str:
        self.seen_select_choices.append([choice.value for choice in choices])
        return self.selects.pop(0) if self.selects else default

    def multi_select(
        self,
        title: str,
        choices: list[TuiChoice],
        *,
        default: list[str],
    ) -> list[str]:
        return self.multi_selects.pop(0) if self.multi_selects else default

    def text(self, title: str, *, default: str = "") -> str:
        return self.texts.pop(0) if self.texts else default


def test_wizard_form_state_moves_toggles_and_updates_values() -> None:
    state = WizardFormState.create()

    assert state.render_value("model_name") == defaults.DEFAULT_MODEL_NAME
    rows = state.visible_rows()
    assert rows[0].kind == "group"
    assert any(row.row_id == "model_name" for row in rows)

    state.move(1)
    assert state.selected_row().row_id == "stages"
    state.toggle_group("model")
    assert all(row.row_id != "model_name" for row in state.visible_rows())
    state.toggle_group("model")
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
    assert "\n\n  输入材料" in text
    assert "    执行阶段:" in text
    assert "    开始运行 CodeAgent" in text


def test_codex_like_session_builds_answers_after_out_of_order_edits(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = tmp_path / "requirements.md"
    requirements.write_text("# Requirements\n", encoding="utf-8")
    driver = ScriptedDriver(
        selects=["openai/gpt-5.5", "auto"],
        multi_selects=[[str(requirements)]],
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
    assert defaults.WIZARD_MODEL_CHOICES == tuple(driver.seen_select_choices[0])


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
