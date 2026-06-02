"""Patch tool wrappers around :mod:`codeagent.services.patch_service`."""

from __future__ import annotations

from pathlib import Path

from codeagent.services.patch_service import (
    CodeChangeResult,
    FileChange,
    PatchArtifact,
    PatchService,
    PatchSummary,
    PatchValidationResult,
)


def create_unified_diff(changes: list[FileChange]) -> PatchArtifact:
    return PatchService().create_unified_diff(changes)


def validate_patch(
    patch_path: str | Path, project_root: str | Path
) -> PatchValidationResult:
    return PatchService().validate_patch(patch_path, project_root)


def summarize_patch(patch_path: str | Path) -> PatchSummary:
    return PatchService().summarize_patch(patch_path)


def apply_patch(
    patch_path: str | Path, project_root: str | Path, operation_id: str
) -> CodeChangeResult:
    return PatchService().apply_patch(patch_path, project_root, operation_id)
