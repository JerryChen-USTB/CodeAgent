"""Filesystem helpers with Windows long-path support."""

from __future__ import annotations

import os
from os import PathLike
from pathlib import Path


PathInput = str | PathLike[str]


def portable_path(path: PathInput) -> Path:
    """Return a Path that can access long Windows paths."""
    return Path(_long_path(path))


def mkdir(path: PathInput, *, parents: bool = True, exist_ok: bool = True) -> None:
    portable_path(path).mkdir(parents=parents, exist_ok=exist_ok)


def read_text(path: PathInput, *, encoding: str = "utf-8") -> str:
    return portable_path(path).read_text(encoding=encoding)


def write_text(path: PathInput, text: str, *, encoding: str = "utf-8") -> None:
    portable_path(path).write_text(text, encoding=encoding)


def append_text(path: PathInput, text: str, *, encoding: str = "utf-8") -> None:
    with portable_path(path).open("a", encoding=encoding) as handle:
        handle.write(text)


def read_bytes(path: PathInput) -> bytes:
    return portable_path(path).read_bytes()


def write_bytes(path: PathInput, content: bytes) -> None:
    portable_path(path).write_bytes(content)


def exists(path: PathInput) -> bool:
    return portable_path(path).exists()


def is_dir(path: PathInput) -> bool:
    return portable_path(path).is_dir()


def is_file(path: PathInput) -> bool:
    return portable_path(path).is_file()


def touch(path: PathInput, *, exist_ok: bool = True) -> None:
    portable_path(path).touch(exist_ok=exist_ok)


def unlink(path: PathInput, *, missing_ok: bool = False) -> None:
    portable_path(path).unlink(missing_ok=missing_ok)


def _long_path(path: PathInput) -> str:
    raw = str(path)
    if os.name != "nt":
        return raw
    if raw.startswith("\\\\?\\"):
        return raw
    resolved = str(Path(raw).resolve())
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved
