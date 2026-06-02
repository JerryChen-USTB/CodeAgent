"""Reusable config validators."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codeagent.config.schema import Stage


STAGE_ORDER = ["implement", "test", "debug", "repair"]
STAGE_ALIASES = {
    "implement": "implement",
    "implementation": "implement",
    "test": "test",
    "testing": "test",
    "debug": "debug",
    "debugging": "debug",
    "repair": "repair",
    "repairing": "repair",
}


def normalize_stage(raw: str | "Stage") -> "Stage":
    """Normalize CLI/config stage aliases to the canonical Stage enum."""
    from codeagent.config.schema import Stage

    if isinstance(raw, Stage):
        return raw
    normalized = str(raw).strip().lower().replace("-", "_")
    canonical = STAGE_ALIASES.get(normalized)
    if canonical is None:
        raise ValueError(f"Unsupported stage: {raw!r}")
    return Stage(canonical)


def validate_stage_sequence(raw_stages: Iterable[str | "Stage"]) -> list["Stage"]:
    """Return canonical stages after enforcing order and contiguity."""
    if raw_stages is None:
        raise ValueError("At least one stage is required.")
    if isinstance(raw_stages, (str, bytes)):
        raise ValueError("stages must be a list, not a scalar string.")
    if not isinstance(raw_stages, Iterable):
        raise ValueError("stages must be an iterable list of stage names.")
    stages = [normalize_stage(stage) for stage in raw_stages]
    if not stages:
        raise ValueError("At least one stage is required.")

    indices = [STAGE_ORDER.index(stage.value) for stage in stages]
    if len(set(indices)) != len(indices):
        raise ValueError("Stages must not contain duplicates.")
    if indices != sorted(indices):
        raise ValueError("Stages must follow implement -> test -> debug -> repair order.")
    if len(indices) > 1 and indices != list(range(indices[0], indices[-1] + 1)):
        raise ValueError("Selected stages must be contiguous.")
    return stages
