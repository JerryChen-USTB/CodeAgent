"""Unified diff creation, validation, risk checks, and application."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from codeagent import filesystem as fs
from codeagent.context.sensitive_filter import SensitiveFilter


PatchLineKind = Literal["context", "add", "remove"]
RiskLevel = Literal["low", "medium", "high"]

HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
HARDCODE_RE = re.compile(
    r"\bif\s+[\w\.\[\]\(\)'\"]+\s*==\s*("
    r"\d+|True|False|None|'.*?'|\".*?\"|\[.*?\]|\{.*?\})"
)


class PatchValidationError(ValueError):
    """Raised when a patch is malformed or violates validation rules."""


class PatchApplyError(RuntimeError):
    """Raised when a valid patch cannot be applied to current file contents."""


@dataclass(frozen=True)
class FileChange:
    path: Path
    old_content: str | None
    new_content: str | None


@dataclass(frozen=True)
class PatchArtifact:
    text: str
    changed_files: list[str]


@dataclass(frozen=True)
class PatchRiskFinding:
    kind: str
    path: str
    message: str
    severity: RiskLevel


@dataclass(frozen=True)
class PatchRiskReport:
    level: RiskLevel = "low"
    findings: list[PatchRiskFinding] = field(default_factory=list)


@dataclass(frozen=True)
class PatchValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
    changed_files: list[str]
    file_count: int
    risk_report: PatchRiskReport


@dataclass(frozen=True)
class PatchSummary:
    changed_files: list[str]
    added_files: list[str]
    modified_files: list[str]
    deleted_files: list[str]
    added_lines: int
    removed_lines: int
    risk_level: RiskLevel


@dataclass(frozen=True)
class CodeChangeResult:
    operation_id: str
    applied: bool
    already_applied: bool
    changed_files: list[str]


@dataclass(frozen=True)
class PatchLine:
    kind: PatchLineKind
    text: str


@dataclass(frozen=True)
class PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[PatchLine]


@dataclass(frozen=True)
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list[PatchHunk]


@dataclass(frozen=True)
class PlannedFileChange:
    action: Literal["write", "delete", "already"]
    path: Path
    relative_path: str
    content: str | None = None


@dataclass(frozen=True)
class TextFileSnapshot:
    lines: list[str]
    newline: str = "\n"
    utf8_bom: bool = False


@dataclass(frozen=True)
class FileBackup:
    path: Path
    existed: bool
    content: bytes | None = None


class PatchService:
    """Create, validate, summarize, and apply unified diffs without Git."""

    def create_unified_diff(self, changes: list[FileChange]) -> PatchArtifact:
        diff_lines: list[str] = []
        changed_files: list[str] = []
        for change in changes:
            relative_path = _normalize_relative_path(change.path)
            old_lines = _split_content(change.old_content)
            new_lines = _split_content(change.new_content)
            fromfile = "/dev/null" if change.old_content is None else f"a/{relative_path}"
            tofile = "/dev/null" if change.new_content is None else f"b/{relative_path}"
            file_diff = list(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=fromfile,
                    tofile=tofile,
                    lineterm="",
                )
            )
            if file_diff:
                diff_lines.extend(file_diff)
                changed_files.append(relative_path)
        text = "\n".join(diff_lines)
        if text:
            text += "\n"
        return PatchArtifact(text=text, changed_files=changed_files)

    def validate_patch(
        self, patch_path: str | Path, project_root: str | Path
    ) -> PatchValidationResult:
        root = Path(project_root).resolve()
        errors: list[str] = []
        warnings: list[str] = []
        try:
            file_patches = self._parse_patch_file(patch_path)
        except PatchValidationError as exc:
            return PatchValidationResult(
                valid=False,
                errors=[str(exc)],
                warnings=[],
                changed_files=[],
                file_count=0,
                risk_report=PatchRiskReport(),
            )

        changed_files: list[str] = []
        seen_targets: set[str] = set()
        for file_patch in file_patches:
            old_path = _validate_patch_path(file_patch.old_path, root, errors)
            new_path = _validate_patch_path(file_patch.new_path, root, errors)
            if old_path and new_path and old_path != new_path:
                errors.append(f"renames are not supported: {old_path} -> {new_path}")
            target = new_path or old_path
            if target in seen_targets:
                errors.append(f"duplicate patch target: {target}")
                continue
            if target:
                seen_targets.add(target)
                changed_files.append(target)
        if len(changed_files) > 10:
            warnings.append("large patch modifies more than 10 files")
        risk_report = self._assess_risk(file_patches)
        return PatchValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            changed_files=changed_files,
            file_count=len(changed_files),
            risk_report=risk_report,
        )

    def summarize_patch(self, patch_path: str | Path) -> PatchSummary:
        file_patches = self._parse_patch_file(patch_path)
        changed_files = [_target_path(file_patch) for file_patch in file_patches]
        added_files = [
            _target_path(file_patch) for file_patch in file_patches if file_patch.old_path is None
        ]
        deleted_files = [
            _target_path(file_patch) for file_patch in file_patches if file_patch.new_path is None
        ]
        modified_files = [
            _target_path(file_patch)
            for file_patch in file_patches
            if file_patch.old_path is not None and file_patch.new_path is not None
        ]
        added_lines = sum(
            1
            for file_patch in file_patches
            for hunk in file_patch.hunks
            for line in hunk.lines
            if line.kind == "add"
        )
        removed_lines = sum(
            1
            for file_patch in file_patches
            for hunk in file_patch.hunks
            for line in hunk.lines
            if line.kind == "remove"
        )
        return PatchSummary(
            changed_files=changed_files,
            added_files=added_files,
            modified_files=modified_files,
            deleted_files=deleted_files,
            added_lines=added_lines,
            removed_lines=removed_lines,
            risk_level=self._assess_risk(file_patches).level,
        )

    def apply_patch(
        self, patch_path: str | Path, project_root: str | Path, operation_id: str
    ) -> CodeChangeResult:
        root = Path(project_root).resolve()
        validation = self.validate_patch(patch_path, root)
        if not validation.valid:
            raise PatchValidationError("; ".join(validation.errors))
        file_patches = self._parse_patch_file(patch_path)
        planned_changes = [
            self._plan_file_patch(file_patch, root) for file_patch in file_patches
        ]
        if all(change.action == "already" for change in planned_changes):
            return CodeChangeResult(
                operation_id=operation_id,
                applied=False,
                already_applied=True,
                changed_files=validation.changed_files,
            )
        self._preflight_planned_changes(planned_changes)
        backups = _snapshot_planned_changes(planned_changes)

        try:
            for change in planned_changes:
                if change.action == "already":
                    continue
                if change.action == "delete":
                    fs.unlink(change.path)
                elif change.action == "write":
                    fs.mkdir(change.path.parent)
                    fs.write_bytes(change.path, (change.content or "").encode("utf-8"))
        except OSError as exc:
            _rollback_file_changes(backups)
            raise PatchApplyError(f"failed to apply patch: {exc}") from exc
        return CodeChangeResult(
            operation_id=operation_id,
            applied=True,
            already_applied=False,
            changed_files=validation.changed_files,
        )

    def _preflight_planned_changes(
        self, planned_changes: list[PlannedFileChange]
    ) -> None:
        for change in planned_changes:
            if change.action == "already":
                continue
            _ensure_parent_can_exist(change.path, change.relative_path)
            if fs.exists(change.path) and fs.is_dir(change.path):
                raise PatchApplyError(
                    f"target path is a directory: {change.relative_path}"
                )

    def _parse_patch_file(self, patch_path: str | Path) -> list[FilePatch]:
        try:
            patch_text = fs.read_text(patch_path)
        except OSError as exc:
            raise PatchValidationError(f"patch file cannot be read: {exc}") from exc
        return _parse_patch_text(patch_text)

    def _assess_risk(self, file_patches: list[FilePatch]) -> PatchRiskReport:
        findings: list[PatchRiskFinding] = []
        if len(file_patches) > 10:
            findings.append(
                PatchRiskFinding(
                    kind="large_patch",
                    path="<multiple>",
                    message="patch modifies more than 10 files",
                    severity="high",
                )
            )
        for file_patch in file_patches:
            path = _safe_target_path(file_patch)
            if file_patch.new_path is None and _is_test_path(path):
                findings.append(
                    PatchRiskFinding(
                        kind="test_deletion",
                        path=path,
                        message="patch deletes a test file",
                        severity="high",
                    )
                )
            test_assertion_replaced = _is_test_path(path) and any(
                line.kind == "add" and _looks_like_assertion(line.text)
                for hunk in file_patch.hunks
                for line in hunk.lines
            )
            for hunk in file_patch.hunks:
                for line in hunk.lines:
                    if line.kind == "remove" and _is_test_path(path):
                        if _looks_like_assertion(line.text) and not test_assertion_replaced:
                            findings.append(
                                PatchRiskFinding(
                                    kind="test_assertion_removal",
                                    path=path,
                                    message="patch removes an assertion from a test",
                                    severity="high",
                                )
                            )
                    if line.kind == "add":
                        lower = line.text.lower()
                        if any(
                            marker in lower
                            for marker in (
                                "pytest.skip",
                                "@pytest.mark.skip",
                                "unittest.skip",
                                "xfail",
                            )
                        ):
                            findings.append(
                                PatchRiskFinding(
                                    kind="skip_or_xfail",
                                    path=path,
                                    message="patch adds skip or xfail behavior",
                                    severity="high",
                                )
                            )
                        if HARDCODE_RE.search(line.text):
                            severity: RiskLevel = "high" if _is_test_path(path) else "low"
                            findings.append(
                                PatchRiskFinding(
                                    kind="hardcoded_case",
                                    path=path,
                                    message=(
                                        "patch adds a literal equality branch; review for "
                                        "test-specific hardcoding"
                                    ),
                                    severity=severity,
                                )
                            )
        level: RiskLevel = "low"
        if any(finding.severity == "high" for finding in findings):
            level = "high"
        elif findings:
            level = "medium"
        return PatchRiskReport(level=level, findings=findings)

    def _plan_file_patch(self, file_patch: FilePatch, root: Path) -> PlannedFileChange:
        relative_path = _target_path(file_patch)
        target_path = (root / Path(relative_path)).resolve()
        if file_patch.old_path is None:
            new_content = _content_from_lines(_new_hunk_lines(file_patch))
            if fs.exists(target_path):
                current = _read_file_snapshot(target_path)
                if _is_already_applied(current.lines, file_patch):
                    return PlannedFileChange("already", target_path, relative_path)
                raise PatchApplyError(f"target file already exists: {relative_path}")
            return PlannedFileChange("write", target_path, relative_path, new_content)

        if file_patch.new_path is None:
            if not fs.exists(target_path):
                return PlannedFileChange("already", target_path, relative_path)
            current = _read_file_snapshot(target_path)
            remaining_lines = _apply_hunks(current.lines, file_patch)
            if remaining_lines:
                raise PatchApplyError("delete patch does not remove entire file")
            return PlannedFileChange("delete", target_path, relative_path)

        if not fs.exists(target_path):
            raise PatchApplyError(f"target file does not exist: {relative_path}")
        current = _read_file_snapshot(target_path)
        try:
            new_lines = _apply_hunks(current.lines, file_patch)
        except PatchApplyError:
            if _is_already_applied(current.lines, file_patch):
                return PlannedFileChange("already", target_path, relative_path)
            raise
        return PlannedFileChange(
            "write",
            target_path,
            relative_path,
            _content_from_lines(
                new_lines,
                newline=current.newline,
                utf8_bom=current.utf8_bom,
            ),
        )


def _parse_patch_text(patch_text: str) -> list[FilePatch]:
    lines = patch_text.splitlines()
    file_patches: list[FilePatch] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        if not lines[index].startswith("--- "):
            raise PatchValidationError(f"expected file header at line {index + 1}")
        old_path = _parse_file_label(lines[index][4:])
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise PatchValidationError(f"expected new file header at line {index + 1}")
        new_path = _parse_file_label(lines[index][4:])
        index += 1
        hunks: list[PatchHunk] = []
        while index < len(lines) and not lines[index].startswith("--- "):
            header = lines[index]
            match = HUNK_RE.match(header)
            if not match:
                raise PatchValidationError(f"expected hunk header at line {index + 1}")
            index += 1
            hunk_lines: list[PatchLine] = []
            while index < len(lines) and not lines[index].startswith(("@@ ", "--- ")):
                raw_line = lines[index]
                if raw_line.startswith("\\ No newline"):
                    index += 1
                    continue
                if not raw_line or raw_line[0] not in {" ", "+", "-"}:
                    raise PatchValidationError(
                        f"invalid patch line prefix at line {index + 1}"
                    )
                hunk_lines.append(_parse_patch_line(raw_line))
                index += 1
            old_count = int(match.group("old_count") or "1")
            new_count = int(match.group("new_count") or "1")
            old_side_count = sum(
                1 for line in hunk_lines if line.kind in {"context", "remove"}
            )
            new_side_count = sum(
                1 for line in hunk_lines if line.kind in {"context", "add"}
            )
            if old_side_count != old_count or new_side_count != new_count:
                raise PatchValidationError(
                    "hunk line count mismatch: "
                    f"header old/new={old_count}/{new_count}, "
                    f"body old/new={old_side_count}/{new_side_count}"
                )
            hunks.append(
                PatchHunk(
                    old_start=int(match.group("old_start")),
                    old_count=old_count,
                    new_start=int(match.group("new_start")),
                    new_count=new_count,
                    lines=hunk_lines,
                )
            )
        if not hunks:
            raise PatchValidationError("file patch has no hunks")
        file_patches.append(FilePatch(old_path=old_path, new_path=new_path, hunks=hunks))
    if not file_patches:
        raise PatchValidationError("patch is empty")
    return file_patches


def _parse_patch_line(raw_line: str) -> PatchLine:
    prefix = raw_line[0]
    text = raw_line[1:]
    if prefix == " ":
        return PatchLine("context", text)
    if prefix == "+":
        return PatchLine("add", text)
    return PatchLine("remove", text)


def _parse_file_label(label: str) -> str | None:
    normalized = label.strip().split("\t", 1)[0]
    if normalized == "/dev/null":
        return None
    if normalized.startswith(("a/", "b/")):
        normalized = normalized[2:]
    return normalized


def _validate_patch_path(
    raw_path: str | None, project_root: Path, errors: list[str]
) -> str | None:
    if raw_path is None:
        return None
    try:
        relative_path = _normalize_relative_path(Path(raw_path))
    except PatchValidationError as exc:
        errors.append(str(exc))
        return None
    target = (project_root / Path(relative_path)).resolve()
    if not _is_relative_to(target, project_root):
        errors.append(f"patch path outside project root: {raw_path}")
        return None
    if SensitiveFilter(project_root).is_denied(target):
        errors.append(f"patch targets sensitive or generated path: {relative_path}")
        return None
    return relative_path


def _normalize_relative_path(path: Path) -> str:
    raw = str(path).replace("\\", "/")
    posix_path = PurePosixPath(raw)
    parts = posix_path.parts
    if (
        not raw
        or posix_path.is_absolute()
        or any(part in {"", ".."} for part in parts)
        or (parts and ":" in parts[0])
    ):
        raise PatchValidationError(f"patch path outside project root: {raw}")
    return posix_path.as_posix()


def _target_path(file_patch: FilePatch) -> str:
    target = file_patch.new_path or file_patch.old_path
    if target is None:
        raise PatchValidationError("file patch has no target path")
    return _normalize_relative_path(Path(target))


def _safe_target_path(file_patch: FilePatch) -> str:
    target = file_patch.new_path or file_patch.old_path
    if target is None:
        return "<unknown>"
    try:
        return _normalize_relative_path(Path(target))
    except PatchValidationError:
        return target


def _split_content(content: str | None) -> list[str]:
    if content is None:
        return []
    return content.splitlines()


def _read_file_snapshot(path: Path) -> TextFileSnapshot:
    raw = fs.read_bytes(path)
    utf8_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if text.count("\r\n") > text.count("\n") - text.count("\r\n") else "\n"
    return TextFileSnapshot(lines=text.splitlines(), newline=newline, utf8_bom=utf8_bom)


def _content_from_lines(
    lines: list[str],
    *,
    newline: str = "\n",
    utf8_bom: bool = False,
) -> str:
    content = newline.join(lines) + (newline if lines else "")
    return ("\ufeff" + content) if utf8_bom else content


def _new_hunk_lines(file_patch: FilePatch) -> list[str]:
    return [
        line.text
        for hunk in file_patch.hunks
        for line in hunk.lines
        if line.kind in {"context", "add"}
    ]


def _apply_hunks(current_lines: list[str], file_patch: FilePatch) -> list[str]:
    output: list[str] = []
    cursor = 0
    for hunk in file_patch.hunks:
        old_index = max(hunk.old_start - 1, 0)
        if old_index < cursor:
            raise PatchApplyError("context mismatch: overlapping hunks")
        output.extend(current_lines[cursor:old_index])
        cursor = old_index
        for line in hunk.lines:
            if line.kind == "add":
                output.append(line.text)
                continue
            if cursor >= len(current_lines) or current_lines[cursor] != line.text:
                raise PatchApplyError("context mismatch while applying patch")
            if line.kind == "context":
                output.append(current_lines[cursor])
            cursor += 1
    output.extend(current_lines[cursor:])
    return output


def _is_already_applied(current_lines: list[str], file_patch: FilePatch) -> bool:
    for hunk in file_patch.hunks:
        new_index = max(hunk.new_start - 1, 0)
        for line in hunk.lines:
            if line.kind == "remove":
                continue
            if new_index >= len(current_lines) or current_lines[new_index] != line.text:
                return False
            new_index += 1
    return True


def _is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return "tests" in parts or PurePosixPath(path).name.startswith("test_")


def _looks_like_assertion(text: str) -> bool:
    stripped = text.strip()
    return (
        stripped.startswith("assert ")
        or stripped.startswith("self.assert")
        or stripped.startswith("with pytest.raises")
        or bool(re.search(r"\bassert[A-Z]\w*\(", stripped))
    )


def _ensure_parent_can_exist(path: Path, relative_path: str) -> None:
    parent = path.parent
    probe = parent
    while not fs.exists(probe):
        probe = probe.parent
    if not fs.is_dir(probe):
        raise PatchApplyError(f"parent path is not a directory: {relative_path}")


def _snapshot_planned_changes(
    planned_changes: list[PlannedFileChange],
) -> list[FileBackup]:
    backups: list[FileBackup] = []
    seen_paths: set[Path] = set()
    for change in planned_changes:
        if change.action == "already" or change.path in seen_paths:
            continue
        seen_paths.add(change.path)
        if fs.exists(change.path):
            backups.append(
                FileBackup(
                    path=change.path,
                    existed=True,
                    content=fs.read_bytes(change.path),
                )
            )
        else:
            backups.append(FileBackup(path=change.path, existed=False))
    return backups


def _rollback_file_changes(backups: list[FileBackup]) -> None:
    for backup in reversed(backups):
        try:
            if backup.existed:
                fs.mkdir(backup.path.parent)
                fs.write_bytes(backup.path, backup.content or b"")
            elif fs.exists(backup.path) and not fs.is_dir(backup.path):
                fs.unlink(backup.path)
        except OSError:
            continue


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
