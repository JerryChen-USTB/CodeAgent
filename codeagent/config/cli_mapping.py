"""Map CLI arguments into normalized TaskConfig objects."""

from __future__ import annotations

from pathlib import Path

from codeagent.config.loader import ConfigLoadError, load_task_config
from codeagent.config.schema import CommandConfig, TaskConfig


def task_config_from_run_options(
    *,
    config_path: str | Path | None = None,
    project: str | Path | None = None,
    stages: str | None = None,
    output_dir: str | Path | None = None,
    test_cmd: str | None = None,
) -> TaskConfig:
    """Load a config file or build one from non-interactive run options."""
    if config_path is not None:
        config = load_task_config(config_path)
        if output_dir is not None:
            config.output_dir = _resolve_output_dir(output_dir)
        config.mode = "run"
        return config
    if project is None:
        raise ConfigLoadError("请提供 --config 或 --project。")
    return _task_config_from_parts(
        stages=_split_stages(stages or "implement,test,debug,repair"),
        project=project,
        output_dir=output_dir,
        test_cmd=test_cmd,
        input_materials=[],
    )


def task_config_for_stage_command(
    *,
    stage: str,
    project: str | Path,
    output_dir: str | Path | None = None,
    test_cmd: str | None = None,
    requirements: str | Path | None = None,
    log: str | Path | None = None,
) -> TaskConfig:
    """Build a TaskConfig for a single stage subcommand."""
    input_materials: list[dict[str, object]] = []
    if requirements is not None:
        path = _resolve_existing_path(requirements, label="requirements")
        input_materials.append(
            {
                "type": "requirements",
                "path": path,
                "required": True,
                "multi": False,
                "description": "Requirements supplied by CLI.",
            }
        )
    if log is not None:
        path = _resolve_existing_path(log, label="log")
        input_materials.append(
            {
                "type": "error_log",
                "path": path,
                "required": True,
                "multi": True,
                "description": "Failure log supplied by CLI.",
            }
        )
    return _task_config_from_parts(
        stages=[stage],
        project=project,
        output_dir=output_dir,
        test_cmd=test_cmd,
        input_materials=input_materials,
    )


def _task_config_from_parts(
    *,
    stages: list[str],
    project: str | Path,
    output_dir: str | Path | None,
    test_cmd: str | None,
    input_materials: list[dict[str, object]],
) -> TaskConfig:
    project_path = _resolve_existing_path(project, label="project", must_be_dir=True)
    return TaskConfig(
        stages=stages,
        project_path=project_path,
        output_dir=_resolve_output_dir(output_dir) if output_dir is not None else None,
        input_materials=input_materials,
        test_command=CommandConfig(command=(test_cmd or "pytest -q").strip() or "pytest -q"),
        mode="run",
    )


def _split_stages(raw_stages: str) -> list[str]:
    normalized = raw_stages.replace(";", ",")
    if "," in normalized:
        parts = normalized.split(",")
    else:
        parts = normalized.split()
    stages = [part.strip() for part in parts if part.strip()]
    if not stages:
        raise ConfigLoadError("至少需要选择一个阶段。")
    return stages


def _resolve_existing_path(
    raw_path: str | Path,
    *,
    label: str,
    must_be_dir: bool = False,
) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise ConfigLoadError(f"{label} 路径不存在：{path}")
    if must_be_dir and not path.is_dir():
        raise ConfigLoadError(f"{label} 路径必须是目录：{path}")
    return path


def _resolve_output_dir(raw_path: str | Path) -> Path:
    return Path(raw_path).expanduser().resolve()
