from __future__ import annotations

import pytest
from pydantic import BaseModel

from codeagent.models.structured_outputs import (
    StructuredOutputValidationFailure,
    invoke_with_structured_retry,
)


class TinyPlan(BaseModel):
    title: str


def test_invoke_with_structured_retry_returns_validated_model_after_retry() -> None:
    errors: list[str | None] = []

    def producer(attempt: int, last_error: Exception | None) -> dict:
        errors.append(str(last_error) if last_error else None)
        if attempt == 1:
            return {}
        return {"title": "ok"}

    result = invoke_with_structured_retry(
        producer,
        TinyPlan,
        max_attempts=2,
    )

    assert result.value == TinyPlan(title="ok")
    assert result.attempts == 2
    assert errors[0] is None
    assert "Field required" in errors[1]


def test_invoke_with_structured_retry_raises_after_attempts() -> None:
    def producer(attempt: int, last_error: Exception | None) -> dict:
        return {}

    with pytest.raises(StructuredOutputValidationFailure) as exc_info:
        invoke_with_structured_retry(producer, TinyPlan, max_attempts=2)

    assert exc_info.value.attempts == 2
    assert "TinyPlan" in str(exc_info.value)
