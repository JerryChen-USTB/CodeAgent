from __future__ import annotations

from pathlib import Path

import pytest

from codeagent.context.code_search import CodeSearcher
from codeagent.context.file_reader import FileReader
from codeagent.context.scanner import ProjectScanner
from codeagent.context.sensitive_filter import SensitiveFilter


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_project_scanner_identifies_python_project_and_skips_sensitive(tmp_path) -> None:
    _write(tmp_path / "src" / "app.py", "def run():\n    return 'ok'\n")
    _write(tmp_path / "tests" / "test_app.py", "def test_run():\n    assert True\n")
    _write(tmp_path / "pyproject.toml", "[project]\nname='demo'\n")
    _write(tmp_path / "requirements.txt", "pytest\n")
    _write(tmp_path / ".env", "SECRET=should-not-read\n")
    _write(tmp_path / ".git" / "config", "git metadata\n")
    _write(tmp_path / ".venv" / "lib.py", "generated\n")
    _write(tmp_path / "build" / "out.py", "generated\n")

    profile = ProjectScanner().scan(tmp_path)

    assert profile.root == tmp_path
    assert tmp_path / "src" / "app.py" in profile.source_files
    assert tmp_path / "tests" / "test_app.py" in profile.test_files
    assert tmp_path / "pyproject.toml" in profile.config_files
    assert tmp_path / "requirements.txt" in profile.dependency_files
    assert all(".env" not in str(path) for path in profile.source_files)
    assert any(".env" in skipped.path.as_posix() for skipped in profile.skipped_paths)
    assert any(".git" in skipped.path.as_posix() for skipped in profile.skipped_paths)


def test_file_reader_reads_small_file_and_truncates_large_file(tmp_path) -> None:
    _write(tmp_path / "src" / "small.py", "x = 1\n")
    _write(tmp_path / "src" / "large.py", "x" * 50)
    reader = FileReader(SensitiveFilter(tmp_path), max_chars=10)

    small = reader.read_text(tmp_path / "src" / "small.py")
    large = reader.read_text(tmp_path / "src" / "large.py")

    assert small.truncated is False
    assert small.content == "x = 1\n"
    assert large.truncated is True
    assert large.content == "x" * 10
    assert large.original_chars == 50


def test_file_reader_denies_secret_without_reading_content(tmp_path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("DO_NOT_READ=1", encoding="utf-8")
    reader = FileReader(SensitiveFilter(tmp_path))

    with pytest.raises(PermissionError, match="denied"):
        reader.read_text(secret)


def test_code_searcher_returns_matches_and_skips_denied_files(tmp_path) -> None:
    _write(tmp_path / "src" / "app.py", "def target_function():\n    return 1\n")
    _write(tmp_path / "src" / "other.py", "def other():\n    return 2\n")
    _write(tmp_path / ".env", "target_function=secret\n")
    searcher = CodeSearcher(SensitiveFilter(tmp_path))

    results = searcher.search(tmp_path, "target_function")

    assert len(results) == 1
    assert results[0].path == tmp_path / "src" / "app.py"
    assert results[0].line_number == 1
    assert "secret" not in results[0].line_text


def test_code_searcher_keeps_matches_when_sibling_directory_is_inaccessible(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "src" / "app.py"
    inaccessible = tmp_path / "locked"
    _write(target, "def target_function():\n    return 1\n")
    _write(inaccessible / "ignored.py", "def target_function():\n    return 'hidden'\n")
    original_iterdir = Path.iterdir

    def flaky_iterdir(self):
        if self.resolve() == inaccessible.resolve():
            raise OSError("simulated inaccessible directory")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", flaky_iterdir)
    searcher = CodeSearcher(SensitiveFilter(tmp_path))

    results = searcher.search(tmp_path, "target_function")

    assert [result.path for result in results] == [target]


def test_code_searcher_does_not_descend_denied_directories(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "src" / "app.py"
    denied_dir = tmp_path / "codeagent_runs"
    _write(target, "def target_function():\n    return 1\n")
    _write(denied_dir / "run.py", "def target_function():\n    return 'denied'\n")
    original_iterdir = Path.iterdir

    def fail_if_denied_dir_is_listed(self):
        if self.resolve() == denied_dir.resolve():
            raise AssertionError("denied directory should not be listed")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fail_if_denied_dir_is_listed)
    searcher = CodeSearcher(SensitiveFilter(tmp_path))

    results = searcher.search(tmp_path, "target_function")

    assert [result.path for result in results] == [target]


def test_project_scanner_keeps_files_when_sibling_directory_is_inaccessible(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "src" / "app.py"
    inaccessible = tmp_path / "locked"
    _write(source, "def run():\n    return 'ok'\n")
    _write(inaccessible / "ignored.py", "def ignored():\n    return 'hidden'\n")
    original_iterdir = Path.iterdir

    def flaky_iterdir(self):
        if self.resolve() == inaccessible.resolve():
            raise OSError("simulated inaccessible directory")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", flaky_iterdir)

    profile = ProjectScanner().scan(tmp_path)

    assert source in profile.source_files


def test_project_scanner_does_not_descend_denied_directories(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "src" / "app.py"
    denied_dir = tmp_path / "codeagent_runs"
    _write(source, "def run():\n    return 'ok'\n")
    _write(denied_dir / "run.py", "def ignored():\n    return 'denied'\n")
    original_iterdir = Path.iterdir

    def fail_if_denied_dir_is_listed(self):
        if self.resolve() == denied_dir.resolve():
            raise AssertionError("denied directory should not be listed")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fail_if_denied_dir_is_listed)

    profile = ProjectScanner().scan(tmp_path)

    assert source in profile.source_files
    assert any(skipped.path == denied_dir for skipped in profile.skipped_paths)


def test_context_tools_enforce_benchmark_visible_allowlist(tmp_path) -> None:
    _write(tmp_path / "input" / "requirements.md", "visible target\n")
    _write(tmp_path / "workspace" / "app.py", "def visible(): pass\n")
    _write(tmp_path / "evaluation" / "test_hidden.py", "hidden target\n")
    sensitive = SensitiveFilter(
        tmp_path,
        visible_roots=[tmp_path / "input", tmp_path / "workspace"],
        hidden_roots=[tmp_path / "evaluation"],
    )
    reader = FileReader(sensitive)
    searcher = CodeSearcher(sensitive)

    assert reader.read_text(tmp_path / "input" / "requirements.md").content
    with pytest.raises(PermissionError):
        reader.read_text(tmp_path / "evaluation" / "test_hidden.py")
    assert all(
        "evaluation" not in result.path.as_posix()
        for result in searcher.search(tmp_path, "target")
    )


def test_code_searcher_does_not_list_explicitly_hidden_search_root(
    tmp_path, monkeypatch
) -> None:
    evaluation = tmp_path / "evaluation"
    _write(tmp_path / "input" / "requirements.md", "visible target\n")
    _write(evaluation / "test_hidden.py", "hidden target\n")
    sensitive = SensitiveFilter(
        tmp_path,
        visible_roots=[tmp_path / "input"],
        hidden_roots=[evaluation],
    )
    original_iterdir = Path.iterdir

    def fail_if_hidden_root_is_listed(self):
        if self.resolve() == evaluation.resolve():
            raise AssertionError("hidden search root should not be listed")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fail_if_hidden_root_is_listed)
    searcher = CodeSearcher(sensitive)

    assert searcher.search(evaluation, "target") == []
