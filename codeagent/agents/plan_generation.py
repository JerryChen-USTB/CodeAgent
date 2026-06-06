"""LLM-backed implementation and repair plan generation."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from codeagent import filesystem as fs
from codeagent.config.schema import Stage
from codeagent.context.sensitive_filter import (
    GENERATED_DIRS,
    SensitiveFilter,
    SENSITIVE_FILENAMES,
    SENSITIVE_NAME_PARTS,
    SENSITIVE_SUFFIXES,
)
from codeagent.context.redaction import redact_sensitive_text
from codeagent.models.factory import ModelClientFactory
from codeagent.runtime.run_context import RunContext
from codeagent.stages.debugging_service import DebuggingAnalysis
from codeagent.stages.implementation_service import (
    PATCH_INTERRUPT_ID,
    PLAN_INTERRUPT_ID as IMPLEMENTATION_PLAN_INTERRUPT_ID,
    ImplementationPlan,
    ImplementationPatchDraft,
    ImplementationRequest,
)
from codeagent.stages.repair_service import (
    REPAIR_COMMAND_INTERRUPT_ID,
    REPAIR_PLAN_INTERRUPT_ID,
    REPAIR_PATCH_INTERRUPT_ID,
    RepairPlan,
    RepairPatchDraft,
    RepairRequest,
)
from codeagent.stages.testing_service import (
    TEST_COMMAND_INTERRUPT_ID,
    TEST_PATCH_INTERRUPT_ID,
    TEST_PLAN_INTERRUPT_ID,
    TestingPlan,
    TestingPatchDraft,
    TestingRequest,
)
from codeagent.tools.hitl import ApprovalDecision
from codeagent.workflow.progress_events import emit_progress


SchemaT = TypeVar("SchemaT", bound=BaseModel)
TEXT_EXTENSIONS = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml"}
TESTING_PLAN_MAX_FILES = 2
TESTING_PATCH_MAX_TEST_FUNCTIONS = 80
TESTING_PATCH_MAX_NEW_CONTENT_CHARS = 70_000
TESTING_SINGLE_FILE_MAX_NEW_CONTENT_CHARS = 18_000
REPAIR_PLAN_MAX_CHANGES_WHEN_SOURCE_EXISTS = 4
REPAIR_PATCH_MAX_CHANGES_WHEN_SOURCE_EXISTS = 4
OUTPUT_LANGUAGE_RULE = (
    "Output language: write every user-facing natural-language field in "
    "Simplified Chinese (简体中文), including summaries, strategies, rationales, "
    "acceptance notes, risks, root-cause explanations, expected effects, and "
    "recommendations. Preserve code identifiers, file paths, commands, API names, "
    "dependency names, log excerpts, and error messages exactly as technical tokens; "
    "do not translate identifiers or source code."
)
EXACT_CONTRACT_RULE = (
    "Exact contract rule: when visible requirements, acceptance criteria, user "
    "stories, or design models specify exact commands, stdin scripts, prompt order, "
    "stdout substrings, menu labels, JSON field names, enum values, default values, "
    "case-sensitive product names, dates, or error messages, preserve those tokens "
    "exactly. Do not substitute synonyms, alternate casing, alternate enum names, "
    "different default values, or a different input order. Generated tests should "
    "exercise these literal contracts with representative end-to-end subprocess or "
    "CLI/TUI sessions when the product is interactive."
)
LOCAL_IMPORT_RULE = (
    "Local import rule: when changing Python code, do not invent modules, helpers, "
    "imports, package names, or public APIs. Any new import from the local workspace "
    "must point to a module that exists in the visible workspace tree and whose "
    "relevant contents were read or are being created by an approved scheduled patch. "
    "If the needed behavior already exists behind a service or repository method, "
    "prefer calling that existing API instead of importing an internal helper directly."
)
INTERACTIVE_CONTRACT_RULE = (
    "Interactive CLI/TUI contract rule: for interactive products, stdout/stderr text "
    "listed in PRD or acceptance criteria is a machine-checked contract. Implement and "
    "test the literal success, listing, and error substrings exactly, including words "
    "such as created/completed/deleted, title is required, invalid due date, invalid "
    "status, unknown option, task not found, and display sentinels such as none/null "
    "when they are specified. Do not add prefixes like Error: or replace required "
    "phrases with friendlier synonyms."
)


class PlanGenerationError(RuntimeError):
    """Raised when the LLM cannot produce a valid structured plan."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class WorkspaceReadRequest(BaseModel):
    """A visible workspace file or line range the Agent wants before writing a file."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_line_range(self) -> "WorkspaceReadRequest":
        if self.start_line is None and self.end_line is not None:
            raise ValueError("start_line is required when end_line is set")
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class PatchFileContextDecision(BaseModel):
    """Agent-selected visible context before writing a scheduled single file."""

    model_config = ConfigDict(extra="forbid")

    read_requests: list[WorkspaceReadRequest] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=4000)
    work_summary_update: str | None = Field(default=None, max_length=1200)


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
        *,
        feedback: str | None = None,
    ) -> ImplementationRequest:
        prompt = self._implementation_prompt(context, feedback=feedback)
        plan = self._invoke_schema(context, prompt, ImplementationPlan)
        return ImplementationRequest(
            plan=plan,
            plan_review=_approval(
                context,
                interrupt_id=IMPLEMENTATION_PLAN_INTERRUPT_ID,
                comment="Auto-approved generated implementation plan.",
            ),
            approval=_approval(
                context,
                interrupt_id=PATCH_INTERRUPT_ID,
                comment="Auto-approved generated implementation patch.",
            ),
            max_patch_attempts=max(1, context.task_config.model.max_retries + 1),
            command_timeout_seconds=context.task_config.test_command.timeout_seconds,
        )

    def create_implementation_patch_draft(
        self,
        context: RunContext,
        plan: ImplementationPlan,
        *,
        feedback: str | None = None,
    ) -> ImplementationPatchDraft:
        prompt = self._implementation_patch_prompt(context, plan, feedback=feedback)
        return self._invoke_schema(
            context,
            prompt,
            ImplementationPatchDraft,
            generation_kind="patch_generation",
        )

    def select_patch_file_context(
        self,
        context: RunContext,
        *,
        stage: Literal["implementation", "testing", "repair"],
        plan: BaseModel,
        target_path: Path,
        workspace_tree: str,
        work_summary: str,
        completed_files: list[str],
        failed_attempts: list[dict[str, Any]],
        feedback: str | None = None,
    ) -> PatchFileContextDecision:
        stage_enum = _stage_enum_for_name(stage)
        prompt = self._patch_file_context_prompt(
            context,
            stage=stage,
            plan=plan,
            target_path=target_path,
            workspace_tree=workspace_tree,
            work_summary=work_summary,
            completed_files=completed_files,
            failed_attempts=failed_attempts,
            feedback=feedback,
        )
        return self._invoke_schema(
            context,
            prompt,
            PatchFileContextDecision,
            generation_kind="patch_file_context_selection",
            audit_stage=stage_enum,
            audit_filename=f"patch_context_{_path_slug(target_path)}_attempts.json",
            post_validator=lambda decision: _validate_patch_file_context_decision(
                decision,
                context,
            ),
        )

    def create_implementation_file_patch_draft(
        self,
        context: RunContext,
        plan: ImplementationPlan,
        *,
        target_path: Path,
        workspace_context: str,
        work_summary: str,
        completed_files: list[str],
        failed_attempts: list[dict[str, Any]],
        feedback: str | None = None,
    ) -> ImplementationPatchDraft:
        prompt = self._single_file_patch_prompt(
            context,
            stage="implementation",
            schema=ImplementationPatchDraft,
            plan=plan,
            target_path=target_path,
            workspace_context=workspace_context,
            work_summary=work_summary,
            completed_files=completed_files,
            failed_attempts=failed_attempts,
            feedback=feedback,
            guidance=(
                "Generate implementation code only. Do not create, modify, delete, or "
                "reference tests, tests/, test_*.py, *_test.py, conftest.py, pytest.ini, "
                "or other test artifacts. syntax_check_targets may include only the "
                "target file when it is a Python file; otherwise leave it empty. "
                f"{INTERACTIVE_CONTRACT_RULE}"
            ),
        )
        return self._invoke_schema(
            context,
            prompt,
            ImplementationPatchDraft,
            generation_kind="single_file_patch_generation",
            audit_stage=Stage.IMPLEMENT,
            audit_filename=f"patch_generation_{_path_slug(target_path)}_attempts.json",
            post_validator=lambda draft: _validate_single_file_patch_draft(
                draft,
                target_path,
            ),
        )

    def create_repair_request(
        self,
        context: RunContext,
        *,
        feedback: str | None = None,
    ) -> RepairRequest:
        prompt = self._repair_prompt(context, feedback=feedback)
        plan = self._invoke_schema(
            context,
            prompt,
            RepairPlan,
            post_validator=lambda value: _validate_repair_plan_scope(value, context),
        )
        return RepairRequest(
            plan=plan,
            plan_review=_approval(
                context,
                interrupt_id=REPAIR_PLAN_INTERRUPT_ID,
                comment="Auto-approved generated repair plan.",
            ),
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

    def create_debugging_analysis(
        self,
        context: RunContext,
        *,
        failure_summary: str,
        static_localization: BaseModel,
        feedback: str | None = None,
    ) -> DebuggingAnalysis:
        prompt = self._debugging_analysis_prompt(
            context,
            failure_summary=failure_summary,
            static_localization=static_localization,
            feedback=feedback,
        )
        return self._invoke_schema(
            context,
            prompt,
            DebuggingAnalysis,
            generation_kind="debugging_analysis",
            audit_stage=Stage.DEBUG,
            audit_filename="llm_debug_analysis_attempts.json",
        )

    def create_repair_patch_draft(
        self,
        context: RunContext,
        plan: RepairPlan,
        *,
        feedback: str | None = None,
    ) -> RepairPatchDraft:
        prompt = self._repair_patch_prompt(context, plan, feedback=feedback)
        return self._invoke_schema(
            context,
            prompt,
            RepairPatchDraft,
            generation_kind="patch_generation",
            post_validator=lambda draft: _validate_repair_patch_scope(
                draft,
                context,
                plan=plan,
            ),
        )

    def create_repair_file_patch_draft(
        self,
        context: RunContext,
        plan: RepairPlan,
        *,
        target_path: Path,
        workspace_context: str,
        work_summary: str,
        completed_files: list[str],
        failed_attempts: list[dict[str, Any]],
        feedback: str | None = None,
    ) -> RepairPatchDraft:
        latest_testing_command = _latest_testing_command(context)
        command_guidance = (
            "Use the latest Agent self-test command for verification unless the "
            f"approved plan requires a safer equivalent: {latest_testing_command!r}."
            if latest_testing_command
            else "Use the approved plan verification command unless a safer equivalent is required."
        )
        prompt = self._single_file_patch_prompt(
            context,
            stage="repair",
            schema=RepairPatchDraft,
            plan=plan,
            target_path=target_path,
            workspace_context=workspace_context,
            work_summary=work_summary,
            completed_files=completed_files,
            failed_attempts=failed_attempts,
            feedback=feedback,
            guidance=(
                _repair_patch_target_guidance(plan)
                + " "
                f"{command_guidance}"
            ),
        )
        return self._invoke_schema(
            context,
            prompt,
            RepairPatchDraft,
            generation_kind="single_file_patch_generation",
            audit_stage=Stage.REPAIR,
            audit_filename=f"patch_generation_{_path_slug(target_path)}_attempts.json",
            post_validator=lambda draft: _validate_single_file_patch_draft(
                draft,
                target_path,
            )
            or _validate_repair_patch_scope(draft, context, plan=plan),
        )

    def create_testing_request(
        self,
        context: RunContext,
        *,
        feedback: str | None = None,
    ) -> TestingRequest:
        prompt = self._testing_prompt(context, feedback=feedback)
        plan = self._invoke_schema(
            context,
            prompt,
            TestingPlan,
            post_validator=_validate_testing_plan_scope,
        )
        return TestingRequest(
            plan=plan,
            plan_review=_approval(
                context,
                interrupt_id=TEST_PLAN_INTERRUPT_ID,
                comment="Auto-approved generated testing plan.",
            ),
            patch_approval=_approval(
                context,
                interrupt_id=TEST_PATCH_INTERRUPT_ID,
                comment="Auto-approved generated testing patch.",
            ),
            command_approval=_approval(
                context,
                interrupt_id=TEST_COMMAND_INTERRUPT_ID,
                comment="Auto-approved generated testing command.",
            ),
            max_patch_attempts=max(1, context.task_config.model.max_retries + 1),
            command_timeout_seconds=context.task_config.test_command.timeout_seconds,
        )

    def create_testing_patch_draft(
        self,
        context: RunContext,
        plan: TestingPlan,
        *,
        feedback: str | None = None,
    ) -> TestingPatchDraft:
        prompt = self._testing_patch_prompt(context, plan, feedback=feedback)
        return self._invoke_schema(
            context,
            prompt,
            TestingPatchDraft,
            generation_kind="patch_generation",
            post_validator=lambda draft: _validate_testing_patch_scope(
                draft,
                single_file=False,
            ),
        )

    def create_testing_file_patch_draft(
        self,
        context: RunContext,
        plan: TestingPlan,
        *,
        target_path: Path,
        workspace_context: str,
        work_summary: str,
        completed_files: list[str],
        failed_attempts: list[dict[str, Any]],
        feedback: str | None = None,
    ) -> TestingPatchDraft:
        configured_command = context.task_config.test_command.command
        prompt = self._single_file_patch_prompt(
            context,
            stage="testing",
            schema=TestingPatchDraft,
            plan=plan,
            target_path=target_path,
            workspace_context=workspace_context,
            work_summary=work_summary,
            completed_files=completed_files,
            failed_attempts=failed_attempts,
            feedback=feedback,
            guidance=(
                "Generate visible pytest/unittest test code only. The target must be "
                "inside tests/ or named test_*.py. The command must execute the tests "
                "generated or fully rewritten by this testing stage. Do not reference "
                "hidden benchmark directories, oracle_tests, evaluation, or "
                "expected_result.json. Do not use py_compile as the testing command. "
                "For this single file, write a compact representative test set and "
                "keep the full generated suite within the approved total test budget. "
                "Use pytest parametrization instead of many near-duplicate test "
                "functions. Do not split tests only to satisfy an artificial per-file "
                "function cap. If this "
                "file uses subprocess to capture non-ASCII CLI output on Windows, set "
                "PYTHONIOENCODING=utf-8 in the child environment or avoid incompatible "
                "forced UTF-8 decoding. "
                "Set command to the approved testing plan command unless it is unsafe; "
                f"the approved plan command is {plan.command!r}. Do not narrow the "
                "command to only this target file, because the workflow must verify all "
                "tests generated by the stage after all single-file patches are applied. "
                "Prefer the configured public test command when safe: "
                f"{configured_command!r}. For interactive CLI/TUI requirements, this "
                "testing stage must include end-to-end stdin-driven coverage that asserts "
                "literal stdout/stderr substrings from the PRD and acceptance criteria. "
                f"{INTERACTIVE_CONTRACT_RULE}"
            ),
        )
        return self._invoke_schema(
            context,
            prompt,
            TestingPatchDraft,
            generation_kind="single_file_patch_generation",
            audit_stage=Stage.TEST,
            audit_filename=f"patch_generation_{_path_slug(target_path)}_attempts.json",
            post_validator=lambda draft: _validate_testing_single_file_patch_draft(
                draft,
                target_path,
            ),
        )

    def _implementation_prompt(
        self,
        context: RunContext,
        *,
        feedback: str | None = None,
    ) -> str:
        return "\n\n".join(
            _without_empty(
                [
                    _system_rules("implementation", "ImplementationPlan"),
                    _schema_block(ImplementationPlan),
                    "Task inputs and visible project files:",
                _visible_context(
                    context,
                    include_failure_logs=False,
                    max_context_chars=self.max_context_chars,
                ),
                _feedback_block(feedback),
                (
                    "Return only JSON for a pure plan. Do not include patch text, "
                    "complete code, old_content, new_content, diffs, or full file "
                    "contents. Describe what should be implemented, why, which "
                    "source files are likely involved, public interfaces, acceptance "
                    "criteria, and risks. Implementation planning must target "
                    "product/source files only: "
                    "do not create, modify, delete, or list tests, tests/, test_*.py, "
                    "*_test.py, or any other test artifact. The dedicated testing "
                    "stage will later generate a separate complete visible test suite. "
                    "Use project-root-relative paths. If the configured project root is "
                    "already a workspace directory, do not prefix paths with workspace/; "
                    "for example use package/module.py, not workspace/package/module.py. "
                    f"{EXACT_CONTRACT_RULE} {INTERACTIVE_CONTRACT_RULE}"
                ),
                ]
            )
        )

    def _implementation_patch_prompt(
        self,
        context: RunContext,
        plan: ImplementationPlan,
        *,
        feedback: str | None = None,
    ) -> str:
        return "\n\n".join(
            _without_empty(
                [
                    _system_rules("implementation patch", "ImplementationPatchDraft"),
                    _schema_block(ImplementationPatchDraft),
                    "Approved implementation plan JSON:",
                    json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=False),
                    "Task inputs and visible project files:",
                    _visible_context(
                        context,
                        include_failure_logs=False,
                        max_context_chars=self.max_context_chars,
                    ),
                    _feedback_block(feedback),
                    (
                        "Return only JSON. Now generate the concrete patch draft for "
                        "the approved plan. Include exact old_content when modifying "
                        "an existing file and full new_content for every changed file. "
                        "Do not modify tests, tests/, test_*.py, *_test.py, conftest.py, "
                        "or other test artifacts during implementation. Use "
                        "project-root-relative paths only. "
                        f"{EXACT_CONTRACT_RULE} {INTERACTIVE_CONTRACT_RULE}"
                    ),
                ]
            )
        )

    def _repair_prompt(
        self,
        context: RunContext,
        *,
        feedback: str | None = None,
    ) -> str:
        latest_testing_command = _latest_testing_command(context)
        command_guidance = (
            "Use the latest Agent self-test command from testing/test_command.json "
            f"when repairing a failed self-test: {latest_testing_command!r}. "
            "Do not switch to hidden oracle paths or py_compile-only smoke checks."
            if latest_testing_command
            else (
                "Set verification_command to the configured command unless a safer "
                f"equivalent is required: {context.task_config.test_command.command!r}."
            )
        )
        return "\n\n".join(
            _without_empty(
                [
                _system_rules("repair", "RepairPlan"),
                _schema_block(RepairPlan),
                "Visible project files and failure evidence:",
                _visible_context(
                    context,
                    include_failure_logs=True,
                    max_context_chars=self.max_context_chars,
                ),
                _feedback_block(feedback),
                (
                    "Return only JSON for a pure repair plan. Do not include patch "
                    "text, complete code, old_content, new_content, diffs, or full file "
                    "contents. Describe root cause, strategy, likely source files, "
                    "expected effects, and the recommended verification command. "
                    "Use the current project tree, current source files, failure logs, "
                    "test result artifacts, and debugging reports as primary evidence; "
                    "treat input materials as background requirements, not proof of the "
                    "current workspace state. Do not say the workspace is empty or a "
                    "package is missing unless the visible project tree and failure logs "
                    "both support that claim. Make the repair minimal and failure-driven: "
                    "usually 1-3 product/source files, never a full implementation-file "
                    "inventory unless the current source is genuinely absent. Do not list "
                    "files only because they appeared in the implementation plan. If "
                    "Windows subprocess logs show stdout/stderr decoding failures, "
                    "stdout=None, or UnicodeDecodeError, consider process I/O encoding "
                    "such as PYTHONIOENCODING or stdout/stderr reconfiguration. "
                    "Default to repairing product/source code. You may include visible "
                    "tests/** or test_*.py files in changes only when debugging evidence "
                    "explicitly indicates failure_origin generated_test_code, mixed, or "
                    "test_harness and the defect is a visible generated test problem such "
                    "as syntax/import/cwd/subprocess encoding/fixture/null-vs-None. In "
                    "that case set test_repair_allowed=true and explain "
                    "test_repair_rationale. Never modify hidden benchmark materials, "
                    "oracle_tests, evaluation, expected_result.json, conftest.py, pytest "
                    "configuration, or tests by deleting/skipping/xfailing them or "
                    f"weakening assertions. {EXACT_CONTRACT_RULE} {LOCAL_IMPORT_RULE} "
                    f"{command_guidance}"
                ),
                ]
            )
        )

    def _repair_patch_prompt(
        self,
        context: RunContext,
        plan: RepairPlan,
        *,
        feedback: str | None = None,
    ) -> str:
        latest_testing_command = _latest_testing_command(context)
        command_guidance = (
            "Use this latest Agent self-test command for verification unless the approved "
            f"plan requires a safer equivalent: {latest_testing_command!r}."
            if latest_testing_command
            else (
                "Use the approved plan verification command unless a safer equivalent "
                "is required."
            )
        )
        return "\n\n".join(
            _without_empty(
                [
                    _system_rules("repair patch", "RepairPatchDraft"),
                    _schema_block(RepairPatchDraft),
                    "Approved repair plan JSON:",
                    json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=False),
                    "Visible project files and failure evidence:",
                    _visible_context(
                        context,
                        include_failure_logs=True,
                        max_context_chars=self.max_context_chars,
                    ),
                    _feedback_block(feedback),
                    (
                        "Return only JSON. Now generate the concrete repair patch draft "
                        "for the approved plan. Include exact old_content when modifying "
                        "an existing file and full new_content for every changed file. "
                        "Keep the patch minimal and failure-driven; do not rewrite unrelated "
                        "files or recreate the implementation plan. "
                        f"{EXACT_CONTRACT_RULE} {LOCAL_IMPORT_RULE} "
                        f"{_repair_patch_target_guidance(plan)} {command_guidance}"
                    ),
                ]
            )
        )

    def _debugging_analysis_prompt(
        self,
        context: RunContext,
        *,
        failure_summary: str,
        static_localization: BaseModel,
        feedback: str | None = None,
    ) -> str:
        return "\n\n".join(
            _without_empty(
                [
                    _system_rules("debugging analysis", "DebuggingAnalysis"),
                    _schema_block(DebuggingAnalysis),
                    "Latest failure summary prepared by the workflow:",
                    failure_summary,
                    "Static fault localization JSON prepared before LLM analysis:",
                    json.dumps(
                        static_localization.model_dump(mode="json"),
                        indent=2,
                        ensure_ascii=False,
                    ),
                    "Visible project files and latest failure evidence:",
                    _visible_context(
                        context,
                        include_failure_logs=True,
                        max_context_chars=self.max_context_chars,
                    ),
                    _feedback_block(feedback),
                    (
                        "Return only JSON. Analyze the latest visible failure evidence, "
                        "not old assumptions. Classify failure_origin as one of: "
                        "product_code, generated_test_code, mixed, test_harness, or "
                        "inconclusive. product_code means implementation/source code is "
                        "the primary fault. generated_test_code means the visible tests "
                        "created by the Agent are themselves invalid. mixed means both "
                        "product code and visible generated tests plausibly need changes. "
                        "test_harness means visible test scaffolding such as imports, cwd, "
                        "subprocess encoding, fixtures, or Python literals like null/None "
                        "is preventing a valid product check. Use candidates for the "
                        "smallest visible files directly supported by stack traces, test "
                        "reports, logs, and current source/test code. "
                        "Set test_repair_allowed=true only for generated_test_code, mixed, "
                        "or test_harness, only when the evidence points to a visible tests/** "
                        "or test_*.py defect such as syntax errors, wrong imports, wrong cwd, "
                        "bad subprocess encoding, invalid fixtures, or JSON null written in "
                        "Python code. Never allow modifying hidden oracle material, "
                        "evaluation, expected_result.json, conftest.py, pytest config, or "
                        "tests by deleting/skipping/xfailing them or weakening assertions. "
                        "When evidence is insufficient, choose inconclusive with "
                        "test_repair_allowed=false and recommend collecting a clearer "
                        "traceback. Use Simplified Chinese in natural-language fields."
                    ),
                ]
            )
        )

    def _testing_prompt(
        self,
        context: RunContext,
        *,
        feedback: str | None = None,
    ) -> str:
        configured_command = context.task_config.test_command.command
        return "\n\n".join(
            _without_empty(
                [
                _system_rules("testing", "TestingPlan"),
                _schema_block(TestingPlan),
                "Task inputs, implementation artifacts, and visible project files:",
                _visible_context(
                    context,
                    include_failure_logs=False,
                    max_context_chars=self.max_context_chars,
                ),
                _feedback_block(feedback),
                    (
                        "Return only JSON for a pure test plan. Do not include patch text, "
                    "complete code, old_content, new_content, diffs, or full file "
                    "contents. Describe the test strategy, planned test files, coverage "
                    "points, acceptance criteria, and safe command recommendation. "
                    "Keep the generated suite human-scale for a self-built benchmark: "
                    "target 25-60 total test functions, hard maximum 80 total test "
                    "functions. Prefer one visible test file by default, usually "
                    "tests/test_app.py or tests/test_todo_manager.py; use two visible "
                    "test files only when one file would become clearly too large or "
                    "when end-to-end subprocess/TUI tests need to be isolated from "
                    "unit-style tests. Never plan more than two visible test files. "
                    "If two files are planned, explain the split in strategy or file "
                    "rationale. Prefer pytest "
                    "parametrization and representative acceptance paths over exhaustive "
                    "combinatorial duplication. Each planned test file should normally "
                    "contain 3-12 test functions. "
                    "Planned test files must target tests/ or test_*.py paths only. "
                    "The command must run the generated tests and must not reference hidden benchmark directories such as "
                    "oracle_tests, evaluation, or expected_result.json. Do not use "
                    "py_compile as the testing command; py_compile is only a syntax "
                    "smoke check and does not count as product testing. The command "
                    "must execute the tests generated or fully rewritten by this testing "
                    "stage. If existing tests are visible, treat them only as reference "
                    "material; do not return a plan that merely reuses them without a "
                        "complete testing-stage patch. "
                        "will already run from the configured project root, so do not use "
                        "`cd project &&`, `cd workspace &&`, or prefix paths with "
                        "`project/` or `workspace/`. For multi-file plans, choose a "
                        "full-suite command such as `python -m pytest -q` or "
                        "`python -m pytest tests -q`; do not target only one generated "
                        "test file. Prefer the "
                        f"configured public test command when it is safe: {configured_command!r}. "
                        "For interactive CLI/TUI requirements, include at least one "
                        "subprocess or equivalent end-to-end stdin session test that "
                        "asserts the literal success/listing/error stdout and stderr "
                        "substrings from the acceptance criteria. "
                        f"{EXACT_CONTRACT_RULE} {INTERACTIVE_CONTRACT_RULE}"
                ),
                ]
            )
        )

    def _testing_patch_prompt(
        self,
        context: RunContext,
        plan: TestingPlan,
        *,
        feedback: str | None = None,
    ) -> str:
        configured_command = context.task_config.test_command.command
        return "\n\n".join(
            _without_empty(
                [
                    _system_rules("testing patch", "TestingPatchDraft"),
                    _schema_block(TestingPatchDraft),
                    "Approved testing plan JSON:",
                    json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=False),
                    "Task inputs, implementation artifacts, and visible project files:",
                    _visible_context(
                        context,
                        include_failure_logs=False,
                        max_context_chars=self.max_context_chars,
                    ),
                    _feedback_block(feedback),
                    (
                        "Return only JSON. Now generate the concrete visible test patch "
                        "for the approved plan. Include full pytest/unittest test code "
                        "in new_content and exact old_content when modifying an existing "
                        "visible test file. The command must execute the tests generated "
                        "or fully rewritten by this testing stage. Do not reference hidden "
                        "benchmark directories, oracle_tests, evaluation, or "
                        "expected_result.json. Do not use py_compile as the testing "
                        "command. Keep the entire generated suite to 25-60 test functions "
                        "when possible, never more than 80. Prefer one visible test file; "
                        "use two files only when the approved plan explicitly needs a "
                        "unit/end-to-end split or a readability split, and never generate "
                        "more than two visible test files. Keep each file readable and "
                        "use parametrize instead of repeated near-duplicate functions; "
                        "do not split tests only to satisfy an artificial per-file "
                        "function cap. If you "
                        "write subprocess CLI tests, cwd must be an "
                        "existing directory inside the configured project root. Do not "
                        "derive cwd by appending hard-coded project/workspace directory "
                        "names to __file__ parents, such as parent.parent / 'project' "
                        "or parents[1] / 'workspace'. Prefer running tests from the "
                        "configured project root and invoking the CLI with "
                        "sys.executable -m <module>. On Windows, if subprocess tests "
                        "capture non-ASCII CLI output, set PYTHONIOENCODING=utf-8 in the "
                        "child environment or otherwise avoid forced UTF-8 decode "
                        "mismatches. Keep command aligned with the approved testing plan "
                        f"command {plan.command!r}; for a multi-file suite, do not narrow "
                        "the command to one generated test file. Prefer the configured public "
                        "command when safe: "
                        f"{configured_command!r}. For interactive CLI/TUI requirements, "
                        "write at least one end-to-end stdin-driven subprocess test and "
                        "assert the literal success/listing/error substrings from PRD and "
                        "acceptance criteria, not only generic behavior. "
                        f"{EXACT_CONTRACT_RULE} {INTERACTIVE_CONTRACT_RULE}"
                    ),
                ]
            )
        )

    def _patch_file_context_prompt(
        self,
        context: RunContext,
        *,
        stage: Literal["implementation", "testing", "repair"],
        plan: BaseModel,
        target_path: Path,
        workspace_tree: str,
        work_summary: str,
        completed_files: list[str],
        failed_attempts: list[dict[str, Any]],
        feedback: str | None = None,
    ) -> str:
        return "\n\n".join(
            _without_empty(
                [
                    _system_rules(
                        f"{stage} scheduled single-file context selection",
                        "PatchFileContextDecision",
                    ),
                    _schema_block(PatchFileContextDecision),
                    "Approved plan JSON:",
                    json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=False),
                    "Workflow-scheduled single-file patch target:",
                    target_path.as_posix(),
                    "Visible workspace file tree:",
                    workspace_tree or "(empty workspace tree)",
                    "Current incremental work summary:",
                    work_summary or "(no completed file patches yet)",
                    "Completed file patches already applied:",
                    json.dumps(completed_files, indent=2, ensure_ascii=False),
                    "Prior single-file failures and reviewer feedback:",
                    json.dumps(failed_attempts, indent=2, ensure_ascii=False),
                    _feedback_block(feedback),
                    (
                        "Return only JSON. The workflow has already selected the next "
                        "target file from the approved plan; do not choose a different "
                        "target and do not decide whether the stage is complete. Select "
                        "only the visible files or line ranges needed before writing "
                        "this scheduled file. Always request the current target file "
                        "only if it exists or likely needs exact old_content. You may "
                        "request already-applied product files, visible tests, or stage "
                        "artifacts when they are useful for cross-file consistency. "
                        "Never request hidden benchmark oracle material, evaluation "
                        "directories, expected answers, secrets, API keys, tokens, "
                        "credentials, .git, generated run output, or dependency "
                        "directories. Use work_summary_update only for new context facts "
                        "that will help this file or later scheduled files; keep it "
                        "under 600 characters and do not repeat the existing summary."
                    ),
                ]
            )
        )

    def _single_file_patch_prompt(
        self,
        context: RunContext,
        *,
        stage: Literal["implementation", "testing", "repair"],
        schema: type[SchemaT],
        plan: BaseModel,
        target_path: Path,
        workspace_context: str,
        work_summary: str,
        completed_files: list[str],
        failed_attempts: list[dict[str, Any]],
        guidance: str,
        feedback: str | None = None,
    ) -> str:
        return "\n\n".join(
            _without_empty(
                [
                    _system_rules(f"{stage} single-file patch", schema.__name__),
                    _schema_block(schema),
                    "Approved plan JSON:",
                    json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=False),
                    "Single-file patch target:",
                    target_path.as_posix(),
                    "Visible workspace file tree:",
                    _visible_project_tree_for_prompt(context, max_entries=300),
                    "Workspace context selected for this target:",
                    workspace_context or "(no additional context selected)",
                    "Current incremental work summary:",
                    work_summary or "(no completed file patches yet)",
                    "Completed file patches already applied to the workspace:",
                    json.dumps(completed_files, indent=2, ensure_ascii=False),
                    "Prior single-file failures and reviewer feedback to avoid repeating:",
                    json.dumps(failed_attempts, indent=2, ensure_ascii=False),
                    _feedback_block(feedback),
                    (
                        "Return only JSON for the schema above. Generate exactly one "
                        "file change: changes must contain exactly one item, and "
                        f"changes[0].path must be exactly {target_path.as_posix()!r}. "
                        "Do not include any other file in this response. When modifying "
                        "an existing file, old_content must match the current visible "
                        "workspace content exactly, and new_content must be the full "
                        "replacement file content. When adding a new file, use "
                        "old_content=null and full new_content. When deleting a file, "
                        "use full old_content and new_content=null. Earlier approved "
                        "file patches have already been applied to the workspace and are "
                        "included in the supplied applied-file context. The workspace "
                        "context was read once at the start of this stage; do not assume "
                        "another workspace read will happen before this patch. "
                        "keep imports, public interfaces, commands, and names consistent "
                        "with the current context. Avoid hard-coding a single failing "
                        "test input, exact failure-only literal, or exact assertion text; "
                        "prefer fixing the domain rule at the narrowest semantic location. "
                        f"{EXACT_CONTRACT_RULE} {LOCAL_IMPORT_RULE} {guidance}"
                    ),
                ]
            )
        )

    def _invoke_schema(
        self,
        context: RunContext,
        prompt: str,
        schema: type[SchemaT],
        *,
        generation_kind: str = "plan_generation",
        audit_stage: Stage | None = None,
        audit_filename: str | None = None,
        post_validator: Callable[[SchemaT], None] | None = None,
    ) -> SchemaT:
        model_config = context.task_config.model
        model = self.model_factory.create(model_config)
        last_error: Exception | None = None
        attempts = max(1, context.task_config.model.max_retries + 1)
        audit = _new_attempt_audit(prompt=prompt, schema=schema, attempts=attempts)
        trace_stage = _stage_name_for_override(schema, audit_stage)
        call_bundle = _start_llm_call_bundle(
            context,
            schema=schema,
            generation_kind=generation_kind,
            stage_override=audit_stage,
            audit_filename=audit_filename,
            model_name=context.task_config.model.model_name,
            prompt=prompt,
            max_attempts=attempts,
        )
        for attempt in range(1, attempts + 1):
            retry_prompt = prompt
            if last_error is not None:
                retry_detail = _compact_retry_detail(str(last_error))
                emit_progress(
                    "agent_status",
                    stage=trace_stage,
                    message=(
                        f"上次 LLM 输出未通过 {schema.__name__} 结构化校验："
                        f"{retry_detail}；正在重试。"
                    ),
                )
                context.workflow_trace.record(
                    "llm_retry_scheduled",
                    stage=trace_stage,
                    generation_kind=generation_kind,
                    attempt=attempt,
                    schema=schema.__name__,
                    call_id=call_bundle["call_id"],
                    reason=retry_detail,
                )
                retry_prompt += (
                    "\n\nPrevious response failed schema validation. "
                    f"Attempt {attempt}/{attempts}. Error: {_redact(str(last_error))}"
                )
            prompt_paths = _write_llm_attempt_prompt(
                call_bundle,
                attempt=attempt,
                prompt=retry_prompt,
                retry=last_error is not None,
            )
            try:
                emit_progress(
                    "agent_status",
                    stage=trace_stage,
                    message=f"正在调用 LLM 生成 {schema.__name__}（第 {attempt}/{attempts} 次）",
                )
                context.workflow_trace.record(
                    "llm_prompt",
                    stage=trace_stage,
                    generation_kind=generation_kind,
                    attempt=attempt,
                    schema=schema.__name__,
                    model=context.task_config.model.model_name,
                    call_id=call_bundle["call_id"],
                    call_dir=call_bundle["relative_call_dir"],
                    prompt_path=prompt_paths["prompt_path"],
                    prompt_manifest_path=prompt_paths["manifest_path"],
                    prompt_sha256=hashlib.sha256(retry_prompt.encode("utf-8")).hexdigest(),
                    prompt_chars=len(retry_prompt),
                    retry=last_error is not None,
                )
                response = model.invoke(retry_prompt)
            except Exception as exc:
                last_error = exc
                retryable = _is_retryable_model_error(exc)
                reduced_max_tokens = _reduced_max_tokens_for_model_error(
                    exc,
                    current_max_tokens=model_config.max_tokens,
                )
                validation_path = _write_llm_attempt_validation(
                    call_bundle,
                    attempt=attempt,
                    status="model_error",
                    error=exc,
                    retryable=retryable,
                )
                _record_attempt(
                    audit,
                    attempt=attempt,
                    status="model_error",
                    error=exc,
                    retryable=retryable,
                )
                _write_attempt_audit(
                    context,
                    schema,
                    audit,
                    stage_override=audit_stage,
                    filename_override=audit_filename,
                )
                _write_llm_call_summary(call_bundle)
                context.workflow_trace.record(
                    "llm_attempt_validation",
                    stage=trace_stage,
                    generation_kind=generation_kind,
                    attempt=attempt,
                    schema=schema.__name__,
                    call_id=call_bundle["call_id"],
                    status="model_error",
                    retryable=retryable,
                    validation_path=validation_path,
                )
                if not retryable:
                    raise PlanGenerationError(
                        f"Failed to generate valid {schema.__name__}: {_redact(str(exc))}",
                        retryable=False,
                    ) from exc
                if reduced_max_tokens is not None and attempt < attempts:
                    model_config = model_config.model_copy(
                        update={"max_tokens": reduced_max_tokens}
                    )
                    model = self.model_factory.create(model_config)
                    context.workflow_trace.record(
                        "llm_max_tokens_adjusted",
                        stage=trace_stage,
                        generation_kind=generation_kind,
                        attempt=attempt + 1,
                        schema=schema.__name__,
                        call_id=call_bundle["call_id"],
                        max_tokens=reduced_max_tokens,
                        reason=_compact_retry_detail(str(exc)),
                    )
                    emit_progress(
                        "agent_status",
                        stage=trace_stage,
                        message=(
                            "模型服务提示 max_tokens 超过当前额度，"
                            f"下一次尝试临时降为 {reduced_max_tokens}。"
                        ),
                    )
                continue
            response_text = _response_text(response)
            response_path = _write_llm_attempt_response(
                call_bundle,
                attempt=attempt,
                response_text=response_text,
            )
            context.workflow_trace.record(
                "llm_response",
                stage=trace_stage,
                generation_kind=generation_kind,
                attempt=attempt,
                schema=schema.__name__,
                call_id=call_bundle["call_id"],
                response_path=response_path,
                response_chars=len(response_text),
                response_preview=_truncate(_redact(response_text), limit=500),
            )
            try:
                payload = json.loads(_extract_json(response_text))
                value = schema.model_validate(payload)
                value = _normalize_generated_plan(value, context.task_config.project_path)
                _validate_generated_plan_targets(value, context)
                if post_validator is not None:
                    post_validator(value)
                output_path = _write_llm_attempt_output(
                    call_bundle,
                    attempt=attempt,
                    output=value.model_dump(mode="json"),
                )
                validation_path = _write_llm_attempt_validation(
                    call_bundle,
                    attempt=attempt,
                    status="valid",
                    response_text=response_text,
                    output_path=output_path,
                )
                context.workflow_trace.record(
                    "llm_structured_output",
                    stage=trace_stage,
                    generation_kind=generation_kind,
                    attempt=attempt,
                    schema=schema.__name__,
                    call_id=call_bundle["call_id"],
                    output_path=output_path,
                    validation_path=validation_path,
                    output_summary=_summarize_structured_output(value),
                )
                _record_attempt(
                    audit,
                    attempt=attempt,
                    status="valid",
                    response_text=response_text,
                )
                _write_attempt_audit(
                    context,
                    schema,
                    audit,
                    stage_override=audit_stage,
                    filename_override=audit_filename,
                )
                _write_llm_call_summary(call_bundle)
                emit_progress(
                    "agent_status",
                    stage=trace_stage,
                    message=f"LLM 已生成有效的 {schema.__name__}",
                )
                return value
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                last_error = exc
                validation_path = _write_llm_attempt_validation(
                    call_bundle,
                    attempt=attempt,
                    status="invalid",
                    error=exc,
                    response_text=response_text,
                )
                _record_attempt(
                    audit,
                    attempt=attempt,
                    status="invalid",
                    error=exc,
                    response_text=response_text,
                )
                _write_attempt_audit(
                    context,
                    schema,
                    audit,
                    stage_override=audit_stage,
                    filename_override=audit_filename,
                )
                _write_llm_call_summary(call_bundle)
                context.workflow_trace.record(
                    "llm_attempt_validation",
                    stage=trace_stage,
                    generation_kind=generation_kind,
                    attempt=attempt,
                    schema=schema.__name__,
                    call_id=call_bundle["call_id"],
                    status="invalid",
                    validation_path=validation_path,
                )
        raise PlanGenerationError(
            f"Failed to generate valid {schema.__name__}: {_redact(str(last_error))}",
            retryable=True,
        )


def _system_rules(stage: str, schema_name: str) -> str:
    return (
        f"You are CodeAgent's {stage} planner. Generate a {schema_name} that can be "
        "validated by Pydantic and audited by the workflow. "
        f"{OUTPUT_LANGUAGE_RULE} "
        "Use only visible inputs and visible project files supplied below. Never infer or request "
        "hidden benchmark oracle material, evaluation directories, expected answers, "
        "secret files, API keys, tokens, or credentials. Do not claim tests pass; "
        "the system will run verification commands after applying the patch. "
        "When generating SQLite code, close every sqlite3.Connection explicitly, "
        "for example with contextlib.closing or try/finally; context manager alone "
        "does not close sqlite3.Connection, which can leave database files locked on Windows."
    )


def _without_empty(items: list[str | None]) -> list[str]:
    return [item for item in items if item]


def _feedback_block(feedback: str | None) -> str | None:
    if not feedback or not feedback.strip():
        return None
    return (
        "Human reviewer feedback for this regeneration. Treat it as a visible, "
        "high-priority requirement, but still obey all safety, visibility, and "
        f"schema rules above:\n{_redact(feedback.strip())}"
    )


def _is_retryable_model_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    non_retryable_markers = (
        "permissiondenied",
        "permission denied",
        "authentication",
        "unauthorized",
        "forbidden",
        "invalid api key",
        "insufficient credits",
        "prompt tokens limit exceeded",
        "model is not available",
        "not available in your region",
        "unsupported model",
        "model not found",
        "does not exist",
        "error code: 401",
        "error code: 403",
        "'code': 401",
        "'code': 403",
        '"code": 401',
        '"code": 403',
    )
    return not any(marker in text for marker in non_retryable_markers)


def _reduced_max_tokens_for_model_error(
    exc: Exception,
    *,
    current_max_tokens: int | None,
) -> int | None:
    text = str(exc)
    lowered = text.lower()
    if "max_tokens" not in lowered and "requested up to" not in lowered:
        return None
    affordable = _first_int_match(
        text,
        patterns=(
            r"can only afford\s+(\d+)",
            r"only afford\s+(\d+)",
            r"最多(?:支持|可用)?\s*(\d+)",
        ),
    )
    requested = current_max_tokens or _first_int_match(
        text,
        patterns=(r"requested up to\s+(\d+)", r"requested\s+(\d+)"),
    )
    if affordable is None and requested is None:
        return None
    if affordable is not None:
        reduced = max(512, min(affordable - 64, int(affordable * 0.9)))
    else:
        reduced = max(512, int(requested * 0.5))  # type: ignore[arg-type]
    if current_max_tokens is not None and reduced >= current_max_tokens:
        return None
    return reduced


def _first_int_match(text: str, *, patterns: tuple[str, ...]) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def _normalize_generated_plan(plan: SchemaT, project_root: Path) -> SchemaT:
    if isinstance(plan, PatchFileContextDecision):
        updates: dict[str, Any] = {}
        updates["read_requests"] = [
            request.model_copy(
                update={"path": _project_relative_path(request.path, project_root)}
            )
            for request in plan.read_requests
        ]
        return plan.model_copy(update=updates)  # type: ignore[return-value]
    if isinstance(plan, DebuggingAnalysis):
        candidates = [
            candidate.model_copy(
                update={"path": _project_relative_path(candidate.path, project_root)}
            )
            for candidate in plan.candidates
        ]
        return plan.model_copy(update={"candidates": candidates})  # type: ignore[return-value]
    if isinstance(plan, ImplementationPlan):
        changes = [
            change.model_copy(
                update={"path": _project_relative_path(change.path, project_root)}
            )
            for change in plan.changes
        ]
        return plan.model_copy(update={"changes": changes})  # type: ignore[return-value]
    if isinstance(plan, ImplementationPatchDraft):
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
        return plan.model_copy(
            update={
                "changes": changes,
                "verification_command": _normalize_testing_command(
                    plan.verification_command,
                    project_root,
                ),
            }
        )  # type: ignore[return-value]
    if isinstance(plan, RepairPatchDraft):
        changes = [
            change.model_copy(
                update={"path": _project_relative_path(change.path, project_root)}
            )
            for change in plan.changes
        ]
        return plan.model_copy(
            update={
                "changes": changes,
                "verification_command": _normalize_testing_command(
                    plan.verification_command,
                    project_root,
                ),
            }
        )  # type: ignore[return-value]
    if isinstance(plan, TestingPlan):
        changes = [
            change.model_copy(
                update={"path": _project_relative_path(change.path, project_root)}
            )
            for change in plan.changes
        ]
        return plan.model_copy(
            update={
                "changes": changes,
                "command": _normalize_testing_command(plan.command, project_root),
            }
        )  # type: ignore[return-value]
    if isinstance(plan, TestingPatchDraft):
        changes = [
            change.model_copy(
                update={"path": _project_relative_path(change.path, project_root)}
            )
            for change in plan.changes
        ]
        return plan.model_copy(
            update={
                "changes": changes,
                "command": _normalize_testing_command(plan.command, project_root),
            }
        )  # type: ignore[return-value]
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
            continue
        if isinstance(plan, (ImplementationPlan, ImplementationPatchDraft)) and _is_test_artifact_target(normalized):
            errors.append(
                f"implementation plan must not target test artifact: {normalized}"
            )
            continue
        if isinstance(plan, (TestingPlan, TestingPatchDraft)) and not _is_allowed_test_target(normalized):
            errors.append(f"testing plan target is not a test path: {normalized}")
            continue
        if isinstance(plan, RepairPlan) and _is_test_artifact_target(normalized):
            if not (
                _repair_plan_allows_test_modification(plan)
                and _is_allowed_repair_test_target(normalized)
            ):
                errors.append(
                    "repair plan may target visible test files only when "
                    f"test_repair_allowed is true: {normalized}"
                )
            continue
        if isinstance(plan, RepairPatchDraft) and _is_test_artifact_target(normalized):
            continue
    if errors:
        raise ValueError("; ".join(errors))


def _validate_testing_plan_scope(plan: TestingPlan) -> None:
    unique_paths = {change.path.as_posix() for change in plan.changes}
    if len(unique_paths) > TESTING_PLAN_MAX_FILES:
        raise ValueError(
            "testing plan is too large for a self-built benchmark: "
            f"{len(unique_paths)} files planned, maximum is {TESTING_PLAN_MAX_FILES}. "
            "Prefer one test file and use pytest parametrization."
        )
    if len(unique_paths) == TESTING_PLAN_MAX_FILES and not _has_testing_file_split_rationale(
        plan.strategy,
        [getattr(change, "rationale", "") for change in plan.changes],
    ):
        raise ValueError(
            "testing plan uses two test files but does not explain why the suite must "
            "be split. Prefer one file, or explain a unit/end-to-end/readability split "
            "in strategy or file rationale."
        )
    if len(unique_paths) > 1 and _testing_command_targets_single_file(
        plan.command,
        unique_paths,
    ):
        raise ValueError(
            "testing plan command is too narrow for multiple generated test files. "
            "Use a full-suite command such as python -m pytest -q or "
            "python -m pytest tests -q."
        )


def _validate_testing_single_file_patch_draft(
    draft: TestingPatchDraft,
    target_path: Path,
) -> None:
    _validate_single_file_patch_draft(draft, target_path)
    _validate_testing_patch_scope(draft, single_file=True)


def _validate_testing_patch_scope(
    draft: TestingPatchDraft,
    *,
    single_file: bool,
) -> None:
    unique_paths = {change.path.as_posix() for change in draft.changes}
    if len(unique_paths) > TESTING_PLAN_MAX_FILES:
        raise ValueError(
            "testing patch changes too many files for a self-built benchmark: "
            f"{len(unique_paths)} files generated, maximum is {TESTING_PLAN_MAX_FILES}."
        )
    if not single_file and len(unique_paths) > 1 and _testing_command_targets_single_file(
        draft.command,
        unique_paths,
    ):
        raise ValueError(
            "testing patch command is too narrow for multiple generated test files. "
            "Use a full-suite command such as python -m pytest -q or "
            "python -m pytest tests -q."
        )
    max_tests = TESTING_PATCH_MAX_TEST_FUNCTIONS
    max_chars = (
        TESTING_SINGLE_FILE_MAX_NEW_CONTENT_CHARS
        if single_file
        else TESTING_PATCH_MAX_NEW_CONTENT_CHARS
    )
    total_tests = 0
    total_chars = 0
    for change in draft.changes:
        content = change.new_content or ""
        test_count = _count_test_functions(content)
        total_tests += test_count
        total_chars += len(content)
    if total_tests > max_tests:
        raise ValueError(
            "testing patch is too large: "
            f"{total_tests} test functions generated, maximum is {max_tests}. "
            "Regenerate a compact representative suite."
        )
    if total_chars > max_chars:
        raise ValueError(
            "testing patch content is too large: "
            f"{total_chars} characters generated, maximum is {max_chars}. "
            "Regenerate a smaller, readable test suite."
        )


def _has_testing_file_split_rationale(strategy: str, rationales: list[str]) -> bool:
    text = " ".join([strategy, *rationales]).lower()
    keywords = {
        "split",
        "separate",
        "separated",
        "isolate",
        "isolated",
        "independent",
        "readability",
        "subprocess",
        "end-to-end",
        "e2e",
        "拆分",
        "分离",
        "隔离",
        "独立",
        "可读",
        "端到端",
    }
    return any(keyword in text for keyword in keywords)


def _testing_command_targets_single_file(command: str, paths: set[str]) -> bool:
    normalized_command = command.replace("\\", "/")
    path_hits = [
        path
        for path in paths
        if path.replace("\\", "/") in normalized_command
    ]
    if len(path_hits) == 1:
        return True
    return bool(
        re.search(
            r"(?:^|\s)(?:\.?/)?tests/[^ \t\r\n;|&]+\.py(?:\s|$)",
            normalized_command,
        )
    )


def _validate_repair_plan_scope(plan: RepairPlan, context: RunContext) -> None:
    _validate_repair_test_targets(
        [change.path for change in plan.changes],
        plan=plan,
    )
    if not _project_has_visible_source_files(context):
        return
    unique_paths = {change.path.as_posix() for change in plan.changes}
    if len(unique_paths) > REPAIR_PLAN_MAX_CHANGES_WHEN_SOURCE_EXISTS:
        raise ValueError(
            "repair plan is too broad for an existing source workspace: "
            f"{len(unique_paths)} files planned, maximum is "
            f"{REPAIR_PLAN_MAX_CHANGES_WHEN_SOURCE_EXISTS}. Target only files directly "
            "implicated by the failing tests and logs."
        )


def _validate_repair_patch_scope(
    draft: RepairPatchDraft,
    context: RunContext,
    *,
    plan: RepairPlan,
) -> None:
    _validate_repair_test_targets(
        [change.path for change in draft.changes],
        plan=plan,
    )
    if not _project_has_visible_source_files(context):
        return
    unique_paths = {change.path.as_posix() for change in draft.changes}
    if len(unique_paths) > REPAIR_PATCH_MAX_CHANGES_WHEN_SOURCE_EXISTS:
        raise ValueError(
            "repair patch is too broad for an existing source workspace: "
            f"{len(unique_paths)} files changed, maximum is "
            f"{REPAIR_PATCH_MAX_CHANGES_WHEN_SOURCE_EXISTS}."
        )


def _validate_repair_test_targets(paths: list[Path], *, plan: RepairPlan) -> None:
    for path in paths:
        normalized = _safe_generated_target(path)
        if normalized is None:
            continue
        if not _is_test_artifact_target(normalized):
            continue
        if not _repair_plan_allows_test_modification(plan):
            raise ValueError(
                "repair may modify visible tests only when the approved plan sets "
                f"test_repair_allowed=true: {normalized}"
            )
        if not _is_allowed_repair_test_target(normalized):
            raise ValueError(
                "repair test modification is limited to ordinary visible tests/*.py "
                f"files, not test infrastructure: {normalized}"
            )


def _repair_plan_allows_test_modification(plan: RepairPlan) -> bool:
    return bool(
        plan.test_repair_allowed
        and plan.failure_origin in {"generated_test_code", "mixed", "test_harness"}
        and (plan.test_repair_rationale or "").strip()
    )


def _repair_patch_target_guidance(plan: RepairPlan) -> str:
    if _repair_plan_allows_test_modification(plan):
        return (
            "Generate a minimal failure-driven repair patch. Product/source files remain "
            "the default target. This approved repair plan permits visible generated test "
            "repair only for ordinary tests/*.py or test_*.py files when the change fixes "
            "a test-code defect supported by debugging evidence, such as syntax/import/cwd/"
            "subprocess encoding/fixture/null-vs-None. Never modify hidden benchmark "
            "materials, oracle_tests, evaluation, expected_result.json, conftest.py, "
            "pytest configuration, delete tests, add skip/xfail, remove tests, or weaken "
            "assertions."
        )
    return (
        "Generate repair code for product/source files only. Do not create, modify, "
        "delete, or reference tests, tests/, test_*.py, *_test.py, conftest.py, pytest "
        "configuration, or hidden benchmark materials."
    )


def _count_test_functions(text: str) -> int:
    return len(
        re.findall(
            r"^\s*(?:async\s+)?def\s+test_[A-Za-z0-9_]*\s*\(",
            text,
            flags=re.MULTILINE,
        )
    )


def _project_has_visible_source_files(context: RunContext) -> bool:
    project_root = context.task_config.project_path.resolve()
    hidden_paths = [
        path.resolve() for path in context.task_config.agent_visibility.hidden_paths
    ]
    visible_roots = [
        path.resolve() for path in context.task_config.agent_visibility.visible_paths
    ]
    for path in _iter_text_files(project_root):
        if not _is_visible_file(
            path,
            visible_roots=visible_roots,
            hidden_paths=hidden_paths,
            context_roots=visible_roots or [project_root],
        ):
            continue
        try:
            relative = path.resolve().relative_to(project_root).as_posix()
        except ValueError:
            continue
        if _is_test_artifact_target(relative):
            continue
        return True
    return False


def _generated_plan_target_paths(plan: BaseModel) -> list[Path]:
    if isinstance(plan, DebuggingAnalysis):
        return [candidate.path for candidate in plan.candidates]
    if isinstance(plan, ImplementationPlan):
        return [change.path for change in plan.changes]
    if isinstance(plan, ImplementationPatchDraft):
        return [change.path for change in plan.changes] + list(plan.syntax_check_targets)
    if isinstance(plan, RepairPlan):
        return [change.path for change in plan.changes]
    if isinstance(plan, RepairPatchDraft):
        return [change.path for change in plan.changes]
    if isinstance(plan, TestingPlan):
        return [change.path for change in plan.changes]
    if isinstance(plan, TestingPatchDraft):
        return [change.path for change in plan.changes]
    return []


def _validate_patch_file_context_decision(
    decision: PatchFileContextDecision,
    context: RunContext,
) -> None:
    for read_request in decision.read_requests:
        read_path = _safe_generated_target(read_request.path)
        if read_path is None:
            raise ValueError(f"read request outside project root: {read_request.path}")
        _validate_visible_generated_path(
            read_path,
            context,
            description="read request",
        )


def _validate_visible_generated_path(
    normalized: str,
    context: RunContext,
    *,
    description: str,
) -> None:
    root = context.task_config.project_path.resolve()
    hidden_roots = [
        path.resolve() for path in context.task_config.agent_visibility.hidden_paths
    ]
    target = (root / normalized).resolve()
    if not _is_relative_to(target, root):
        raise ValueError(f"{description} outside project root: {normalized}")
    if _is_hidden_benchmark_target(normalized) or any(
        _is_relative_to(target, hidden) for hidden in hidden_roots
    ):
        raise ValueError(f"{description} targets hidden benchmark material: {normalized}")
    if SensitiveFilter(
        root,
        visible_roots=[root],
        hidden_roots=hidden_roots,
    ).is_denied(target):
        raise ValueError(f"{description} targets sensitive or generated path: {normalized}")


def _validate_single_file_patch_draft(draft: BaseModel, target_path: Path) -> None:
    changes = getattr(draft, "changes", None)
    if not isinstance(changes, list) or len(changes) != 1:
        raise ValueError("single-file patch draft must contain exactly one change")
    actual_path = Path(changes[0].path).as_posix()
    expected_path = Path(target_path).as_posix()
    if actual_path != expected_path:
        raise ValueError(
            f"single-file patch target mismatch: expected {expected_path}, got {actual_path}"
        )
    if isinstance(draft, ImplementationPatchDraft):
        extra_targets = [
            target.as_posix()
            for target in draft.syntax_check_targets
            if target.as_posix() != expected_path
        ]
        if extra_targets:
            raise ValueError(
                "single-file implementation syntax_check_targets may only include "
                f"the target file: {', '.join(extra_targets)}"
            )


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


def _is_allowed_test_target(path: str) -> bool:
    posix_path = PurePosixPath(path.replace("\\", "/"))
    return "tests" in posix_path.parts or posix_path.name.startswith("test_")


def _is_allowed_repair_test_target(path: str) -> bool:
    posix_path = PurePosixPath(path.replace("\\", "/"))
    parts = tuple(part.lower() for part in posix_path.parts)
    name = posix_path.name.lower()
    ordinary_name = name.startswith("test_") or name.endswith("_test.py")
    return (
        name.endswith(".py")
        and name != "conftest.py"
        and not any(part in {"oracle_tests", "evaluation"} for part in parts)
        and name != "expected_result.json"
        and (
            (len(parts) >= 2 and parts[0] == "tests")
            or (len(parts) == 1 and ordinary_name)
        )
    )


def _is_test_artifact_target(path: str) -> bool:
    posix_path = PurePosixPath(path.replace("\\", "/"))
    name = posix_path.name.lower()
    parts = {part.lower() for part in posix_path.parts}
    return (
        "tests" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name in {"conftest.py", "pytest.ini"}
    )


def _normalize_testing_command(command: str, project_root: Path) -> str:
    normalized = command.strip()
    prefixes = _wrapper_prefixes(project_root)
    for prefix in prefixes:
        escaped = re.escape(prefix)
        normalized = re.sub(
            rf"^\s*cd\s+['\"]?{escaped}['\"]?\s*(?:&&|;)\s*",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
    for prefix in prefixes:
        if fs.exists(project_root / prefix):
            continue
        escaped = re.escape(prefix)
        normalized = re.sub(
            rf"(?<![A-Za-z0-9_.-]){escaped}[\\/]",
            "",
            normalized,
        )
    return normalized.strip()


def _wrapper_prefixes(project_root: Path) -> set[str]:
    return {
        "project",
        "workspace",
        project_root.resolve().name,
    }


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
    if include_failure_logs:
        budget = _append_text_section(
            sections,
            label="project_tree",
            text=_visible_project_tree_for_prompt(context),
            budget=budget,
        )
        evidence_budget = min(
            budget,
            max(1, min(30_000, max_context_chars * 35 // 100)),
        )
        evidence_sections: list[str] = []
        evidence_remaining = _append_failure_evidence_sections(
            context,
            evidence_sections,
            budget=evidence_budget,
        )
        sections.extend(evidence_sections)
        budget -= evidence_budget - evidence_remaining
        project_budget = min(
            budget,
            max(1, min(40_000, max_context_chars * 45 // 100)),
        )
        project_sections: list[str] = []
        project_remaining = _append_project_file_sections(
            project_sections,
            project_root=project_root,
            visible_roots=visible_roots,
            hidden_paths=hidden_paths,
            budget=project_budget,
        )
        sections.extend(project_sections)
        budget -= project_budget - project_remaining
        budget = _append_input_material_sections(
            context,
            sections,
            visible_roots=visible_roots,
            hidden_paths=hidden_paths,
            budget=budget,
        )
        return "\n\n".join(sections) if sections else "(no visible context files found)"

    budget = _append_input_material_sections(
        context,
        sections,
        visible_roots=visible_roots,
        hidden_paths=hidden_paths,
        budget=budget,
    )
    if budget <= 0:
        return "\n\n".join(sections)
    _append_project_file_sections(
        sections,
        project_root=project_root,
        visible_roots=visible_roots,
        hidden_paths=hidden_paths,
        budget=budget,
    )
    return "\n\n".join(sections) if sections else "(no visible context files found)"


def _append_input_material_sections(
    context: RunContext,
    sections: list[str],
    *,
    visible_roots: list[Path],
    hidden_paths: list[Path],
    budget: int,
) -> int:
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
                return budget
    return budget


def _append_project_file_sections(
    sections: list[str],
    *,
    project_root: Path,
    visible_roots: list[Path],
    hidden_paths: list[Path],
    budget: int,
) -> int:
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
            return budget
    return budget


def _append_failure_evidence_sections(
    context: RunContext,
    sections: list[str],
    *,
    budget: int,
) -> int:
    for artifact_path in _stage_evidence_paths(context):
        budget = _append_file_section(
            sections,
            label=f"stage_artifact/{artifact_path.name}",
            path=artifact_path,
            budget=budget,
        )
        if budget <= 0:
            return budget
    for log_path in _failure_log_paths(context):
        budget = _append_file_section(
            sections,
            label=f"failure_log/{log_path.name}",
            path=log_path,
            budget=budget,
        )
        if budget <= 0:
            return budget
    return budget


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
    if budget <= 0:
        return budget
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


def _append_text_section(
    sections: list[str],
    *,
    label: str,
    text: str,
    budget: int,
) -> int:
    if budget <= 0:
        return budget
    if len(text) > budget:
        text = text[:budget] + "\n[truncated]\n"
    sections.append(f"### {label}\n{text}")
    return budget - len(text)


def _visible_project_tree_for_prompt(context: RunContext, *, max_entries: int = 600) -> str:
    root = context.task_config.project_path.resolve()
    hidden_roots = [
        path.resolve() for path in context.task_config.agent_visibility.hidden_paths
    ]
    sensitive_filter = SensitiveFilter(
        root,
        visible_roots=[root],
        hidden_roots=hidden_roots,
    )
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        if len(lines) >= max_entries:
            lines.append(f"... truncated after {max_entries} visible entries")
            break
        try:
            if sensitive_filter.is_denied(path):
                continue
            relative = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if not relative:
            continue
        if fs.is_dir(path):
            lines.append(f"[dir]  {relative}/")
        elif fs.is_file(path):
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            lines.append(f"[file] {relative} ({size} bytes)")
    return "\n".join(lines) if lines else "(no visible project files)"


def _failure_log_paths(context: RunContext) -> list[Path]:
    repair_logs_dir = context.stage_dirs[Stage.REPAIR] / "logs"
    repair_candidates = [
        repair_logs_dir / "repair_regression_command.stdout.log",
        repair_logs_dir / "repair_regression_command.stderr.log",
    ]
    if fs.exists(repair_logs_dir):
        repair_candidates.extend(sorted(repair_logs_dir.glob("*.stdout.log")))
        repair_candidates.extend(sorted(repair_logs_dir.glob("*.stderr.log")))
    logs_dir = context.stage_dirs[Stage.TEST] / "logs"
    candidates = [
        *repair_candidates,
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


def _stage_evidence_paths(context: RunContext) -> list[Path]:
    candidates = [
        context.stage_dirs[Stage.REPAIR] / "repair_test_result.json",
        context.stage_dirs[Stage.REPAIR] / "after_test.log",
        context.stage_dirs[Stage.REPAIR] / "repair_report.md",
        context.stage_dirs[Stage.REPAIR] / "stage_result.json",
        context.stage_dirs[Stage.REPAIR] / "changed_files.json",
        context.stage_dirs[Stage.DEBUG] / "failure_summary.md",
        context.stage_dirs[Stage.DEBUG] / "llm_debug_analysis.json",
        context.stage_dirs[Stage.DEBUG] / "llm_debug_analysis.md",
        context.stage_dirs[Stage.DEBUG] / "fault_localization.json",
        context.stage_dirs[Stage.DEBUG] / "root_cause.md",
        context.stage_dirs[Stage.DEBUG] / "repair_plan.md",
        context.stage_dirs[Stage.DEBUG] / "debug_report.md",
        context.stage_dirs[Stage.TEST] / "test_result.json",
        context.stage_dirs[Stage.TEST] / "test_command.json",
        context.stage_dirs[Stage.TEST] / "test_report.md",
        context.stage_dirs[Stage.TEST] / "test_report.json",
        context.stage_dirs[Stage.TEST] / "stage_result.json",
    ]
    return [path for path in candidates if fs.exists(path) and fs.is_file(path)]


def _latest_testing_command(context: RunContext) -> str | None:
    command_path = context.stage_dirs[Stage.TEST] / "test_command.json"
    if not fs.exists(command_path):
        return None
    try:
        data = json.loads(fs.read_text(command_path))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    command = data.get("command")
    return str(command).strip() if command else None


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
    retryable: bool | None = None,
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
    if retryable is not None:
        record["retryable"] = retryable
    audit["attempts"].append(record)


def _write_attempt_audit(
    context: RunContext,
    schema: type[BaseModel],
    audit: dict[str, Any],
    *,
    stage_override: Stage | None = None,
    filename_override: str | None = None,
) -> None:
    if stage_override is not None:
        stage = stage_override
    elif issubclass(schema, DebuggingAnalysis):
        stage = Stage.DEBUG
    elif issubclass(schema, (RepairPlan, RepairPatchDraft)):
        stage = Stage.REPAIR
    elif issubclass(schema, (TestingPlan, TestingPatchDraft)):
        stage = Stage.TEST
    else:
        stage = Stage.IMPLEMENT
    filename = filename_override or (
        "patch_generation_attempts.json"
        if issubclass(
            schema,
            (ImplementationPatchDraft, TestingPatchDraft, RepairPatchDraft),
        )
        else "plan_generation_attempts.json"
    )
    stage_dir = context.stage_dirs[stage]
    _mkdir(stage_dir)
    _write_text(
        stage_dir / filename,
        json.dumps(audit, ensure_ascii=False, indent=2),
    )


def _start_llm_call_bundle(
    context: RunContext,
    *,
    schema: type[BaseModel],
    generation_kind: str,
    stage_override: Stage | None,
    audit_filename: str | None,
    model_name: str,
    prompt: str,
    max_attempts: int,
) -> dict[str, Any]:
    stage = _stage_for_schema_or_override(schema, stage_override)
    calls_dir = context.stage_dirs[stage] / "llm_calls"
    _mkdir(calls_dir)
    call_slug = _text_slug(
        "__".join(
            part
            for part in (
                generation_kind,
                schema.__name__,
                Path(audit_filename).stem if audit_filename else None,
            )
            if part
        )
    )
    call_id, call_dir = _next_call_dir(calls_dir, call_slug)
    bundle: dict[str, Any] = {
        "context": context,
        "call_id": call_id,
        "call_dir": call_dir,
        "relative_call_dir": _run_relative_path(context, call_dir),
        "stage": _stage_name_for_override(schema, stage_override),
        "schema": schema.__name__,
        "generation_kind": generation_kind,
        "model": model_name,
        "max_attempts": max_attempts,
        "attempts": [],
    }
    request = {
        "call_id": call_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": bundle["stage"],
        "schema": schema.__name__,
        "generation_kind": generation_kind,
        "model": model_name,
        "max_attempts": max_attempts,
        "audit_filename": audit_filename,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_chars": len(prompt),
    }
    _write_json(call_dir / "request.json", request)
    _write_prompt_artifacts(
        context,
        call_dir=call_dir,
        prompt=prompt,
        manifest_name="prompt.manifest.json",
        prompt_name="prompt.full.txt",
        retry=False,
        attempt=None,
    )
    _write_llm_call_summary(bundle)
    return bundle


def _write_llm_attempt_prompt(
    bundle: dict[str, Any],
    *,
    attempt: int,
    prompt: str,
    retry: bool,
) -> dict[str, str]:
    context: RunContext = bundle["context"]
    attempt_dir = _llm_attempt_dir(bundle, attempt)
    artifacts = _write_prompt_artifacts(
        context,
        call_dir=attempt_dir,
        prompt=prompt,
        manifest_name="prompt.manifest.json",
        prompt_name="prompt.full.txt",
        retry=retry,
        attempt=attempt,
    )
    attempt_record = _llm_attempt_record(bundle, attempt)
    attempt_record.update(
        {
            "attempt": attempt,
            "retry": retry,
            "prompt_path": artifacts["prompt_path"],
            "prompt_manifest_path": artifacts["manifest_path"],
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "prompt_chars": len(prompt),
        }
    )
    _write_llm_call_summary(bundle)
    return artifacts


def _write_llm_attempt_response(
    bundle: dict[str, Any],
    *,
    attempt: int,
    response_text: str,
) -> str:
    context: RunContext = bundle["context"]
    path = _llm_attempt_dir(bundle, attempt) / "response.raw.txt"
    _write_text(path, _redact(response_text))
    relative_path = _run_relative_path(context, path)
    attempt_record = _llm_attempt_record(bundle, attempt)
    attempt_record.update(
        {
            "response_path": relative_path,
            "response_sha256": hashlib.sha256(response_text.encode("utf-8")).hexdigest(),
            "response_chars": len(response_text),
        }
    )
    _write_llm_call_summary(bundle)
    return relative_path


def _write_llm_attempt_output(
    bundle: dict[str, Any],
    *,
    attempt: int,
    output: dict[str, Any],
) -> str:
    context: RunContext = bundle["context"]
    path = _llm_attempt_dir(bundle, attempt) / "response.parsed.json"
    _write_json(path, output)
    relative_path = _run_relative_path(context, path)
    attempt_record = _llm_attempt_record(bundle, attempt)
    attempt_record["output_path"] = relative_path
    _write_llm_call_summary(bundle)
    return relative_path


def _write_llm_attempt_validation(
    bundle: dict[str, Any],
    *,
    attempt: int,
    status: str,
    error: Exception | None = None,
    response_text: str | None = None,
    output_path: str | None = None,
    retryable: bool | None = None,
) -> str:
    context: RunContext = bundle["context"]
    payload: dict[str, Any] = {
        "attempt": attempt,
        "status": status,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }
    if response_text is not None:
        payload["response_sha256"] = hashlib.sha256(response_text.encode("utf-8")).hexdigest()
        payload["response_chars"] = len(response_text)
        payload["response_preview"] = _truncate(_redact(response_text), limit=1_000)
    if output_path is not None:
        payload["output_path"] = output_path
    if error is not None:
        payload["error_type"] = type(error).__name__
        payload["error_message"] = _truncate(_redact(str(error)), limit=4_000)
    if retryable is not None:
        payload["retryable"] = retryable
    path = _llm_attempt_dir(bundle, attempt) / "validation.json"
    _write_json(path, payload)
    relative_path = _run_relative_path(context, path)
    attempt_record = _llm_attempt_record(bundle, attempt)
    attempt_record.update(
        {
            "status": status,
            "validation_path": relative_path,
        }
    )
    if retryable is not None:
        attempt_record["retryable"] = retryable
    _write_llm_call_summary(bundle)
    return relative_path


def _write_prompt_artifacts(
    context: RunContext,
    *,
    call_dir: Path,
    prompt: str,
    manifest_name: str,
    prompt_name: str,
    retry: bool,
    attempt: int | None,
) -> dict[str, str]:
    prompt_path = call_dir / prompt_name
    manifest_path = call_dir / manifest_name
    _write_text(prompt_path, _redact(prompt))
    _write_json(
        manifest_path,
        {
            "attempt": attempt,
            "retry": retry,
            "prompt_path": _run_relative_path(context, prompt_path),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "prompt_chars": len(prompt),
            "prompt_lines": prompt.count("\n") + 1 if prompt else 0,
            "prompt_preview": _truncate(_redact(prompt), limit=1_000),
        },
    )
    return {
        "prompt_path": _run_relative_path(context, prompt_path),
        "manifest_path": _run_relative_path(context, manifest_path),
    }


def _write_llm_call_summary(bundle: dict[str, Any]) -> None:
    call_dir: Path = bundle["call_dir"]
    lines = [
        "# LLM Call Bundle",
        "",
        f"- call_id: `{bundle['call_id']}`",
        f"- stage: `{bundle['stage']}`",
        f"- generation_kind: `{bundle['generation_kind']}`",
        f"- schema: `{bundle['schema']}`",
        f"- model: `{bundle['model']}`",
        f"- max_attempts: `{bundle['max_attempts']}`",
        "",
        "## Attempts",
        "",
    ]
    attempts = sorted(bundle["attempts"], key=lambda item: item.get("attempt", 0))
    if not attempts:
        lines.append("- No attempts recorded yet.")
    for record in attempts:
        status = record.get("status", "pending")
        retry = "retry" if record.get("retry") else "initial"
        lines.append(f"- Attempt {record.get('attempt')}: `{status}` ({retry})")
        for key in (
            "prompt_path",
            "response_path",
            "output_path",
            "validation_path",
        ):
            if record.get(key):
                lines.append(f"  - {key}: `{record[key]}`")
        if record.get("prompt_chars") is not None:
            lines.append(f"  - prompt_chars: `{record['prompt_chars']}`")
        if record.get("response_chars") is not None:
            lines.append(f"  - response_chars: `{record['response_chars']}`")
    _write_text(call_dir / "call_summary.md", "\n".join(lines) + "\n")


def _summarize_structured_output(value: BaseModel) -> dict[str, Any]:
    output = value.model_dump(mode="json")
    summary: dict[str, Any] = {}
    for key in (
        "requirements_summary",
        "implementation_strategy",
        "target_summary",
        "strategy",
        "repair_strategy",
        "root_cause",
        "failure_origin",
        "command",
        "verification_command",
        "recommended_verification_command",
        "framework",
    ):
        if key in output and output[key]:
            summary[key] = _truncate(str(output[key]), limit=500)
    if "test_repair_allowed" in output:
        summary["test_repair_allowed"] = bool(output["test_repair_allowed"])
    changes = output.get("changes")
    if isinstance(changes, list):
        summary["change_count"] = len(changes)
        summary["change_paths"] = [
            str(change.get("path"))
            for change in changes
            if isinstance(change, dict) and change.get("path")
        ][:20]
    return summary


def _stage_for_schema_or_override(
    schema: type[BaseModel],
    stage_override: Stage | None,
) -> Stage:
    if stage_override is not None:
        return stage_override
    if issubclass(schema, DebuggingAnalysis):
        return Stage.DEBUG
    if issubclass(schema, (RepairPlan, RepairPatchDraft)):
        return Stage.REPAIR
    if issubclass(schema, (TestingPlan, TestingPatchDraft)):
        return Stage.TEST
    return Stage.IMPLEMENT


def _next_call_dir(root: Path, slug: str) -> tuple[str, Path]:
    for index in range(1, 10_000):
        call_id = f"{index:03d}_{slug}"
        call_dir = root / call_id
        if not fs.exists(call_dir):
            _mkdir(call_dir)
            return call_id, call_dir
    raise RuntimeError(f"Unable to create LLM call bundle under {root}")


def _llm_attempt_dir(bundle: dict[str, Any], attempt: int) -> Path:
    attempt_dir = bundle["call_dir"] / f"attempt_{attempt:02d}"
    _mkdir(attempt_dir)
    return attempt_dir


def _llm_attempt_record(bundle: dict[str, Any], attempt: int) -> dict[str, Any]:
    for record in bundle["attempts"]:
        if record.get("attempt") == attempt:
            return record
    record: dict[str, Any] = {"attempt": attempt}
    bundle["attempts"].append(record)
    return record


def _run_relative_path(context: RunContext, path: Path) -> str:
    try:
        return path.relative_to(context.run_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _text_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")
    return slug[:120] or "llm_call"


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


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
    user_auto = context.task_config.permissions.approval_mode == "auto"
    auto = benchmark_auto or user_auto
    decision_source = (
        "benchmark_auto"
        if benchmark_auto
        else "user_configured_auto"
        if user_auto
        else "system_default"
    )
    return ApprovalDecision(
        interrupt_id=interrupt_id,
        decision_type="approve",
        comment=comment,
        decided_by="benchmark" if benchmark_auto else "config" if user_auto else "workflow",
        auto=auto,
        decision_source=decision_source,
        presented_to_user=False,
    )


def _stage_name_for_schema(schema: type[BaseModel]) -> str:
    if issubclass(schema, DebuggingAnalysis):
        return "debugging"
    if issubclass(schema, (TestingPlan, TestingPatchDraft)):
        return "testing"
    if issubclass(schema, (RepairPlan, RepairPatchDraft)):
        return "repair"
    return "implementation"


def _stage_name_for_override(
    schema: type[BaseModel],
    stage_override: Stage | None,
) -> str:
    if stage_override == Stage.TEST:
        return "testing"
    if stage_override == Stage.REPAIR:
        return "repair"
    if stage_override == Stage.DEBUG:
        return "debugging"
    if stage_override == Stage.IMPLEMENT:
        return "implementation"
    return _stage_name_for_schema(schema)


def _stage_enum_for_name(stage: Literal["implementation", "testing", "repair"]) -> Stage:
    if stage == "testing":
        return Stage.TEST
    if stage == "repair":
        return Stage.REPAIR
    return Stage.IMPLEMENT


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
    return redact_sensitive_text(text)


def _truncate(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def _compact_retry_detail(text: str, *, limit: int = 180) -> str:
    compact = " ".join(_redact(str(text or "")).split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _path_slug(path: Path) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(path).as_posix()).strip("_")
    return slug[:120] or "target"


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
