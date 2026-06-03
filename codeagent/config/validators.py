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
        raise ValueError(f"不支持的阶段：{raw!r}")
    return Stage(canonical)


def validate_stage_sequence(raw_stages: Iterable[str | "Stage"]) -> list["Stage"]:
    """Return canonical stages after enforcing order and contiguity."""
    if raw_stages is None:
        raise ValueError("至少需要选择一个阶段。")
    if isinstance(raw_stages, (str, bytes)):
        raise ValueError("stages 必须是阶段列表，不能是单个字符串。")
    if not isinstance(raw_stages, Iterable):
        raise ValueError("stages 必须是可迭代的阶段名称列表。")
    stages = [normalize_stage(stage) for stage in raw_stages]
    if not stages:
        raise ValueError("至少需要选择一个阶段。")

    indices = [STAGE_ORDER.index(stage.value) for stage in stages]
    if len(set(indices)) != len(indices):
        raise ValueError("阶段列表不能包含重复项。")
    if indices != sorted(indices):
        raise ValueError("阶段必须遵循 implement -> test -> debug -> repair 顺序。")
    if len(indices) > 1 and indices != list(range(indices[0], indices[-1] + 1)):
        raise ValueError("选择的阶段必须连续。")
    return stages
