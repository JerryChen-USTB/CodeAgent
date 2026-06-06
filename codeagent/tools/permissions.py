"""Tool permission classification for CodeAgent tool calls."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from codeagent.tools.registry import STAGES, create_default_tool_registry


PermissionAction = Literal["allow", "ask", "deny"]
RunMode = Literal["wizard", "run", "benchmark", "resume"]


READONLY_TOOLS = {
    "scan_project",
    "read_file",
    "search_code",
    "validate_patch",
    "summarize_patch",
    "parse_test_result",
}
PATCH_PRODUCING_TOOLS = {"create_unified_diff"}
SIDE_EFFECT_TOOLS = {"apply_patch", "run_shell", "write_project_file"}
OUTPUT_WRITE_TOOLS = {"write_report", "record_artifact"}


@dataclass(frozen=True)
class ToolCallContext:
    stage: str
    mode: RunMode
    output_dir: Path | None = None
    auto_approve_in_benchmark: bool = False


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    reason: str
    requires_approval: bool = False
    auto_approved: bool = False


class ToolPermissionPolicy:
    """Fail-closed policy for direct and registry-mediated tool calls."""

    def __init__(self, stage_tool_names: Mapping[str, set[str]] | None = None) -> None:
        if stage_tool_names is None:
            stage_tool_names = _default_stage_tool_names()
        self._stage_tool_names = {
            stage: set(tool_names) for stage, tool_names in stage_tool_names.items()
        }
        self._known_tools = set().union(*self._stage_tool_names.values())
        self._known_tools.update(SIDE_EFFECT_TOOLS | OUTPUT_WRITE_TOOLS)

    def classify(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ToolCallContext,
    ) -> PermissionDecision:
        if tool_name not in self._known_tools:
            return PermissionDecision(
                "deny",
                f"unregistered or unauthorized tool: {tool_name}",
            )
        stage_tools = self._stage_tool_names.get(context.stage)
        if stage_tools is None:
            return PermissionDecision("deny", f"unknown stage: {context.stage}")
        if tool_name not in stage_tools:
            return PermissionDecision(
                "deny",
                f"tool {tool_name} is not available in stage {context.stage}",
            )
        if tool_name in READONLY_TOOLS or tool_name in PATCH_PRODUCING_TOOLS:
            return PermissionDecision("allow", "readonly or patch-producing tool")
        if tool_name in OUTPUT_WRITE_TOOLS:
            return self._classify_output_write(tool_name, args, context)
        if tool_name in SIDE_EFFECT_TOOLS:
            if context.mode == "benchmark" and context.auto_approve_in_benchmark:
                return PermissionDecision(
                    "allow",
                    "benchmark mode auto approval enabled",
                    auto_approved=True,
                )
            return PermissionDecision(
                "ask",
                "side-effect tool requires approval",
                requires_approval=True,
            )
        return PermissionDecision(
            "deny",
            f"unregistered or unauthorized tool: {tool_name}",
        )

    def _classify_output_write(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ToolCallContext,
    ) -> PermissionDecision:
        if context.output_dir is None:
            return PermissionDecision(
                "deny",
                f"{tool_name} requires an output directory",
            )
        path_value = _first_path_arg(args)
        if path_value is None:
            return PermissionDecision("deny", f"{tool_name} requires a path argument")
        try:
            is_allowed_path = _is_under_output_dir(path_value, context.output_dir)
        except (OSError, TypeError, ValueError):
            return PermissionDecision("deny", f"{tool_name} received an invalid path")
        if is_allowed_path:
            return PermissionDecision("allow", "output write is inside output directory")
        return PermissionDecision(
            "deny",
            "output write target must be inside output directory",
        )


def _first_path_arg(args: dict[str, Any]) -> Any | None:
    for key in ("path", "report_path", "artifact_path"):
        if key in args:
            return args[key]
    return None


def _is_under_output_dir(path_value: Any, output_dir: Path) -> bool:
    if isinstance(path_value, str) and "\0" in path_value:
        raise ValueError("path contains NUL")
    candidate = Path(path_value)
    root = output_dir.resolve()
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (root / candidate).resolve()
    )
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True


def _default_stage_tool_names() -> dict[str, set[str]]:
    registry = create_default_tool_registry()
    return {stage: registry.tool_names_for_stage(stage) for stage in STAGES}
