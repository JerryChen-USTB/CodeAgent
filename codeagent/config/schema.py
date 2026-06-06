"""Pydantic schemas for CodeAgent configuration."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from codeagent.config import defaults
from codeagent.config.validators import validate_stage_sequence


class Stage(str, Enum):
    """Canonical workflow stages."""

    IMPLEMENT = "implement"
    TEST = "test"
    DEBUG = "debug"
    REPAIR = "repair"


class ModelConfig(BaseModel):
    provider: str = defaults.DEFAULT_MODEL_PROVIDER
    model_name: str = defaults.DEFAULT_MODEL_NAME
    base_url: str = defaults.DEFAULT_BASE_URL
    api_key_env: str = defaults.DEFAULT_API_KEY_ENV
    temperature: float = 0.2
    timeout_seconds: int = 120
    max_retries: int = 2
    max_tokens: int | None = defaults.DEFAULT_MODEL_MAX_TOKENS


class RuntimeConfig(BaseModel):
    checkpoint: Literal["sqlite", "memory"] = "sqlite"
    max_repair_attempts: int = defaults.DEFAULT_REPAIR_ATTEMPTS
    command_timeout_seconds: int = defaults.DEFAULT_COMMAND_TIMEOUT_SECONDS
    log_truncation_chars: int = defaults.DEFAULT_LOG_TRUNCATION_CHARS
    auto_approve_in_benchmark: bool = False


class PermissionsConfig(BaseModel):
    approval_mode: Literal["manual", "auto"] = "manual"
    require_approval_for: list[str] = Field(
        default_factory=lambda: ["test_plan", "patch_apply", "shell_command"]
    )
    skip_sensitive_files: bool = True


class InputMaterial(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    material_type: str = Field(alias="type")
    path: Path
    required: bool = False
    multi: bool = True
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_material_type_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict) and "material_type" in data and "type" not in data:
            data = dict(data)
            data["type"] = data["material_type"]
        return data


class CommandConfig(BaseModel):
    command: str = defaults.DEFAULT_TEST_COMMAND
    timeout_seconds: int = defaults.DEFAULT_COMMAND_TIMEOUT_SECONDS

    @model_validator(mode="before")
    @classmethod
    def accept_string_command(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"command": data}
        return data


class AgentVisibility(BaseModel):
    visible_paths: list[Path] = Field(default_factory=list)
    hidden_paths: list[Path] = Field(default_factory=list)


class ExecutionEnvironment(BaseModel):
    recommended: str | None = None
    conda_env: str | None = None
    reason: str | None = None


class TaskConfig(BaseModel):
    """Normalized task configuration used by CLI and benchmark cases."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_version: int | None = None
    task_id: str | None = None
    case_id: str | None = None
    title: str | None = None
    task_type: str | None = None
    stages: list[Stage]
    project_path: Path
    output_dir: Path | None = None
    input_materials: list[InputMaterial] = Field(default_factory=list)
    model: ModelConfig = Field(default_factory=ModelConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    test_command: CommandConfig = Field(default_factory=CommandConfig)
    prepare_command: CommandConfig | None = None
    language: Literal["python"] = defaults.DEFAULT_LANGUAGE
    test_framework: Literal["pytest", "unittest"] = defaults.DEFAULT_TEST_FRAMEWORK
    mode: Literal["wizard", "run", "benchmark"] = "run"
    max_repair_attempts: int = defaults.DEFAULT_REPAIR_ATTEMPTS
    command_timeout_seconds: int = defaults.DEFAULT_COMMAND_TIMEOUT_SECONDS
    log_truncation_chars: int = defaults.DEFAULT_LOG_TRUNCATION_CHARS
    auto_approve_in_benchmark: bool = False
    agent_visibility: AgentVisibility = Field(default_factory=AgentVisibility)
    execution_environment: ExecutionEnvironment | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_config_variants(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)

        project = normalized.get("project")
        if isinstance(project, dict):
            normalized.setdefault("project_path", project.get("path"))
            normalized.setdefault("test_framework", project.get("test_framework"))

        workspace = normalized.get("workspace")
        if isinstance(workspace, dict):
            normalized.setdefault("project_path", workspace.get("path"))

        if "model_config" in normalized and "model" not in normalized:
            normalized["model"] = normalized["model_config"]

        runtime = dict(normalized.get("runtime") or {})
        for key in (
            "max_repair_attempts",
            "command_timeout_seconds",
            "log_truncation_chars",
            "auto_approve_in_benchmark",
        ):
            if key in normalized and key not in runtime:
                runtime[key] = normalized[key]
        if runtime:
            normalized["runtime"] = runtime
        return normalized

    @field_validator("stages", mode="before")
    @classmethod
    def validate_stages(cls, value: Any) -> list[Stage]:
        return validate_stage_sequence(value)


class BenchmarkCaseConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    case_id: str
    config: Path
    enabled: bool = True
    difficulty: str | None = None
    project_type: str | None = None
    note: str | None = None


class BenchmarkConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int | None = None
    name: str | None = None
    benchmark_id: str | None = None
    description: str | None = None
    default_output_dir: Path = Path(defaults.DEFAULT_BENCHMARK_OUTPUT_DIR)
    output_dir: Path | None = None
    case_root: Path | None = None
    default_agent_visible_paths: list[Path] = Field(default_factory=list)
    default_hidden_paths: list[Path] = Field(default_factory=list)
    cases: list[BenchmarkCaseConfig] = Field(default_factory=list)
