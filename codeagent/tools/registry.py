"""Tool registration and stage-scoped exposure."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal


ToolCategory = Literal["readonly", "patch_producing", "side_effect", "output_write"]
STAGES = ("implement", "test", "debug", "repair")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: ToolCategory
    stages: tuple[str, ...]
    description: str = ""
    handler: Callable[..., Any] | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"tool is not registered: {name}") from exc

    def get_stage_tools(self, stage: str) -> list[ToolSpec]:
        if stage not in STAGES:
            raise ValueError(f"unknown stage: {stage}")
        return [
            spec
            for spec in self._tools.values()
            if stage in spec.stages or "all" in spec.stages
        ]

    def tool_names_for_stage(self, stage: str) -> set[str]:
        return {spec.name for spec in self.get_stage_tools(stage)}

    def get_readonly_tool_names(self) -> set[str]:
        return {
            spec.name
            for spec in self._tools.values()
            if spec.category in {"readonly", "patch_producing"}
        }

    def get_side_effect_tool_names(self) -> set[str]:
        return {
            spec.name
            for spec in self._tools.values()
            if spec.category == "side_effect"
        }


def create_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for spec in _default_specs():
        registry.register(spec)
    return registry


def _default_specs() -> tuple[ToolSpec, ...]:
    all_stages = ("all",)
    patch_stages = ("implement", "test", "repair")
    test_run_stages = ("test", "debug", "repair")
    return (
        ToolSpec("scan_project", "readonly", all_stages, "scan project structure"),
        ToolSpec("read_file", "readonly", all_stages, "read visible project file"),
        ToolSpec("search_code", "readonly", all_stages, "search visible project code"),
        ToolSpec(
            "create_unified_diff",
            "patch_producing",
            patch_stages,
            "create a unified diff artifact",
        ),
        ToolSpec("validate_patch", "readonly", patch_stages, "validate a patch"),
        ToolSpec("summarize_patch", "readonly", patch_stages, "summarize a patch"),
        ToolSpec("apply_patch", "side_effect", patch_stages, "apply an approved patch"),
        ToolSpec("run_shell", "side_effect", test_run_stages, "run an approved command"),
        ToolSpec(
            "parse_test_result",
            "readonly",
            test_run_stages,
            "parse pytest or unittest output",
        ),
        ToolSpec("write_report", "output_write", all_stages, "write a run report"),
        ToolSpec("record_artifact", "output_write", all_stages, "record an artifact"),
    )
