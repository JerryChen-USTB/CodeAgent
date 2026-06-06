"""Safe text file reading with truncation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codeagent.context.sensitive_filter import SensitiveFilter


@dataclass(frozen=True)
class FileReadResult:
    path: Path
    content: str
    truncated: bool
    original_chars: int


class FileReader:
    def __init__(self, sensitive_filter: SensitiveFilter, *, max_chars: int = 12000) -> None:
        self.sensitive_filter = sensitive_filter
        self.max_chars = max_chars

    def read_text(self, path: str | Path) -> FileReadResult:
        allowed_path = self.sensitive_filter.ensure_allowed(path)
        content = allowed_path.read_text(encoding="utf-8", errors="replace")
        original_chars = len(content)
        truncated = original_chars > self.max_chars
        if truncated:
            content = content[: self.max_chars]
        return FileReadResult(
            path=allowed_path,
            content=content,
            truncated=truncated,
            original_chars=original_chars,
        )
