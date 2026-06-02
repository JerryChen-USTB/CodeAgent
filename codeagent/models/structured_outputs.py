"""Helpers for validated structured model outputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError


SchemaT = TypeVar("SchemaT", bound=BaseModel)
StructuredProducer = Callable[[int, Exception | None], Any]


@dataclass(frozen=True)
class StructuredOutputResult:
    value: BaseModel
    attempts: int


class StructuredOutputValidationFailure(ValueError):
    def __init__(self, *, schema_name: str, attempts: int, last_error: Exception) -> None:
        self.schema_name = schema_name
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Failed to produce valid {schema_name} after {attempts} attempts: {last_error}"
        )


def invoke_with_structured_retry(
    producer: StructuredProducer,
    schema: type[SchemaT],
    *,
    max_attempts: int,
) -> StructuredOutputResult:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        raw = producer(attempt, last_error)
        try:
            return StructuredOutputResult(
                value=schema.model_validate(raw),
                attempts=attempt,
            )
        except ValidationError as exc:
            last_error = exc

    assert last_error is not None
    raise StructuredOutputValidationFailure(
        schema_name=schema.__name__,
        attempts=max_attempts,
        last_error=last_error,
    )
