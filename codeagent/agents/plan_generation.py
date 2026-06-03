"""LLM-backed implementation and repair plan generation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from codeagent import filesystem as fs
from codeagent.config.schema import Stage
from codeagent.context.sensitive_filter import (
    GENERATED_DIRS,
    SensitiveFilter,
    SENSITIVE_FILENAMES,
    SENSITIVE_NAME_PARTS,
    SENSITIVE_SUFFIXES,
)
from codeagent.models.factory import ModelClientFactory
from codeagent.runtime.run_context import RunContext
from codeagent.stages.implementation_service import (
    PATCH_INTERRUPT_ID,
    ImplementationPlan,
    ImplementationRequest,
)
from codeagent.stages.repair_service import (
    REPAIR_COMMAND_INTERRUPT_ID,
    REPAIR_PATCH_INTERRUPT_ID,
    RepairPlan,
    RepairRequest,
)
from codeagent.tools.hitl import ApprovalDecision


SchemaT = TypeVar("SchemaT", bound=BaseModel)
TEXT_EXTENSIONS = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml"}


class PlanGenerationError(RuntimeError):
    """Raised when the LLM cannot produce a valid structured plan."""


class PlanGenerationService:
    def __init__(
        self,
        *,
        model_factory: ModelClientFactory | None = None,
        max_context_chars: int = 80_000,
    ) -> None:
        self.model_factory = model_factory or ModelClientFactory()
        self.max_context_chars = max_context_chars

    def create_implementation_request(
        self,
        context: RunContext,
    ) -> ImplementationRequest:
        prompt = self._implementation_prompt(context)
        plan = self._invoke_schema(context, prompt, ImplementationPlan)
        return ImplementationRequest(
            plan=plan,
            approval=_approval(
                context,
                interrupt_id=PATCH_INTERRUPT_ID,
                comment="Auto-approved generated implementation patch.",
            ),
            max_patch_attempts=max(1, context.task_config.model.max_retries + 1),
            command_timeout_seconds=context.task_config.test_command.timeout_seconds,
        )

    def create_repair_request(self, context: RunContext) -> RepairRequest:
        prompt = self._repair_prompt(context)
        plan = self._invoke_schema(context, prompt, RepairPlan)
        return RepairRequest(
            plan=plan,
            patch_approval=_approval(
                context,
                interrupt_id=REPAIR_PATCH_INTERRUPT_ID,
                comment="Auto-approved generated repair patch.",
            ),
            command_approval=_approval(
                context,
                interrupt_id=REPAIR_COMMAND_INTERRUPT_ID,
                comment="Auto-approved generated repair verification command.",
            ),
            max_patch_attempts=max(1, context.task_config.model.max_retries + 1),
            command_timeout_seconds=context.task_config.test_command.timeout_seconds,
        )

    def _implementation_prompt(self, context: RunContext) -> str:
        return "\n\n".join(
            [
                _system_rules("implementation", "ImplementationPlan"),
                _schema_block(ImplementationPlan),
                "Task inputs and visible project files:",
                _visible_context(
                    context,
                    include_failure_logs=False,
                    max_context_chars=self.max_context_chars,
                ),
                (
                    "Return only JSON. Include exact old_content when modifying an "
                    "existing file and full new_content for every changed file. "
                    "Use project-root-relative paths. If the configured project root is "
                    "already a workspace directory, do not prefix paths with workspace/; "
                    "for example use package/module.py, not workspace/package/module.py."
                ),
            ]
        )

    def _repair_prompt(self, context: RunContext) -> str:
        return "\n\n".join(
            [
                _system_rules("repair", "RepairPlan"),
                _schema_block(RepairPlan),
                "Visible project files and failure evidence:",
                _visible_context(
                    context,
                    include_failure_logs=True,
                    max_context_chars=self.max_context_chars,
                ),
                (
                    "Return only JSON. Produce a complete, scope-controlled "
                    "source-code repair with enough context for audit. Do not modify "
                    "tests. Set verification_command to the configured command unless "
                    f"a safer equivalent is required: "
                    f"{context.task_config.test_command.command!r}."
                ),
            ]
        )

    def _invoke_schema(
        self,
        context: RunContext,
        prompt: str,
        schema: type[SchemaT],
    ) -> SchemaT:
        model = self.model_factory.create(context.task_config.model)
        last_error: Exception | None = None
        attempts = max(1, context.task_config.model.max_retries + 1)
        audit = _new_attempt_audit(prompt=prompt, schema=schema, attempts=attempts)
        for attempt in range(1, attempts + 1):
            retry_prompt = prompt
            if last_error is not None:
                retry_prompt += (
                    "\n\nPrevious response failed schema validation. "
                    f"Attempt {attempt}/{attempts}. Error: {_redact(str(last_error))}"
                )
            try:
                response = model.invoke(retry_prompt)
            except Exception as exc:
                last_error = exc
                _record_attempt(
                    audit,
                    attempt=attempt,
                    status="model_error",
                    error=exc,
                )
                _write_attempt_audit(context, schema, audit)
                continue
            response_text = _response_text(response)
            try:
                payload = json.loads(_extract_json(response_text))
                value = schema.model_validate(payload)
                value = _normalize_generated_plan(value, context.task_config.project_path)
                _validate_generated_plan_targets(value, context)
                _record_attempt(
                    audit,
                    attempt=attempt,
                    status="valid",
                    response_text=response_text,
                )
                _write_attempt_audit(context, schema, audit)
                return value
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                last_error = exc
                _record_attempt(
                    audit,
                    attempt=attempt,
                    status="invalid",
                    error=exc,
                    response_text=response_text,
                )
                _write_attempt_audit(context, schema, audit)
        raise PlanGenerationError(
            f"Failed to generate valid {schema.__name__}: {_redact(str(last_error))}"
        )


def _system_rules(stage: str, schema_name: str) -> str:
    return (
        f"You are CodeAgent's {stage} planner. Generate a {schema_name} that can be "
        "validated by Pydantic and applied as a complete, scope-controlled patch. "
        "Use only visible inputs and visible project files supplied below. Never infer or request "
        "hidden benchmark oracle material, evaluation directories, expected answers, "
        "secret files, API keys, tokens, or credentials. Do not claim tests pass; "
        "the system will run verification commands after applying the patch. "
        "When generating SQLite code, close every sqlite3.Connection explicitly, "
        "for example with contextlib.closing or try/finally; context manager alone "
        "does not close sqlite3.Connection, which can leave database files locked on Windows."
    )


def _normalize_generated_plan(plan: SchemaT, project_root: Path) -> SchemaT:
    if isinstance(plan, ImplementationPlan):
        changes = [
            change.model_copy(
                update={"path": _project_relative_path(change.path, project_root)}
            )
            for change in plan.changes
        ]
        syntax_targets = [
            _project_relative_path(target, project_root)
            for target in plan.syntax_check_targets
        ]
        return plan.model_copy(
            update={"changes": changes, "syntax_check_targets": syntax_targets}
        )  # type: ignore[return-value]
    if isinstance(plan, RepairPlan):
        changes = [
            change.model_copy(
                update={"path": _project_relative_path(change.path, project_root)}
            )
            for change in plan.changes
        ]
        return plan.model_copy(update={"changes": changes})  # type: ignore[return-value]
    return plan


def _validate_generated_plan_targets(plan: BaseModel, context: RunContext) -> None:
    root = context.task_config.project_path.resolve()
    hidden_roots = [
        path.resolve() for path in context.task_config.agent_visibility.hidden_paths
    ]
    sensitive_filter = SensitiveFilter(
        root,
        visible_roots=[root],
        hidden_roots=hidden_roots,
    )
    errors: list[str] = []
    for target_path in _generated_plan_target_paths(plan):
        normalized = _safe_generated_target(target_path)
        if normalized is None:
            errors.append(f"generated plan path outside project root: {target_path}")
            continue
        target = (root / normalized).resolve()
        if not _is_relative_to(target, root):
            errors.append(f"generated plan path outside project root: {target_path}")
            continue
        if _is_hidden_benchmark_target(normalized) or any(
            _is_relative_to(target, hidden) for hidden in hidden_roots
        ):
            errors.append(
                f"generated plan targets hidden benchmark material: {normalized}"
            )
            continue
        if sensitive_filter.is_denied(target):
            errors.append(
                f"generated plan targets sensitive or generated path: {normalized}"
            )
    if errors:
        raise ValueError("; ".join(errors))


def _generated_plan_target_paths(plan: BaseModel) -> list[Path]:
    if isinstance(plan, ImplementationPlan):
        return [change.path for change in plan.changes] + list(plan.syntax_check_targets)
    if isinstance(plan, RepairPlan):
        return [change.path for change in plan.changes]
    return []


def _safe_generated_target(path: Path) -> str | None:
    raw = str(path).replace("\\", "/")
    posix_path = PurePosixPath(raw)
    if (
        not raw
        or posix_path.is_absolute()
        or any(part in {"", ".."} for part in posix_path.parts)
        or (posix_path.parts and ":" in posix_path.parts[0])
    ):
        return None
    return posix_path.as_posix()


def _is_hidden_benchmark_target(path: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(path.replace("\\", "/")).parts]
    return (
        "evaluation" in parts
        or "oracle_tests" in parts
        or any(part == "expected_result.json" for part in parts)
    )


def _project_relative_path(raw_path: Path, project_root: Path) -> Path:
    project_root = project_root.resolve()
    path = Path(raw_path)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(project_root)
        except ValueError:
            return path
    parts = path.parts
    if not parts:
        return path
    stripped = Path(*parts[1:]) if len(parts) > 1 else path
    should_consider_prefix = parts[0] in {project_root.name, "workspace", "project"}
    if not should_consider_prefix:
        return path
    original_candidate = project_root / path
    stripped_candidate = project_root / stripped
    prefixed_directory = project_root / parts[0]
    original_parent_exists = fs.exists(original_candidate.parent)
    stripped_parent_exists = fs.exists(stripped_candidate.parent)
    if (
        not fs.exists(original_candidate)
        and (
            fs.exists(stripped_candidate)
            or not fs.exists(prefixed_directory)
            or (stripped_parent_exists and not original_parent_exists)
        )
    ):
        return stripped
    return path


def _schema_block(schema: type[BaseModel]) -> str:
    return "JSON schema:\n" + json.dumps(
        schema.model_json_schema(),
        ensure_ascii=False,
        indent=2,
    )


def _visible_context(
    context: RunContext,
    *,
    include_failure_logs: bool,
    max_context_chars: int,
) -> str:
    hidden_paths = [path.resolve() for path in context.task_config.agent_visibility.hidden_paths]
    visible_roots = [
        path.resolve() for path in context.task_config.agent_visibility.visible_paths
    ]
    sections: list[str] = []
    budget = max(0, max_context_chars)

    project_root = context.task_config.project_path.resolve()
    for material in context.task_config.input_materials:
        material_root = _context_root_for_material(material.path)
        for path in _iter_text_files(material.path):
            if not _is_visible_file(
                path,
                visible_roots=visible_roots,
                hidden_paths=hidden_paths,
                context_roots=visible_roots or [material_root],
            ):
                continue
            budget = _append_file_section(
                sections,
                label=f"input/{path.name}",
                path=path,
                budget=budget,
            )
            if budget <= 0:
                return "\n\n".join(sections)

    for path in _iter_text_files(project_root):
        if not _is_visible_file(
            path,
            visible_roots=visible_roots,
            hidden_paths=hidden_paths,
            context_roots=visible_roots or [project_root],
        ):
            continue
        try:
            label = path.resolve().relative_to(project_root).as_posix()
        except ValueError:
            label = path.name
        budget = _append_file_section(
            sections,
            label=f"project/{label}",
            path=path,
            budget=budget,
        )
        if budget <= 0:
            return "\n\n".join(sections)

    if include_failure_logs:
        for log_path in _failure_log_paths(context):
            budget = _append_file_section(
                sections,
                label=f"failure_log/{log_path.name}",
                path=log_path,
                budget=budget,
            )
            if budget <= 0:
                break
    return "\n\n".join(sections) if sections else "(no visible context files found)"


def _iter_text_files(path: Path) -> list[Path]:
    path = path.resolve()
    if fs.is_file(path):
        return [path] if path.suffix.lower() in TEXT_EXTENSIONS else []
    if not fs.is_dir(path):
        return []
    return [
        candidate
        for candidate in sorted(path.rglob("*"))
        if fs.is_file(candidate) and candidate.suffix.lower() in TEXT_EXTENSIONS
    ]


def _context_root_for_material(path: Path) -> Path:
    resolved = path.resolve()
    if fs.is_file(resolved):
        return resolved.parent
    return resolved


def _append_file_section(
    sections: list[str],
    *,
    label: str,
    path: Path,
    budget: int,
) -> int:
    try:
        text = fs.read_text(path)
    except UnicodeDecodeError:
        text = fs.portable_path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return budget
    if len(text) > budget:
        text = text[:budget] + "\n[truncated]\n"
    sections.append(f"### {label}\n{text}")
    return budget - len(text)


def _failure_log_paths(context: RunContext) -> list[Path]:
    logs_dir = context.stage_dirs[Stage.TEST] / "logs"
    candidates = [
        logs_dir / "testing_cli_command.stdout.log",
        logs_dir / "testing_cli_command.stderr.log",
    ]
    if fs.exists(logs_dir):
        candidates.extend(sorted(logs_dir.glob("*.stdout.log")))
        candidates.extend(sorted(logs_dir.glob("*.stderr.log")))
    seen: set[Path] = set()
    paths: list[Path] = []
    for path in candidates:
        if not fs.exists(path) or not fs.is_file(path):
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        paths.append(path)
    return paths


def _new_attempt_audit(
    *,
    prompt: str,
    schema: type[BaseModel],
    attempts: int,
) -> dict[str, Any]:
    return {
        "schema": schema.__name__,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_chars": len(prompt),
        "max_attempts": attempts,
        "attempts": [],
    }


def _record_attempt(
    audit: dict[str, Any],
    *,
    attempt: int,
    status: str,
    error: Exception | None = None,
    response_text: str | None = None,
) -> None:
    record: dict[str, Any] = {
        "attempt": attempt,
        "status": status,
    }
    if response_text is not None:
        record["response_chars"] = len(response_text)
        record["response_preview"] = _truncate(_redact(response_text), limit=500)
    if error is not None:
        record["error_type"] = type(error).__name__
        record["error_message"] = _truncate(_redact(str(error)), limit=2_000)
    audit["attempts"].append(record)


def _write_attempt_audit(
    context: RunContext,
    schema: type[BaseModel],
    audit: dict[str, Any],
) -> None:
    stage = Stage.REPAIR if issubclass(schema, RepairPlan) else Stage.IMPLEMENT
    stage_dir = context.stage_dirs[stage]
    _mkdir(stage_dir)
    _write_text(
        stage_dir / "plan_generation_attempts.json",
        json.dumps(audit, ensure_ascii=False, indent=2),
    )


def _is_visible_file(
    path: Path,
    *,
    visible_roots: list[Path],
    hidden_paths: list[Path],
    context_roots: list[Path],
) -> bool:
    resolved = path.resolve()
    if visible_roots and not any(_is_relative_to(resolved, root) for root in visible_roots):
        return False
    if any(_is_relative_to(resolved, hidden) for hidden in hidden_paths):
        return False
    parts = _relative_parts_for_policy(resolved, context_roots=context_roots)
    if any(part in GENERATED_DIRS for part in parts):
        return False
    name = resolved.name.lower()
    if name in SENSITIVE_FILENAMES or resolved.suffix.lower() in SENSITIVE_SUFFIXES:
        return False
    return not any(part in name for part in SENSITIVE_NAME_PARTS)


def _relative_parts_for_policy(path: Path, *, context_roots: list[Path]) -> tuple[str, ...]:
    for root in context_roots:
        if _is_relative_to(path, root):
            return path.relative_to(root).parts
    return path.parts


def _approval(context: RunContext, *, interrupt_id: str, comment: str) -> ApprovalDecision:
    benchmark_auto = (
        context.task_config.mode == "benchmark"
        or context.task_config.auto_approve_in_benchmark
        or context.task_config.runtime.auto_approve_in_benchmark
    )
    return ApprovalDecision(
        interrupt_id=interrupt_id,
        decision_type="approve",
        comment=comment,
        decided_by="benchmark" if benchmark_auto else "cli",
        auto=benchmark_auto,
    )


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item) for item in content)
    return str(content)


def _extract_json(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("model response did not contain a JSON object")
    return text[start : end + 1]


def _redact(text: str) -> str:
    redacted = re.sub(r"sk(?:-or)?-[A-Za-z0-9_-]+", "<redacted>", text)
    redacted = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer <redacted>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|authorization|token|secret|password)"
        r"(\s*[:=]\s*)"
        r"([^\s,;]+)",
        r"\1\2<redacted>",
        redacted,
    )
    return redacted


def _truncate(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def _mkdir(path: Path) -> None:
    fs.mkdir(path)


def _write_text(path: Path, text: str) -> None:
    fs.write_text(path, text)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
