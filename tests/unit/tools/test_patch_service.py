from __future__ import annotations

from pathlib import Path

import pytest

from codeagent.services.patch_service import (
    FileChange,
    PatchApplyError,
    PatchService,
    PatchValidationError,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _patch(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_create_validate_summarize_and_apply_modify_patch_once(tmp_path) -> None:
    project = tmp_path / "project"
    source = project / "src" / "app.py"
    _write(source, "def value():\n    return 1\n")
    service = PatchService()

    artifact = service.create_unified_diff(
        [
            FileChange(
                path=Path("src/app.py"),
                old_content="def value():\n    return 1\n",
                new_content="def value():\n    return 2\n",
            )
        ]
    )
    patch_path = _patch(tmp_path / "change.diff", artifact.text)

    validation = service.validate_patch(patch_path, project)
    summary = service.summarize_patch(patch_path)
    first_apply = service.apply_patch(patch_path, project, operation_id="op-1")
    second_apply = service.apply_patch(patch_path, project, operation_id="op-1")

    assert validation.valid is True
    assert validation.changed_files == ["src/app.py"]
    assert summary.modified_files == ["src/app.py"]
    assert summary.added_lines == 1
    assert summary.removed_lines == 1
    assert first_apply.applied is True
    assert first_apply.already_applied is False
    assert first_apply.changed_files == ["src/app.py"]
    assert source.read_text(encoding="utf-8") == "def value():\n    return 2\n"
    assert second_apply.applied is False
    assert second_apply.already_applied is True
    assert source.read_text(encoding="utf-8") == "def value():\n    return 2\n"


def test_apply_add_and_delete_patch(tmp_path) -> None:
    project = tmp_path / "project"
    obsolete = project / "src" / "old.py"
    _write(obsolete, "def old():\n    return 'old'\n")
    patch_path = _patch(
        tmp_path / "add_delete.diff",
        """--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+def new():
+    return 'new'
--- a/src/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def old():
-    return 'old'
""",
    )
    service = PatchService()

    result = service.apply_patch(patch_path, project, operation_id="op-2")

    assert result.applied is True
    assert sorted(result.changed_files) == ["src/new.py", "src/old.py"]
    assert (project / "src" / "new.py").read_text(encoding="utf-8") == (
        "def new():\n    return 'new'\n"
    )
    assert not obsolete.exists()


def test_validate_rejects_out_of_root_and_sensitive_paths(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    service = PatchService()
    traversal_patch = _patch(
        tmp_path / "traversal.diff",
        """--- /dev/null
+++ b/../outside.py
@@ -0,0 +1 @@
+x = 1
""",
    )
    sensitive_patch = _patch(
        tmp_path / "sensitive.diff",
        """--- a/.env
+++ b/.env
@@ -1 +1 @@
-TOKEN=old
+TOKEN=new
""",
    )

    traversal = service.validate_patch(traversal_patch, project)
    sensitive = service.validate_patch(sensitive_patch, project)

    assert traversal.valid is False
    assert any("outside project root" in error for error in traversal.errors)
    assert sensitive.valid is False
    assert any("sensitive" in error for error in sensitive.errors)
    with pytest.raises(PatchValidationError):
        service.apply_patch(traversal_patch, project, operation_id="op-3")


def test_validate_flags_test_deletion_skip_and_hardcoding_risks(tmp_path) -> None:
    project = tmp_path / "project"
    _write(project / "tests" / "test_app.py", "def test_old():\n    assert True\n")
    patch_path = _patch(
        tmp_path / "risky.diff",
        """--- a/tests/test_app.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def test_old():
-    assert True
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,5 @@
 def solve(value):
-    return value
+    if value == 42:
+        return "expected"
+    pytest.skip("not implemented")
+    return value
""",
    )
    service = PatchService()

    validation = service.validate_patch(patch_path, project)
    risk_kinds = {finding.kind for finding in validation.risk_report.findings}

    assert validation.valid is True
    assert validation.risk_report.level == "high"
    assert {"test_deletion", "skip_or_xfail", "hardcoded_case"} <= risk_kinds


def test_validate_flags_large_patch_as_high_risk(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    service = PatchService()
    artifact = service.create_unified_diff(
        [
            FileChange(
                path=Path(f"src/file_{index}.py"),
                old_content=None,
                new_content=f"x = {index}\n",
            )
            for index in range(11)
        ]
    )
    patch_path = _patch(tmp_path / "large.diff", artifact.text)

    validation = service.validate_patch(patch_path, project)
    risk_kinds = {finding.kind for finding in validation.risk_report.findings}

    assert validation.valid is True
    assert validation.risk_report.level == "high"
    assert "large_patch" in risk_kinds


def test_validate_rejects_hunk_header_count_mismatch(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    patch_path = _patch(
        tmp_path / "bad_hunk_count.diff",
        """--- /dev/null
+++ b/src/app.py
@@ -0,0 +10,99 @@
+x = 1
""",
    )
    service = PatchService()

    validation = service.validate_patch(patch_path, project)

    assert validation.valid is False
    assert any("hunk line count mismatch" in error for error in validation.errors)


def test_apply_raises_when_patch_context_does_not_match(tmp_path) -> None:
    project = tmp_path / "project"
    _write(project / "src" / "app.py", "def value():\n    return 3\n")
    patch_path = _patch(
        tmp_path / "mismatch.diff",
        """--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def value():
-    return 1
+    return 2
""",
    )
    service = PatchService()

    with pytest.raises(PatchApplyError, match="context mismatch"):
        service.apply_patch(patch_path, project, operation_id="op-4")


def test_apply_preserves_utf8_bom_and_crlf_when_context_matches(tmp_path) -> None:
    project = tmp_path / "project"
    source = project / "calc.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(
        b"\xef\xbb\xbfdef add(left, right):\r\n    return left - right\r\n"
    )
    patch_path = _patch(
        tmp_path / "crlf.diff",
        """--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(left, right):
-    return left - right
+    return left + right
""",
    )
    service = PatchService()

    result = service.apply_patch(patch_path, project, operation_id="op-crlf")

    assert result.applied is True
    content = source.read_bytes()
    assert content.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in content
    assert b"return left + right" in content


def test_apply_rejects_delete_patch_that_leaves_remaining_lines(tmp_path) -> None:
    project = tmp_path / "project"
    _write(project / "src" / "app.py", "line 1\nline 2\n")
    patch_path = _patch(
        tmp_path / "partial_delete.diff",
        """--- a/src/app.py
+++ /dev/null
@@ -1 +0,0 @@
-line 1
""",
    )
    service = PatchService()

    with pytest.raises(PatchApplyError, match="delete patch does not remove entire file"):
        service.apply_patch(patch_path, project, operation_id="op-5")
    assert (project / "src" / "app.py").exists()


def test_apply_preflights_all_files_before_modifying_workspace(tmp_path) -> None:
    project = tmp_path / "project"
    _write(project / "app.py", "x = 1\n")
    _write(project / "src", "not a directory\n")
    patch_path = _patch(
        tmp_path / "partial_apply.diff",
        """--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-x = 1
+x = 2
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1 @@
+y = 1
""",
    )
    service = PatchService()

    with pytest.raises(PatchApplyError, match="parent path is not a directory"):
        service.apply_patch(patch_path, project, operation_id="op-6")

    assert (project / "app.py").read_text(encoding="utf-8") == "x = 1\n"
    assert (project / "src").read_text(encoding="utf-8") == "not a directory\n"


def test_validate_rejects_duplicate_file_patches(tmp_path) -> None:
    project = tmp_path / "project"
    _write(project / "src" / "app.py", "x = 1\n")
    patch_path = _patch(
        tmp_path / "duplicate.diff",
        """--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-x = 1
+x = 2
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-x = 1
+x = 3
""",
    )
    service = PatchService()

    validation = service.validate_patch(patch_path, project)

    assert validation.valid is False
    assert any("duplicate patch target" in error for error in validation.errors)
    with pytest.raises(PatchValidationError):
        service.apply_patch(patch_path, project, operation_id="op-7")


def test_validate_flags_test_assertion_removal_risk(tmp_path) -> None:
    project = tmp_path / "project"
    _write(project / "tests" / "test_app.py", "def test_value():\n    assert value() == 1\n")
    patch_path = _patch(
        tmp_path / "remove_assertion.diff",
        """--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -1,2 +1 @@
 def test_value():
-    assert value() == 1
""",
    )
    service = PatchService()

    validation = service.validate_patch(patch_path, project)
    risk_kinds = {finding.kind for finding in validation.risk_report.findings}

    assert validation.valid is True
    assert validation.risk_report.level == "high"
    assert "test_assertion_removal" in risk_kinds
