"""Checkpoint-safe AgentState helpers."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal, TypedDict

from pydantic import BaseModel

from codeagent.errors.exceptions import CodeAgentError


RunMode = Literal["wizard", "run", "benchmark", "resume"]
JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
MAX_STATE_STRING_LENGTH = 16000


class CheckpointSafetyError(CodeAgentError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            category="checkpoint",
            severity="error",
            retryable=False,
        )


class AgentState(TypedDict, total=False):
    run_id: str
    mode: RunMode
    selected_stages: list[str]
    current_stage: str | None
    current_node: str | None
    messages: list[dict[str, JsonValue]]
    todo_list: list[dict[str, JsonValue]]
    context_summary: str
    artifact_refs: list[str]
    stage_results: dict[str, dict[str, JsonValue]]
    pending_interrupt: dict[str, JsonValue] | None
    error: dict[str, JsonValue] | None


def create_initial_state(
    *,
    run_id: str,
    mode: RunMode,
    selected_stages: list[str],
) -> AgentState:
    return {
        "run_id": run_id,
        "mode": mode,
        "selected_stages": list(selected_stages),
        "current_stage": None,
        "current_node": None,
        "messages": [],
        "todo_list": [],
        "context_summary": "",
        "artifact_refs": [],
        "stage_results": {},
        "pending_interrupt": None,
        "error": None,
    }


def state_to_json_dict(state: AgentState | dict[str, Any]) -> dict[str, JsonValue]:
    payload = _to_checkpoint_value(state, location="state")
    if not isinstance(payload, dict):
        raise CheckpointSafetyError("AgentState root is not checkpoint safe")
    try:
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CheckpointSafetyError(
            f"AgentState is not checkpoint safe: {exc}"
        ) from exc
    return payload


def _to_checkpoint_value(value: Any, *, location: str) -> JsonValue:
    if isinstance(value, BaseModel):
        return _to_checkpoint_value(value.model_dump(mode="json"), location=location)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, str):
        if len(value) > MAX_STATE_STRING_LENGTH:
            raise CheckpointSafetyError(
                f"{location} is not checkpoint safe: string exceeds "
                f"{MAX_STATE_STRING_LENGTH} characters"
            )
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CheckpointSafetyError(
                f"{location} is not checkpoint safe: non-finite float {value!r}"
            )
        return value
    if isinstance(value, tuple):
        return [
            _to_checkpoint_value(item, location=f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, list):
        return [
            _to_checkpoint_value(item, location=f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        converted: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CheckpointSafetyError(
                    f"{location} is not checkpoint safe: non-string key {key!r}"
                )
            converted[key] = _to_checkpoint_value(
                item,
                location=f"{location}.{key}",
            )
        return converted
    raise CheckpointSafetyError(
        f"{location} is not checkpoint safe: unsupported {type(value).__name__}"
    )
