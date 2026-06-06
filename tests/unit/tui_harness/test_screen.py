from __future__ import annotations

from tools.tui_harness.screen import parse_snapshot, strip_ansi


def test_parse_wizard_form_choices() -> None:
    screen = """
CodeAgent
任务表单

基础设置
>   执行阶段: implement,test,debug,repair
    项目目录: .

输入材料
    输入材料:
      <未添加材料>

运行策略
    输出目录: codeagent_runs
    测试命令: python -m pytest -q

模型与审批
    模型: anthropic/claude-sonnet-4.6
    审批模式: 开启人工审批

最终确认
    开始运行 CodeAgent
方向键移动，Enter 编辑，Space 展开选项或选择，Ctrl+S 开始运行。
"""

    snapshot = parse_snapshot(screen)

    assert snapshot.prompt_kind == "wizard_form"
    assert [choice.label for choice in snapshot.choices] == [
        "执行阶段",
        "项目目录",
        "输入材料",
        "输出目录",
        "测试命令",
        "模型",
        "审批模式",
        "开始运行 CodeAgent",
    ]
    assert snapshot.choices[0].selected is True
    assert snapshot.suggested_actions == ["select-label", "keys"]


def test_parse_inline_select_choices_inside_wizard() -> None:
    screen = """
CodeAgent
任务表单

输入材料
>   输入材料:
      <未添加材料>
      > 1. 添加材料
        2. 完成材料选择

正在管理输入材料
"""

    snapshot = parse_snapshot(screen)

    assert snapshot.prompt_kind == "select"
    assert [(choice.label, choice.selected) for choice in snapshot.choices] == [
        ("添加材料", True),
        ("完成材料选择", False),
    ]


def test_parse_approval_context_files_and_choices() -> None:
    screen = """
请先审查以下文件：
- test_app.py (tests/test_app.py)
- test_models.py (tests/test_models.py)
当前动作：只审查补丁；同意后才会修改项目文件。

应用这个单文件实现补丁了？
上下键移动，回车选中。

> 是，应用此补丁
  是，应用此补丁，本阶段不再提示
  否，告知 CodeAgent 如何调整
"""

    snapshot = parse_snapshot(screen)

    assert snapshot.prompt_kind == "approval"
    assert snapshot.context_files == ["tests/test_app.py", "tests/test_models.py"]
    assert [choice.label for choice in snapshot.choices] == [
        "是，应用此补丁",
        "是，应用此补丁，本阶段不再提示",
        "否，告知 CodeAgent 如何调整",
    ]
    assert "approve-rest" in snapshot.suggested_actions


def test_parse_command_approval() -> None:
    screen = """
将执行命令：
- python -m pytest -q
工作目录：
- D:/Projects/CodeAgent/demo/workspace
当前动作：同意后会在项目目录中执行命令。

运行此测试命令？
上下键移动，回车选中。

> 是，运行命令
  否，修改命令
  取消本次运行
"""

    snapshot = parse_snapshot(screen)

    assert snapshot.prompt_kind == "approval"
    assert snapshot.command == "python -m pytest -q"
    assert snapshot.choices[0].label == "是，运行命令"


def test_strip_ansi_removes_osc_links_and_csi_codes() -> None:
    text = "\x1b[31mred\x1b[0m \x1b]8;;file:///tmp/a.py\x1b\\a.py\x1b]8;;\x1b\\"

    assert strip_ansi(text) == "red a.py"
