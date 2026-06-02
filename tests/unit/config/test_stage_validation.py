from __future__ import annotations

import pytest

from codeagent.config.validators import normalize_stage, validate_stage_sequence
from codeagent.config.schema import Stage


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("implement", Stage.IMPLEMENT),
        ("implementation", Stage.IMPLEMENT),
        ("test", Stage.TEST),
        ("testing", Stage.TEST),
        ("debug", Stage.DEBUG),
        ("debugging", Stage.DEBUG),
        ("repair", Stage.REPAIR),
    ],
)
def test_stage_aliases_normalize(raw: str, expected: Stage) -> None:
    assert normalize_stage(raw) is expected


@pytest.mark.parametrize(
    "stages",
    [
        ["implement"],
        ["test"],
        ["debug"],
        ["repair"],
        ["implementation", "testing"],
        ["testing", "debugging", "repair"],
        ["implement", "test", "debug", "repair"],
    ],
)
def test_legal_stage_subsets(stages: list[str]) -> None:
    validate_stage_sequence(stages)


@pytest.mark.parametrize(
    "stages",
    [
        [],
        None,
        "implement",
        ["implement", "repair"],
        ["test", "repair"],
        ["debug", "implement"],
        ["test", "test"],
    ],
)
def test_illegal_stage_subsets_raise(stages: list[str]) -> None:
    with pytest.raises(ValueError):
        validate_stage_sequence(stages)
