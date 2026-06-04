"""Interactive wizard command and testable controller helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Protocol

import typer

from codeagent.config.schema import CommandConfig, PermissionsConfig, TaskConfig
from codeagent.reports.schemas import StageResult
from codeagent.reports.writer import ReportWriter
from codeagent.runtime.run_context import create_run_context


_MANUAL_MATERIAL_SENTINEL = "__codeagent_manual_input_material__"


@dataclass(frozen=True)
class WizardPromptAnswers:
    """Raw answers collected by the semi-interactive CLI wizard."""

    stages: str
    project_path: str
    input_material_paths: list[str] = field(default_factory=list)
    output_dir: str = "codeagent_runs"
    test_command: str = "pytest -q"
    approval_mode: str = "manual"


class WizardFormBackend(Protocol):
    def select(
        self,
        message: str,
        choices: list[tuple[str, str]],
        *,
        default: str,
    ) -> str: ...

    def checkbox(
        self,
        message: str,
        choices: list[tuple[str, str]],
        *,
        default: list[str] | None = None,
    ) -> list[str]: ...

    def text(self, message: str, *, default: str = "") -> str: ...

    def confirm(self, message: str, *, default: bool = True) -> bool: ...


def wizard_command(backend: WizardFormBackend | None = None) -> None:
    """Start the guided configuration wizard and run the agent directly."""
    backend = backend or _default_backend()
    try:
        answers = _collect_answers(backend)
        config = build_task_config_from_answers(answers)
    except ValueError as exc:
        typer.echo(f"向导输入无效：{exc}")
        raise typer.Exit(1) from exc

    typer.echo(render_task_summary(config))
    if not backend.confirm("确认以上表单并立即启动 Agent 吗？", default=True):
        run_dir = write_wizard_cancellation_report(
            config,
            reason="用户在任务摘要确认步骤取消。",
        )
        typer.echo(f"运行已取消。最终报告：{run_dir / 'final_report.md'}")
        raise typer.Exit()

    typer.echo("表单已确认，正在启动 CodeAgent...")
    result = _run_agent_from_wizard(config)
    typer.echo(f"运行已结束：{result.final_status}")
    typer.echo(f"运行目录：{result.run_dir}")
    if result.final_status != "succeeded":
        raise typer.Exit(1)
    raise typer.Exit()


def build_task_config_from_answers(answers: WizardPromptAnswers) -> TaskConfig:
    """Build a normalized TaskConfig from raw wizard answers."""
    stages = _parse_stages(answers.stages)
    project_path = _resolve_existing_path(
        answers.project_path,
        label="项目目录",
        must_be_dir=True,
    )
    output_dir = Path(answers.output_dir).expanduser().resolve()
    input_materials = []
    for raw_path in answers.input_material_paths:
        if not raw_path.strip():
            continue
        path = _resolve_existing_path(raw_path, label="输入材料路径")
        input_materials.append(
            {
                "type": "requirements",
                "path": path,
                "required": True,
                "multi": True,
                "description": "Collected by codeagent wizard.",
            }
        )

    test_command = answers.test_command.strip() or "pytest -q"
    return TaskConfig(
        stages=stages,
        project_path=project_path,
        output_dir=output_dir,
        input_materials=input_materials,
        test_command=CommandConfig(command=test_command),
        permissions=PermissionsConfig(
            approval_mode=_parse_approval_mode(answers.approval_mode)
        ),
        mode="wizard",
    )


def render_task_summary(config: TaskConfig) -> str:
    """Render the pre-execution summary required by the CLI SRS."""
    material_lines = [
        f"- {material.material_type}: {material.path}"
        for material in config.input_materials
    ]
    if not material_lines:
        material_lines = ["- <none>"]
    return "\n".join(
        [
            "任务摘要",
            "========",
            f"执行阶段：{', '.join(stage.value for stage in config.stages)}",
            f"项目目录：{config.project_path}",
            f"输出目录：{config.output_dir}",
            f"测试命令：{config.test_command.command}",
            "输入材料：",
            f"Approval mode: {config.permissions.approval_mode}",
            *material_lines,
        ]
    )


def write_wizard_cancellation_report(config: TaskConfig, *, reason: str) -> Path:
    """Initialize a run directory and write a final report for a cancelled wizard run."""
    context = create_run_context(config, output_root=config.output_dir)
    writer = ReportWriter(
        run_dir=context.run_dir,
        artifact_store=context.artifact_store,
        transcript=context.transcript,
        decision_trace=context.decision_trace,
    )
    now = datetime.now(timezone.utc).isoformat()
    result = StageResult(
        stage="wizard",
        status="cancelled",
        started_at=now,
        ended_at=now,
        summary=reason,
        next_suggestion="请重新运行 codeagent wizard，或使用 codeagent run --config。",
    )
    writer.write_stage_report(result)
    writer.write_final_report([result])
    return context.run_dir


def _collect_answers(backend: WizardFormBackend) -> WizardPromptAnswers:
    typer.echo("CodeAgent 中文任务表单")
    typer.echo("请按提示选择或填写字段。阶段和输入材料支持选择题/多选题。")
    stages = backend.select(
        "选择要执行的阶段组合",
        _stage_choices(),
        default="implement,test,debug,repair",
    )
    project_path = backend.text("项目目录", default=".")
    material_candidates = _discover_input_material_candidates(project_path)
    selected_materials = backend.checkbox(
        "选择输入材料（上下键移动，空格勾选，回车确认；找不到时选择“手动添加输入材料路径”）",
        material_candidates,
        default=[],
    )
    manual_requested = _MANUAL_MATERIAL_SENTINEL in selected_materials
    selected_materials = [
        material for material in selected_materials if material != _MANUAL_MATERIAL_SENTINEL
    ]
    manual_material = backend.text(
        (
            "请填写补充输入材料路径（多个路径用分号分隔）"
            if manual_requested
            else "补充输入材料路径（可选，多个路径用分号分隔）"
        ),
        default="",
    )
    if manual_requested and not manual_material.strip():
        raise ValueError("已选择手动添加输入材料路径，但没有填写路径")
    output_dir = backend.text("输出目录", default="codeagent_runs")
    test_command = backend.text("测试命令", default="pytest -q")
    approval_mode = backend.select(
        "Approval mode",
        _approval_mode_choices(),
        default="manual",
    )
    return WizardPromptAnswers(
        stages=stages,
        project_path=project_path,
        input_material_paths=[
            *selected_materials,
            *_split_manual_paths(manual_material),
        ],
        output_dir=output_dir,
        test_command=test_command,
        approval_mode=approval_mode,
    )


def _parse_stages(raw_stages: str) -> list[str]:
    normalized = raw_stages.replace(";", ",")
    if "," in normalized:
        parts = normalized.split(",")
    else:
        parts = normalized.split()
    stages = [part.strip() for part in parts if part.strip()]
    if not stages:
        raise ValueError("至少需要选择一个阶段")
    return stages


def _parse_approval_mode(raw_mode: str) -> str:
    mode = (raw_mode or "manual").strip().lower()
    aliases = {
        "1": "manual",
        "manual": "manual",
        "m": "manual",
        "y": "manual",
        "yes": "manual",
        "2": "auto",
        "auto": "auto",
        "a": "auto",
    }
    parsed = aliases.get(mode, mode)
    if parsed not in {"manual", "auto"}:
        raise ValueError(f"invalid approval mode: {raw_mode}")
    return parsed


def _resolve_existing_path(
    raw_path: str,
    *,
    label: str,
    must_be_dir: bool = False,
) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"{label} 不存在：{path}")
    if must_be_dir and not path.is_dir():
        raise ValueError(f"{label} 必须是目录：{path}")
    return path


def _run_agent_from_wizard(config: TaskConfig):
    from codeagent.cli.executor import execute_task_config
    from codeagent.cli.progress import ProgressReporter

    return execute_task_config(config, reporter=ProgressReporter())


class LineWizardBackend:
    """Scriptable fallback used for tests and non-interactive terminals."""

    def select(
        self,
        message: str,
        choices: list[tuple[str, str]],
        *,
        default: str,
    ) -> str:
        typer.echo(message)
        for index, (title, value) in enumerate(choices, start=1):
            marker = "（默认）" if value == default else ""
            typer.echo(f"  {index}. {title} [{value}]{marker}")
        typer.echo("> ", nl=False)
        raw = input().strip()
        if not raw:
            return default
        if raw.isdigit():
            index = int(raw) - 1
            if 0 <= index < len(choices):
                return choices[index][1]
        values = {value for _title, value in choices}
        if raw in values:
            return raw
        return raw

    def checkbox(
        self,
        message: str,
        choices: list[tuple[str, str]],
        *,
        default: list[str] | None = None,
    ) -> list[str]:
        typer.echo(message)
        if not choices:
            typer.echo("  <未发现可选输入材料，可在下一步手动填写>")
        for index, (title, value) in enumerate(choices, start=1):
            typer.echo(f"  {index}. {title} [{value}]")
        typer.echo("> ", nl=False)
        raw = input().strip()
        if not raw:
            return list(default or [])
        selected: list[str] = []
        values = {value for _title, value in choices}
        for part in _split_manual_paths(raw.replace(",", ";")):
            if part.isdigit():
                index = int(part) - 1
                if 0 <= index < len(choices):
                    selected.append(choices[index][1])
                continue
            if part in values or Path(part).expanduser().exists():
                selected.append(part)
        return selected

    def text(self, message: str, *, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        typer.echo(f"{message}{suffix}")
        typer.echo("> ", nl=False)
        raw = input()
        return default if raw == "" else raw

    def confirm(self, message: str, *, default: bool = True) -> bool:
        suffix = "Y/n" if default else "y/N"
        typer.echo(f"{message} [{suffix}]")
        typer.echo("> ", nl=False)
        try:
            raw = input().strip().lower()
        except EOFError:
            return default
        if not raw:
            return default
        return raw in {"y", "yes", "是", "确认", "1", "true"}


class QuestionaryWizardBackend:
    """Questionary-backed terminal form with arrow-key selection."""

    def __init__(self) -> None:
        import questionary

        self._questionary = questionary

    def select(
        self,
        message: str,
        choices: list[tuple[str, str]],
        *,
        default: str,
    ) -> str:
        q_choices = [
            self._questionary.Choice(title=title, value=value)
            for title, value in choices
        ]
        answer = self._questionary.select(
            message,
            choices=q_choices,
            default=default,
        ).ask()
        if answer is None:
            raise ValueError("用户取消了阶段选择")
        return str(answer)

    def checkbox(
        self,
        message: str,
        choices: list[tuple[str, str]],
        *,
        default: list[str] | None = None,
    ) -> list[str]:
        if not choices:
            return []
        default_values = set(default or [])
        q_choices = [
            self._questionary.Choice(
                title=title,
                value=value,
                checked=value in default_values,
            )
            for title, value in choices
        ]
        answer = self._questionary.checkbox(
            message,
            choices=q_choices,
            use_search_filter=True,
            use_jk_keys=False,
            instruction=(
                "（上下键移动，空格勾选/取消，输入文字搜索，回车确认；"
                "手动路径请选列表末尾选项）"
            ),
        ).ask()
        if answer is None:
            raise ValueError("用户取消了输入材料选择")
        return [str(item) for item in answer]

    def text(self, message: str, *, default: str = "") -> str:
        answer = self._questionary.text(message, default=default).ask()
        if answer is None:
            raise ValueError(f"用户取消了字段输入：{message}")
        return str(answer)

    def confirm(self, message: str, *, default: bool = True) -> bool:
        answer = self._questionary.confirm(message, default=default).ask()
        if answer is None:
            raise ValueError("用户取消了确认")
        return bool(answer)


def _default_backend() -> WizardFormBackend:
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            return QuestionaryWizardBackend()
        except Exception:
            return LineWizardBackend()
    return LineWizardBackend()


def _stage_choices() -> list[tuple[str, str]]:
    return [
        ("完整流水线：实现 + 测试 + 调试 + 修复", "implement,test,debug,repair"),
        ("实现 + 测试", "implement,test"),
        ("测试 + 调试 + 修复", "test,debug,repair"),
        ("测试 + 调试", "test,debug"),
        ("调试 + 修复", "debug,repair"),
        ("只执行实现", "implement"),
        ("只执行测试", "test"),
        ("只执行调试", "debug"),
        ("只执行修复", "repair"),
    ]


def _approval_mode_choices() -> list[tuple[str, str]]:
    return [
        ("开启人工审批：逐项确认方案、补丁和命令", "manual"),
        ("关闭人工审批：自动批准方案、补丁和命令", "auto"),
    ]


def _discover_input_material_candidates(project_path: str) -> list[tuple[str, str]]:
    roots: list[Path] = []
    candidate_project = Path(project_path).expanduser()
    cwd = Path.cwd().resolve()
    if candidate_project.exists():
        resolved_project = candidate_project.resolve()
        roots.append(resolved_project)
        if resolved_project != cwd and resolved_project.parent not in roots:
            roots.append(resolved_project.parent)
    if cwd not in roots:
        roots.append(cwd)
    seen: set[Path] = set()
    choices: list[tuple[str, str]] = []
    for root in roots:
        for path in _iter_material_candidates(root):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            choices.append((_format_material_choice_title(resolved), str(resolved)))
            if len(choices) >= 40:
                return _with_manual_material_choice(choices)
    return _with_manual_material_choice(choices)


def _with_manual_material_choice(choices: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [
        *choices,
        ("手动添加输入材料路径（下一步填写）", _MANUAL_MATERIAL_SENTINEL),
    ]


def _format_material_choice_title(path: Path) -> str:
    try:
        parent = path.parent.relative_to(Path.cwd().resolve())
        parent_text = str(parent) if str(parent) != "." else "."
    except ValueError:
        parent_text = str(path.parent)
    return f"{path.name} ({parent_text})"


def _sort_material_candidates(candidates: list[Path]) -> list[Path]:
    return sorted(candidates, key=_material_sort_key)


def _material_sort_key(path: Path) -> tuple[int, int, str]:
    name = path.name.lower()
    if name == "requirements.md":
        priority = 0
    elif "requirement" in name or "需求" in name:
        priority = 1
    elif name in {"task.yaml", "task.yml"}:
        priority = 2
    elif name in {"readme.md", "readme.txt"}:
        priority = 3
    else:
        priority = 10
    return (priority, len(path.parts), str(path).lower())


def _is_sensitive_material_candidate(path: Path) -> bool:
    name = path.name.lower()
    normalized = name.replace(" ", "_").replace("-", "_")
    sensitive_markers = {
        "software_engineering_project",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "private",
    }
    if name.startswith(".env") or name == "expected_result.json":
        return True
    return any(marker in normalized for marker in sensitive_markers)


def _iter_material_candidates(root: Path) -> list[Path]:
    suffixes = {".md", ".txt", ".yaml", ".yml", ".json"}
    denied_parts = {
        ".git",
        "__pycache__",
        "codeagent_runs",
        "benchmark",
        "oracle_tests",
        "evaluation",
        "node_modules",
        ".venv",
        "venv",
    }
    if not root.exists() or not root.is_dir():
        return []
    root = root.resolve()
    candidates: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > 3:
            continue
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for path in children:
            try:
                relative_parts = path.resolve().relative_to(root).parts
            except (OSError, ValueError):
                relative_parts = path.parts
            if any(part in denied_parts for part in relative_parts):
                continue
            if path.is_dir():
                stack.append((path, depth + 1))
                continue
            try:
                is_file = path.is_file()
            except OSError:
                continue
            if not is_file or path.suffix.lower() not in suffixes:
                continue
            if _is_sensitive_material_candidate(path):
                continue
            candidates.append(path)
            if len(candidates) >= 120:
                return _sort_material_candidates(candidates)
    return _sort_material_candidates(candidates)


def _split_manual_paths(raw: str) -> list[str]:
    normalized = raw.replace("\n", ";").replace(",", ";")
    return [part.strip() for part in normalized.split(";") if part.strip()]
