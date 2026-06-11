"""VS Code extension bridge for structured CodeAgent runs."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from codeagent.cli.approval_console import (
    PATCH_AUTO_APPROVE_REMAINING_KEY,
    _approval_choice_options,
)
from codeagent.cli.executor import CliRunResult, execute_task_config
from codeagent.cli.progress import ProgressEventFormatter, ProgressReporter
from codeagent.config.loader import load_task_config
from codeagent.runtime.run_context import RunContext
from codeagent.tools.hitl import ApprovalDecision, ApprovalRequest


class BridgeProtocolError(ValueError):
    """Raised when the extension sends malformed bridge data."""


@dataclass
class JsonlBridgeChannel:
    input_stream: TextIO = sys.stdin
    output_stream: TextIO = sys.stdout

    def emit(self, event_type: str, **payload: Any) -> None:
        message = {"type": event_type, **payload}
        self.output_stream.write(
            json.dumps(message, ensure_ascii=False, default=str) + "\n"
        )
        self.output_stream.flush()

    def read_message(self) -> dict[str, Any]:
        raw = self.input_stream.readline()
        if raw == "":
            raise BridgeProtocolError("VS Code bridge stdin closed while waiting for approval.")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BridgeProtocolError(f"Invalid JSON from VS Code bridge: {exc.msg}") from exc
        if not isinstance(message, dict):
            raise BridgeProtocolError("VS Code bridge message must be a JSON object.")
        return message


class PluginProgressReporter(ProgressReporter):
    """Progress reporter that writes normalized JSONL instead of terminal text."""

    def __init__(
        self,
        channel: JsonlBridgeChannel | None = None,
        formatter: ProgressEventFormatter | None = None,
    ) -> None:
        self.channel = channel or JsonlBridgeChannel()
        self._formatter = formatter or ProgressEventFormatter()
        self._context: RunContext | None = None

    def bind_run_context(self, context: RunContext) -> None:
        self._context = context
        self.channel.emit(
            "run_started",
            run_id=context.run_id,
            run_dir=context.run_dir.as_posix(),
            task_config=_task_config_payload(context),
        )

    def planned(self, command: str, detail: str) -> None:
        self.channel.emit("workflow_event", event={"type": "planned", "command": command, "detail": detail})

    def render_event(self, event: dict[str, Any]) -> str:
        line = self._formatter.format_event(event)
        self.channel.emit("workflow_event", event=event, line=line)
        return line


class PluginApprovalConsole:
    """Approval console that exchanges decisions with a VS Code Webview."""

    def __init__(self, channel: JsonlBridgeChannel | None = None) -> None:
        self.channel = channel or JsonlBridgeChannel()
        self._context: RunContext | None = None
        self._approval_contexts: dict[str, dict[str, Any]] = {}

    def bind_run_context(self, context: RunContext) -> None:
        self._context = context

    def set_approval_context(
        self,
        request: ApprovalRequest,
        refs: list[Any],
        command_context: tuple[str, Any] | None,
        hint: str,
    ) -> None:
        payload: dict[str, Any] = {
            "files": [_file_ref_payload(ref) for ref in refs],
            "hint": hint,
        }
        if command_context is not None:
            command, cwd_ref = command_context
            payload["command"] = command
            payload["cwd"] = _file_ref_payload(cwd_ref)
        self._approval_contexts[request.interrupt_id] = payload

    def prompt(self, request: ApprovalRequest) -> ApprovalDecision:
        self.channel.emit(
            "approval_requested",
            request=_approval_request_payload(request),
            context=self._approval_contexts.get(request.interrupt_id, {}),
            choices=_approval_choices_payload(request),
        )
        while True:
            try:
                message = self.channel.read_message()
                return _decision_from_message(message, request=request)
            except BridgeProtocolError as exc:
                self.channel.emit(
                    "error",
                    code="invalid_approval_decision",
                    message=str(exc),
                    retryable=True,
                    interrupt_id=request.interrupt_id,
                )


def run_vscode_bridge(config_path: str | Path) -> int:
    """Run CodeAgent for the VS Code extension and emit JSONL protocol events."""
    channel = JsonlBridgeChannel()
    try:
        task_config = load_task_config(config_path)
        task_config.mode = "run"
        result = execute_task_config(
            task_config,
            reporter=PluginProgressReporter(channel),
            approval_console=PluginApprovalConsole(channel),
        )
        _emit_run_completed(channel, result)
        return 0 if result.final_status == "succeeded" else 1
    except Exception as exc:
        channel.emit(
            "error",
            code=type(exc).__name__,
            message=str(exc),
            retryable=False,
        )
        return 1


def _emit_run_completed(channel: JsonlBridgeChannel, result: CliRunResult) -> None:
    channel.emit(
        "run_completed",
        run_id=result.run_id,
        run_dir=result.run_dir.as_posix(),
        final_status=result.final_status,
        run_health_path=(result.run_dir / "run_health.json").as_posix(),
        run_health_report_path=(result.run_dir / "run_health.md").as_posix(),
        stage_results={
            stage: stage_result.model_dump(mode="json", exclude_none=True)
            for stage, stage_result in result.stage_results.items()
        },
    )


def _task_config_payload(context: RunContext) -> dict[str, Any]:
    config = context.task_config
    return {
        "stages": [stage.value for stage in config.stages],
        "project_path": config.project_path.as_posix(),
        "output_dir": config.output_dir.as_posix() if config.output_dir else None,
        "test_command": config.test_command.command,
        "test_timeout_seconds": config.test_command.timeout_seconds,
        "approval_mode": config.permissions.approval_mode,
        "model": {
            "provider": config.model.provider,
            "model_name": config.model.model_name,
            "base_url": config.model.base_url,
            "api_key_env": config.model.api_key_env,
        },
        "input_materials": [
            {
                "type": material.material_type,
                "path": material.path.as_posix(),
                "required": material.required,
                "multi": material.multi,
                "description": material.description,
            }
            for material in config.input_materials
        ],
    }


def _approval_request_payload(request: ApprovalRequest) -> dict[str, Any]:
    return {
        "interrupt_id": request.interrupt_id,
        "action": request.action,
        "title": request.title,
        "risk_level": request.risk_level,
        "allowed_decisions": list(request.allowed_decisions),
        "default_decision": request.default_decision,
        "payload": request.payload,
    }


def _approval_choices_payload(request: ApprovalRequest) -> list[dict[str, str]]:
    choices = []
    for value, label in _approval_choice_options(request):
        decision_type = "approve" if value == "__patch_auto_approve_remaining_stage__" else value
        choices.append(
            {
                "value": value,
                "decision_type": decision_type,
                "label": label,
            }
        )
    return choices


def _decision_from_message(
    message: dict[str, Any],
    *,
    request: ApprovalRequest,
) -> ApprovalDecision:
    interrupt_id = str(message.get("interrupt_id") or request.interrupt_id)
    if interrupt_id != request.interrupt_id:
        raise BridgeProtocolError(
            f"approval interrupt_id mismatch: expected {request.interrupt_id}, got {interrupt_id}"
        )
    raw_type = message.get("decision_type") or message.get("choice") or message.get("type")
    decision_type = str(raw_type or "").strip()
    if decision_type == "approval_decision":
        decision_type = str(message.get("decision_type") or "").strip()
    if decision_type == "__patch_auto_approve_remaining_stage__":
        return ApprovalDecision(
            interrupt_id=request.interrupt_id,
            decision_type="approve",
            edited_payload={PATCH_AUTO_APPROVE_REMAINING_KEY: True},
            comment="应用此补丁，并在本阶段本轮自动通过后续补丁。",
            decided_by="user",
            auto=False,
            decision_source="vscode_stage_patch_auto_approve",
            presented_to_user=True,
        )
    if decision_type not in request.allowed_decisions:
        allowed = ", ".join(request.allowed_decisions)
        raise BridgeProtocolError(f"decision_type {decision_type!r} is not allowed; allowed: {allowed}")

    comment = message.get("comment")
    if comment is not None:
        comment = str(comment).strip()
    if decision_type == "respond" and not comment:
        raise BridgeProtocolError("respond decisions must include a non-empty comment.")

    edited_payload = _edited_payload_from_message(message, decision_type=decision_type)
    return ApprovalDecision(
        interrupt_id=request.interrupt_id,
        decision_type=decision_type,  # type: ignore[arg-type]
        edited_payload=edited_payload,
        comment=comment or None,
        decided_by="user",
        auto=False,
        decision_source="vscode",
        presented_to_user=True,
    )


def _edited_payload_from_message(
    message: dict[str, Any],
    *,
    decision_type: str,
) -> dict[str, Any] | None:
    if decision_type != "edit":
        return None
    payload = message.get("edited_payload")
    if payload is None and isinstance(message.get("edited_payload_text"), str):
        try:
            payload = json.loads(str(message["edited_payload_text"]))
        except json.JSONDecodeError as exc:
            raise BridgeProtocolError(f"edited_payload_text must be valid JSON: {exc.msg}") from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise BridgeProtocolError("edited_payload must be a JSON object.")
    return payload


def _file_ref_payload(ref: Any) -> dict[str, str]:
    path = Path(ref.absolute_path)
    uri = ""
    try:
        uri = path.resolve().as_uri()
    except ValueError:
        uri = ""
    return {
        "label": str(ref.display),
        "path": path.as_posix(),
        "uri": uri,
    }
