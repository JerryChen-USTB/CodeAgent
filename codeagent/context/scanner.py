"""Project structure scanner."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from codeagent.context.path_utils import safe_walk
from codeagent.context.sensitive_filter import SensitiveFilter


PYTHON_SOURCE_DIRS = {"src", "app", "codeagent"}
TEST_DIR_NAMES = {"tests", "test"}
CONFIG_FILENAMES = {
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "pytest.ini",
    "mypy.ini",
}
DEPENDENCY_FILENAMES = {
    "requirements.txt",
    "requirements-dev.txt",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
}


@dataclass(frozen=True)
class SkippedPath:
    path: Path
    reason: str


@dataclass(frozen=True)
class ProjectProfile:
    root: Path
    source_files: list[Path] = field(default_factory=list)
    test_files: list[Path] = field(default_factory=list)
    config_files: list[Path] = field(default_factory=list)
    dependency_files: list[Path] = field(default_factory=list)
    skipped_paths: list[SkippedPath] = field(default_factory=list)


class ProjectScanner:
    def scan(self, root: str | Path) -> ProjectProfile:
        root_path = Path(root).resolve()
        sensitive_filter = SensitiveFilter(root_path)
        source_files: list[Path] = []
        test_files: list[Path] = []
        config_files: list[Path] = []
        dependency_files: list[Path] = []
        skipped_paths: list[SkippedPath] = []

        for path in safe_walk(
            root_path,
            should_descend=lambda path: not sensitive_filter.is_denied(path),
        ):
            try:
                denied = sensitive_filter.is_denied(path)
            except OSError:
                skipped_paths.append(SkippedPath(path=path, reason="unavailable"))
                continue
            if denied:
                skipped_paths.append(SkippedPath(path=path.resolve(), reason="denied"))
                continue
            try:
                is_file = path.is_file()
            except OSError:
                skipped_paths.append(SkippedPath(path=path.resolve(), reason="unavailable"))
                continue
            if not is_file:
                continue
            resolved = path.resolve()
            relative_parts = resolved.relative_to(root_path).parts
            name = path.name
            suffix = path.suffix.lower()
            if name in CONFIG_FILENAMES:
                config_files.append(resolved)
            if name in DEPENDENCY_FILENAMES:
                dependency_files.append(resolved)
            if suffix == ".py":
                if any(part in TEST_DIR_NAMES for part in relative_parts) or name.startswith(
                    "test_"
                ):
                    test_files.append(resolved)
                elif (
                    len(relative_parts) == 1
                    or relative_parts[0] in PYTHON_SOURCE_DIRS
                ):
                    source_files.append(resolved)

        return ProjectProfile(
            root=root_path,
            source_files=source_files,
            test_files=test_files,
            config_files=config_files,
            dependency_files=dependency_files,
            skipped_paths=skipped_paths,
        )
