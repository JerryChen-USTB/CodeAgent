"""Path filtering for sensitive and generated files."""

from __future__ import annotations

from pathlib import Path


SENSITIVE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt", ".cer"}
SENSITIVE_NAME_PARTS = {"secret", "token", "credential", "credentials"}
GENERATED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    "build",
    "dist",
    "node_modules",
    ".mypy_cache",
    ".ruff_cache",
    "codeagent_runs",
}


class SensitiveFilter:
    """Decide whether a path may be exposed to the Agent."""

    def __init__(
        self,
        root: str | Path,
        *,
        visible_roots: list[str | Path] | None = None,
        hidden_roots: list[str | Path] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.visible_roots = [
            Path(path).resolve() for path in visible_roots or []
        ]
        self.hidden_roots = [Path(path).resolve() for path in hidden_roots or []]

    def is_denied(self, path: str | Path) -> bool:
        candidate = Path(path).resolve()
        if not _is_relative_to(candidate, self.root):
            return True
        if self.visible_roots and not any(
            _is_relative_to(candidate, visible) for visible in self.visible_roots
        ):
            return True
        if any(_is_relative_to(candidate, hidden) for hidden in self.hidden_roots):
            return True
        relative_parts = candidate.relative_to(self.root).parts
        if any(part in GENERATED_DIRS for part in relative_parts):
            return True
        name = candidate.name.lower()
        if name in SENSITIVE_FILENAMES or candidate.suffix.lower() in SENSITIVE_SUFFIXES:
            return True
        return any(part in name for part in SENSITIVE_NAME_PARTS)

    def ensure_allowed(self, path: str | Path) -> Path:
        candidate = Path(path).resolve()
        if not _is_relative_to(candidate, self.root):
            raise ValueError(f"path is outside allowed root: {candidate}")
        if self.is_denied(candidate):
            raise PermissionError(f"path is denied by sensitive filter: {candidate}")
        return candidate


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
