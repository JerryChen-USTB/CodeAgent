"""Load YAML/JSON configuration files into normalized schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from codeagent.config.schema import BenchmarkConfig, TaskConfig


class ConfigLoadError(ValueError):
    """Raised when a config file cannot be loaded or validated."""


def load_task_config(path: str | Path, *, validate_paths: bool = True) -> TaskConfig:
    """Load and normalize a task config from YAML or JSON."""
    config_path = Path(path).resolve()
    raw = _read_mapping(config_path)
    config = TaskConfig.model_validate(raw)
    _resolve_task_paths(config, config_path.parent)
    if validate_paths:
        _validate_task_paths(config)
    return config


def load_benchmark_config(
    path: str | Path, *, validate_case_configs: bool = True
) -> BenchmarkConfig:
    """Load and normalize a benchmark config from YAML or JSON."""
    config_path = Path(path).resolve()
    raw = _read_mapping(config_path)
    config = BenchmarkConfig.model_validate(raw)
    base_dir = config_path.parent

    config.default_output_dir = _resolve_path(base_dir, config.default_output_dir)
    if config.output_dir is not None:
        config.output_dir = _resolve_path(base_dir, config.output_dir)
    if config.case_root is not None:
        config.case_root = _resolve_path(base_dir, config.case_root)
    config.default_agent_visible_paths = [
        _resolve_path(base_dir, path) for path in config.default_agent_visible_paths
    ]
    config.default_hidden_paths = [
        _resolve_path(base_dir, path) for path in config.default_hidden_paths
    ]
    for case in config.cases:
        case.config = _resolve_path(base_dir, case.config)
        if validate_case_configs and not case.config.exists():
            raise ConfigLoadError(f"benchmark case config does not exist: {case.config}")
    return config


def _read_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigLoadError(f"config file does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        raise ConfigLoadError(f"unsupported config file extension: {path.suffix}")
    if not isinstance(data, dict):
        raise ConfigLoadError(f"config file must contain a mapping: {path}")
    return data


def _resolve_task_paths(config: TaskConfig, base_dir: Path) -> None:
    config.project_path = _resolve_path(base_dir, config.project_path)
    if config.output_dir is not None:
        config.output_dir = _resolve_path(base_dir, config.output_dir)
    for material in config.input_materials:
        material.path = _resolve_path(base_dir, material.path)
    config.agent_visibility.visible_paths = [
        _resolve_path(base_dir, path) for path in config.agent_visibility.visible_paths
    ]
    config.agent_visibility.hidden_paths = [
        _resolve_path(base_dir, path) for path in config.agent_visibility.hidden_paths
    ]


def _validate_task_paths(config: TaskConfig) -> None:
    if not config.project_path.exists():
        raise ConfigLoadError(f"project_path does not exist: {config.project_path}")
    for material in config.input_materials:
        if material.required and not material.path.exists():
            raise ConfigLoadError(
                f"required input material does not exist: {material.path}"
            )


def _resolve_path(base_dir: Path, path: Path) -> Path:
    return path if path.is_absolute() else (base_dir / path).resolve()
