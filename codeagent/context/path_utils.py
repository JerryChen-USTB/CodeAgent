"""Path traversal helpers for safe project context collection."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path


def safe_walk(
    root: Path,
    *,
    should_descend: Callable[[Path], bool] | None = None,
) -> Iterator[Path]:
    """Yield descendants while skipping directories that cannot be listed."""
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError:
        return

    for child in children:
        yield child
        try:
            if child.is_symlink() or not child.is_dir():
                continue
        except OSError:
            continue
        try:
            if should_descend is not None and not should_descend(child):
                continue
        except (OSError, ValueError):
            continue
        yield from safe_walk(child, should_descend=should_descend)
