from __future__ import annotations

import pytest

from codeagent.context.sensitive_filter import SensitiveFilter


def test_sensitive_filter_skips_secret_and_generated_paths(tmp_path) -> None:
    root = tmp_path
    sensitive = SensitiveFilter(root)

    assert sensitive.is_denied(root / ".env")
    assert sensitive.is_denied(root / "private.key")
    assert sensitive.is_denied(root / "server.crt")
    assert sensitive.is_denied(root / "client.cer")
    assert sensitive.is_denied(root / ".git" / "config")
    assert sensitive.is_denied(root / ".venv" / "pyvenv.cfg")
    assert sensitive.is_denied(root / "build" / "out.py")
    assert not sensitive.is_denied(root / "src" / "app.py")


def test_sensitive_filter_enforces_visible_paths(tmp_path) -> None:
    visible = tmp_path / "input"
    hidden = tmp_path / "evaluation"
    visible.mkdir()
    hidden.mkdir()
    sensitive = SensitiveFilter(tmp_path, visible_roots=[visible])

    assert not sensitive.is_denied(visible / "requirements.md")
    assert sensitive.is_denied(hidden / "test_secret.py")


def test_sensitive_filter_rejects_path_traversal(tmp_path) -> None:
    sensitive = SensitiveFilter(tmp_path)

    with pytest.raises(ValueError, match="outside allowed root"):
        sensitive.ensure_allowed(tmp_path.parent / "outside.txt")
