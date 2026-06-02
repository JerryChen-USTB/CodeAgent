"""Safe keyword search over project files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codeagent.context.path_utils import safe_walk
from codeagent.context.sensitive_filter import SensitiveFilter


SEARCHABLE_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
}


@dataclass(frozen=True)
class CodeSearchMatch:
    path: Path
    line_number: int
    line_text: str


class CodeSearcher:
    def __init__(
        self,
        sensitive_filter: SensitiveFilter,
        *,
        max_file_bytes: int = 1_000_000,
        max_results: int = 100,
    ) -> None:
        self.sensitive_filter = sensitive_filter
        self.max_file_bytes = max_file_bytes
        self.max_results = max_results

    def search(self, root: str | Path, query: str) -> list[CodeSearchMatch]:
        if not query:
            return []
        root_path = Path(root).resolve()
        if not _is_relative_to(root_path, self.sensitive_filter.root):
            raise ValueError(f"search root is outside allowed root: {root_path}")
        if self.sensitive_filter.is_denied(root_path) and not _contains_visible_root(
            root_path, self.sensitive_filter.visible_roots
        ):
            return []
        matches: list[CodeSearchMatch] = []
        for path in safe_walk(
            root_path,
            should_descend=lambda path: not self.sensitive_filter.is_denied(path),
        ):
            if len(matches) >= self.max_results:
                break
            try:
                is_file = path.is_file()
            except OSError:
                continue
            if not is_file or path.suffix.lower() not in SEARCHABLE_SUFFIXES:
                continue
            if self.sensitive_filter.is_denied(path):
                continue
            try:
                if path.stat().st_size > self.max_file_bytes:
                    continue
                for line_number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(),
                    start=1,
                ):
                    if query in line:
                        matches.append(
                            CodeSearchMatch(
                                path=path.resolve(),
                                line_number=line_number,
                                line_text=line.strip(),
                            )
                        )
                        if len(matches) >= self.max_results:
                            break
            except OSError:
                continue
        return matches


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _contains_visible_root(path: Path, visible_roots: list[Path]) -> bool:
    return any(_is_relative_to(visible_root, path) for visible_root in visible_roots)
